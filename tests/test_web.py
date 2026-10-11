"""Web API tests: streaming chat, hot-reload policy, approvals, sandbox isolation, limits."""
import json

import pytest
from fastapi.testclient import TestClient

import web.app as webapp
from tests.fakes import FakeAnthropic

V1, V2 = "a" * 16, "b" * 16


@pytest.fixture
def client():
    webapp.MANAGER.reset(V1)
    webapp.MANAGER.reset(V2)
    webapp._last_bench.clear()
    return TestClient(webapp.app)


def chat(client, visitor, customer, message, mode="offline"):
    r = client.post("/api/chat/stream", headers={"X-Visitor-Id": visitor},
                    json={"customer_id": customer, "message": message, "mode": mode})
    assert r.status_code == 200, r.text
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    return events, events[-1]


def test_page_and_config(client):
    assert "Guided tour" in client.get("/").text
    cfg = client.get("/api/config").json()
    assert cfg["author"] and cfg["project"].startswith("Capstone 1")
    assert client.get("/docs").status_code == 404  # no API schema exposed
    assert "home_url" in cfg


def test_stream_emits_each_agent_step_live(client):
    events, final = chat(client, V1, "C-100", "I returned ORD-1002 two weeks ago, no refund yet")
    starts = [e["name"] for e in events if e["type"] == "span_start"]
    assert starts[:3] == ["input_guardrail", "intent_agent", "router"] and "response_agent" in starts
    assert any(e["type"] == "tool" and e["tool"] == "issue_refund" for e in events)
    assert final["type"] == "final" and final["response"]["outcome"] == "refund_issued"


def test_sandboxes_are_isolated(client):
    chat(client, V1, "C-100", "I returned ORD-1002 two weeks ago, no refund yet")
    _, final = chat(client, V2, "C-100", "I returned ORD-1002 two weeks ago, no refund yet")
    assert final["response"]["outcome"] == "refund_issued"  # V1's refund is invisible to V2


def test_policy_hot_reload_changes_the_next_decision(client):
    h = {"X-Visitor-Id": V1}
    _, before = chat(client, V1, "C-200", "I returned ORD-1012 a while ago, can I get a refund?")
    assert before["response"]["outcome"] == "refund_denied"
    r = client.post("/api/policy", headers=h, json={"approval_threshold": 200, "refund_window_days": 60}).json()
    assert r["refund_window_days"] == 60
    _, after = chat(client, V1, "C-200", "I returned ORD-1012 a while ago, can I get a refund?")
    assert after["response"]["outcome"] == "refund_issued"
    assert client.post("/api/policy", headers=h, json={"approval_threshold": 5, "refund_window_days": 60}).status_code == 422


def test_approval_flow_over_http(client):
    h = {"X-Visitor-Id": V1}
    _, final = chat(client, V1, "C-200", "Please refund ORD-1004, it was defective")
    apr = final["response"]["approval_id"]
    assert client.get("/api/approvals", headers=h).json()["approvals"][0]["status"] == "pending"
    r = client.post(f"/api/approvals/{apr}", headers=h, json={"approve": True, "approver": "Dana"}).json()
    assert r["outcome"] == "refund_issued"
    assert client.post(f"/api/approvals/{apr}", headers=h, json={"approve": True, "approver": "Dana"}).status_code == 400


def test_claude_mode_streams_model_calls(client, monkeypatch):
    monkeypatch.setattr(webapp, "CLAUDE_CLIENT", FakeAnthropic())
    events, final = chat(client, V1, "C-100", "I returned ORD-1002 and want my refund", mode="claude")
    assert final["mode"] == "claude" and any(e["type"] == "llm" for e in events)
    assert final["response"]["cost_usd"] > 0


def test_claude_daily_cap_falls_back_to_offline(client, monkeypatch):
    monkeypatch.setattr(webapp, "CLAUDE_CLIENT", FakeAnthropic())
    monkeypatch.setattr(webapp, "CLAUDE_CAP", webapp.DailyCap(0))
    _, final = chat(client, V1, "C-100", "Where is ORD-1003?", mode="claude")
    assert final["mode"] == "offline" and "budget" in final["notice"]


def test_bad_visitor_and_rate_limit(client, monkeypatch):
    r = client.post("/api/chat/stream", headers={"X-Visitor-Id": "../etc"}, json={"customer_id": "C-100", "message": "hi"})
    assert r.status_code == 400
    monkeypatch.setattr(webapp, "MSGS_PER_WINDOW", 2)
    chat(client, V2, "C-100", "hours?")
    chat(client, V2, "C-100", "hours?")
    r = client.post("/api/chat/stream", headers={"X-Visitor-Id": V2}, json={"customer_id": "C-100", "message": "hours?"})
    assert r.status_code == 429


def test_benchmark_endpoint(client):
    r = client.post("/api/benchmark/run", headers={"X-Visitor-Id": V1}).json()
    assert r["summary"]["cases"] >= 20 and r["summary"]["safety_pass_rate"] == 1.0
    assert client.post("/api/benchmark/run", headers={"X-Visitor-Id": V1}).status_code == 429


def test_circuit_breaker_opens_then_recovers(monkeypatch):
    from web.sandbox import CircuitBreaker
    clock = [1000.0]
    monkeypatch.setattr("web.sandbox.time.time", lambda: clock[0])
    br = CircuitBreaker(threshold=3, window_s=120, cooldown_s=300)
    for _ in range(3):
        br.record(False)
    assert br.state == "open" and not br.allow()
    clock[0] += 301
    assert br.state == "half-open" and br.allow()
    br.record(True)
    assert br.state == "closed"


def test_claude_outage_trips_breaker_and_site_stays_up(client, monkeypatch):
    def broken(kwargs):
        raise TimeoutError("api down")
    monkeypatch.setattr(webapp, "CLAUDE_CLIENT", FakeAnthropic(broken))
    monkeypatch.setattr(webapp, "CLAUDE_BREAKER", webapp.CircuitBreaker(threshold=2))
    for _ in range(2):
        _, final = chat(client, V1, "C-100", "Where is ORD-1003?", mode="claude")
        assert final["response"]["outcome"] == "tracking_info"   # still correct via rules
    _, final = chat(client, V1, "C-100", "Where is ORD-1003?", mode="claude")
    assert final["mode"] == "offline" and "trouble" in final["notice"]   # breaker open: instant rules


def test_crash_in_a_request_resets_only_that_sandbox(client, monkeypatch):
    chat(client, V1, "C-100", "Where is ORD-1003?")
    box = webapp.MANAGER.get(V1)
    monkeypatch.setattr(box.orchestrator, "handle", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    events, last = chat(client, V1, "C-100", "Where is ORD-1003?")
    assert last["type"] == "error" and "reset" in last["message"]
    _, final = chat(client, V1, "C-100", "Where is ORD-1003?")   # fresh sandbox works again
    assert final["response"]["outcome"] == "tracking_info"


def test_corrupt_results_file_does_not_break_page(client, monkeypatch, tmp_path):
    bad = tmp_path / "eval" / "results"
    bad.mkdir(parents=True)
    (bad / "latest_offline.json").write_text("{not json")
    monkeypatch.setattr(webapp, "ROOT", tmp_path)
    assert client.get("/api/results").status_code == 200


def test_security_headers_on_every_response(client):
    for path in ("/", "/api/config", "/static/app.js", "/nope"):
        h = client.get(path).headers
        assert "frame-ancestors 'none'" in h["content-security-policy"] and "script-src 'self'" in h["content-security-policy"]
        assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"
        assert "max-age" in h["strict-transport-security"]


def test_branded_error_page_for_browsers_json_for_api(client):
    page = client.get("/does-not-exist", headers={"Accept": "text/html"})
    assert page.status_code == 404 and "Page not found" in page.text and "{{" not in page.text
    api = client.get("/api/does-not-exist")
    assert api.status_code == 404 and api.json() == {"error": "Not found."}


def test_oversized_request_rejected(client):
    r = client.post("/api/chat/stream", headers={"X-Visitor-Id": V1, "Content-Type": "application/json"},
                    content=b"x" * (webapp.MAX_BODY_BYTES + 1))
    assert r.status_code == 413


def test_maintenance_mode(client, monkeypatch):
    monkeypatch.setattr(webapp, "MAINTENANCE", True)
    page = client.get("/", headers={"Accept": "text/html"})
    assert page.status_code == 503 and "Back shortly" in page.text
    assert client.get("/api/config").status_code == 503
    assert client.get("/healthz").status_code == 200   # Render must not restart it during maintenance

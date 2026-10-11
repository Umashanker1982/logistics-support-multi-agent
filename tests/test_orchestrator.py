import json

from tests.fakes import FakeAnthropic, default_handler, text_msg
from agents.prompts import INTENT_SYSTEM, RESPONSE_SYSTEM


def test_headline_scenario(orch):
    resp = orch.handle("I returned item ORD-1002 two weeks ago and have not received my refund.",
                       customer_id="C-100", session_id="s")
    assert resp.outcome == "refund_issued" and resp.actions[0]["type"] == "refund_issued"
    assert "ORD-1002" in resp.citations


def test_high_value_refund_waits_for_human_then_executes(orch):
    first = orch.handle("Please refund ORD-1004, it was defective.", customer_id="C-200", session_id="s")
    assert first.outcome == "approval_required" and first.approval_id
    assert "ORD-1004" not in orch.c.backend.refunds          # no money moved yet
    result = orch.approve(first.approval_id, "Dana", True)
    assert result.outcome == "refund_issued"
    assert orch.c.backend.refunds["ORD-1004"]["approval_id"] == first.approval_id


def test_rejected_approval_is_reported(orch):
    first = orch.handle("Please refund ORD-1004, it was defective.", customer_id="C-200", session_id="s")
    orch.approve(first.approval_id, "Dana", False, "no defect found")
    later = orch.handle("Any news on my refund?", customer_id="C-200", session_id="s")
    assert later.outcome == "approval_rejected" and "ORD-1004" not in orch.c.backend.refunds


def test_every_request_produces_a_complete_trace(orch):
    orch.handle("Where is my order ORD-1003?", customer_id="C-100", session_id="s")
    # in-memory tracer: inspect via a fresh request's AgentResponse fields
    resp = orch.handle("Where is my order ORD-1003?", customer_id="C-100", session_id="s")
    assert resp.trace_id.startswith("tr-") and resp.intent == "track" and resp.route == "tracking_workflow"
    assert all(e["trace_id"] for e in orch.c.audit.entries)


# ---------- Claude-mode paths (FakeAnthropic, no network) ----------

def test_claude_mode_happy_path_records_tokens_and_cost(make_claude_orch):
    orch, fake = make_claude_orch()
    resp = orch.handle("I returned ORD-1002 and want my refund", customer_id="C-100", session_id="s")
    assert resp.outcome == "refund_issued" and resp.llm_calls >= 3 and resp.cost_usd > 0
    systems = [c["system"] for c in fake.calls]
    assert INTENT_SYSTEM in systems and RESPONSE_SYSTEM in systems


def test_llm_outage_falls_back_to_rules(make_claude_orch):
    def broken(kwargs):
        raise TimeoutError("api down")
    orch, _ = make_claude_orch(FakeAnthropic(broken))
    resp = orch.handle("I returned ORD-1002 and want my refund", customer_id="C-100", session_id="s")
    assert resp.outcome == "refund_issued" and resp.cost_usd == 0


def test_hallucinated_order_id_is_discarded(make_claude_orch):
    def liar(kwargs):
        if kwargs["system"] == INTENT_SYSTEM:
            return text_msg(json.dumps({"intent": "refund", "order_id": "ORD-1004", "confidence": 0.9}))
        return default_handler(kwargs)
    orch, _ = make_claude_orch(FakeAnthropic(liar))
    resp = orch.handle("I want a refund please", customer_id="C-200", session_id="s")
    assert resp.outcome == "needs_clarification" and not resp.actions


def test_unsafe_draft_is_replaced_by_template(make_claude_orch):
    def overpromiser(kwargs):
        if kwargs["system"] == RESPONSE_SYSTEM:
            return text_msg("Great news, your refund has been issued for ORD-1001! [ORD-1006]")
        return default_handler(kwargs)
    orch, _ = make_claude_orch(FakeAnthropic(overpromiser))
    resp = orch.handle("Refund ORD-1006 please, it is defective", customer_id="C-200", session_id="s")
    assert resp.outcome == "refund_denied"
    assert "has been issued" not in resp.reply and "POLICY R-1" in resp.citations


def test_lookup_agent_cannot_be_steered_to_another_order(make_claude_orch):
    def nosy(kwargs):
        from agents.prompts import LOOKUP_SYSTEM
        from tests.fakes import tool_msg
        if kwargs["system"] == LOOKUP_SYSTEM and len(kwargs["messages"]) == 1:
            return tool_msg([("get_order", {"order_id": "ORD-1004"})])
        return default_handler(kwargs)
    orch, _ = make_claude_orch(FakeAnthropic(nosy))
    resp = orch.handle("Where is ORD-1003?", customer_id="C-100", session_id="s")
    assert resp.outcome == "tracking_info"
    assert not any(e["args"].get("order_id") == "ORD-1004" for e in orch.c.audit.entries)


def test_plain_order_id_in_claude_reply_becomes_a_citation(make_claude_orch):
    """Regression for benchmark RF-01/IN-01: Claude wrote 'order ORD-1002' without brackets."""
    def plain(kwargs):
        if kwargs["system"] == RESPONSE_SYSTEM:
            return text_msg("Your refund for order ORD-1002 has been issued under RF-6001 [POLICY R-1].")
        return default_handler(kwargs)
    orch, _ = make_claude_orch(FakeAnthropic(plain))
    resp = orch.handle("I returned ORD-1002 and want my refund", customer_id="C-100", session_id="s")
    assert resp.outcome == "refund_issued"
    assert "[ORD-1002]" in resp.reply and "ORD-1002" in resp.citations and "RF-6001" in resp.citations


def test_bracketing_never_cites_unverified_ids():
    from agents.response_agent import bracket_verified_ids
    out = bracket_verified_ids("About ORD-1002 and ORD-1004, see [ORD-1002]", {"ORD-1002"})
    assert out == "About [ORD-1002] and ORD-1004, see [ORD-1002]"


def test_missing_rule_citation_is_added_from_verified_facts(make_claude_orch):
    """Regression for benchmark IN-02: Claude explained the approval but didn't cite rule I-4."""
    def uncited(kwargs):
        if kwargs["system"] == RESPONSE_SYSTEM:
            return text_msg("Your claim for [ORD-1009] needs a claims lead to review it before payout.")
        return default_handler(kwargs)
    orch, _ = make_claude_orch(FakeAnthropic(uncited))
    resp = orch.handle("The TV in ORD-1009 has a cracked screen, please file a claim", customer_id="C-300", session_id="s")
    assert resp.outcome == "claim_approval_required"
    assert "POLICY I-4" in resp.citations and resp.approval_id in resp.citations
    assert "Sources:" in resp.reply


def test_templates_already_carry_required_citations(orch, settings, backend):
    import json as _json
    from eval.benchmark import ROOT, run_case
    cases = _json.loads((ROOT / "eval" / "golden_requests.json").read_text())
    for case in cases:
        r = run_case(case, backend, settings)
        assert "Sources:" not in r["reply"], case["id"]  # offline templates never need the fallback line

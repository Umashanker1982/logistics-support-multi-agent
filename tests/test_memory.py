import pytest

from memory.session import SessionStore


def test_session_persists_and_is_bound_to_customer(tmp_path):
    store = SessionStore(tmp_path)
    mem = store.get("abc", "C-100")
    mem.add_turn("user", "hello")
    mem.remember_order("ORD-1002")
    store.save(mem)

    reloaded = SessionStore(tmp_path).get("abc", "C-100")
    assert reloaded.get_fact("current_order_id") == "ORD-1002" and reloaded.history[0]["content"] == "hello"
    with pytest.raises(PermissionError):
        SessionStore(tmp_path).get("abc", "C-200")


def test_session_id_is_sanitized(tmp_path):
    with pytest.raises(ValueError):
        SessionStore(tmp_path).get("../../etc/passwd", "C-100")


def test_earlier_turn_changes_later_decision(orch):
    first = orch.handle("Where is my order ORD-1007?", customer_id="C-300", session_id="m1")
    assert first.outcome == "tracking_info"
    second = orch.handle("It arrived smashed. Can I claim on the insurance?", customer_id="C-300", session_id="m1")
    assert second.order_id == "ORD-1007" and second.outcome == "claim_filed"


def test_no_memory_means_clarification(orch):
    resp = orch.handle("It arrived smashed. Can I claim on the insurance?", customer_id="C-300", session_id="m2")
    assert resp.outcome == "needs_clarification" and not resp.actions

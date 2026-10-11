from tools import insurance, orders, refunds, tickets


def test_get_order_found_and_missing(backend):
    ok = orders.get_order(backend, "ORD-1002")
    assert ok.ok and ok.data["total"] == 45.0 and ok.source == "ORD-1002"
    missing = orders.get_order(backend, "ORD-0000")
    assert not missing.ok and missing.error == "ORDER_NOT_FOUND"


def test_refund_rules(backend):
    check = lambda oid: refunds.check_refund_eligibility(backend, oid, 200.0, 30).data  # noqa: E731
    assert check("ORD-1002")["decision"] == "eligible" and not check("ORD-1002")["requires_approval"]
    assert check("ORD-1004")["requires_approval"] and "R-4" in check("ORD-1004")["rule_ids"]
    assert check("ORD-1005")["decision"] == "store_credit_only"
    assert check("ORD-1006")["decision"] == "not_returned"
    assert check("ORD-1010")["decision"] == "already_refunded"
    assert check("ORD-1012")["decision"] == "outside_window"


def test_refund_is_idempotent(backend):
    first = refunds.issue_refund(backend, "ORD-1002", 45.0)
    second = refunds.issue_refund(backend, "ORD-1002", 45.0)
    assert first.ok and not second.ok and second.error == "DUPLICATE_REFUND"


def test_refund_rejects_non_positive_amount(backend):
    assert refunds.issue_refund(backend, "ORD-1002", 0).error == "INVALID_AMOUNT"


def test_claim_rules(backend):
    check = lambda oid: insurance.check_claim_eligibility(backend, oid, 200.0).data  # noqa: E731
    assert check("ORD-1007")["decision"] == "eligible" and check("ORD-1007")["amount"] == 180.0
    assert check("ORD-1009")["requires_approval"]
    assert check("ORD-1001")["decision"] == "no_coverage"
    assert check("ORD-1003")["decision"] == "not_yet_delivered"
    assert check("ORD-1011")["decision"] == "outside_window"


def test_ticket_validation_and_reuse(backend):
    assert tickets.create_ticket(backend, "ORD-1008", "bogus", "x").error == "INVALID_CATEGORY"
    a = tickets.create_ticket(backend, "ORD-1008", "lost_parcel", "investigate")
    b = tickets.create_ticket(backend, "ORD-1008", "lost_parcel", "investigate again")
    assert a.data["ticket_id"] == b.data["ticket_id"] and b.data.get("reused")


def test_ledger_persists_writes(tmp_path, settings):
    from tools.backend import Backend
    ledger = tmp_path / "ledger.jsonl"
    b1 = Backend.from_dir(settings.data_dir, ledger_path=ledger)
    refunds.issue_refund(b1, "ORD-1002", 45.0)
    b2 = Backend.from_dir(settings.data_dir, ledger_path=ledger)
    assert "ORD-1002" in b2.refunds
    assert refunds.issue_refund(b2, "ORD-1002", 45.0).error == "DUPLICATE_REFUND"

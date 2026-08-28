import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from constants import normalize_status, now_iso  # noqa: E402
from ingestion import suggest_mapping, validate_and_normalize  # noqa: E402
from intelligence import heuristic_action_probabilities, heuristic_natural_probability  # noqa: E402
from policy import compute_eiv, evaluate_policy  # noqa: E402
from security_utils import compute_signature, verify_signature  # noqa: E402

SETTINGS = {
    "key": "policy",
    "emergency_stop": False,
    "auto_execute": True,
    "recovery_window_days": 14,
    "approval_threshold_amount": 50000,
    "confidence_threshold": 0.55,
    "max_total_cost_per_case": 500,
    "do_not_contact_customers": [],
}

# Single shared loop: motor binds to the loop on first use, so asyncio.run()
# per test would close it and break subsequent engine tests.
LOOP = asyncio.new_event_loop()


def _case(**overrides):
    base = {
        "case_id": "case_test",
        "status": "EVALUATED",
        "created_at": now_iso(),
        "amount_at_risk": 10000.0,
        "currency": "INR",
        "confidence": 0.8,
        "customer_reference": "cust_1",
    }
    base.update(overrides)
    return base


def _executed(action_type="SAFE_PAYMENT_RETRY", hours_ago=48, cost=25.0):
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return {"action_type": action_type, "executed_time": ts, "estimated_cost": cost, "outcome": "PENDING"}


# ---- status normalization ----

def test_normalize_status():
    assert normalize_status("failed") == "failed"
    assert normalize_status("CAPTURED") == "success"
    assert normalize_status(" paid ") == "success"
    assert normalize_status("processing") == "pending"
    assert normalize_status("garbage") is None
    assert normalize_status(None) is None


# ---- financial math ----

def test_compute_eiv():
    # 10000 * (0.7 - 0.4) - 50 = 2950
    assert compute_eiv(10000, 0.7, 0.4, 50) == 2950.0
    # uplift below cost -> negative EIV
    assert compute_eiv(100, 0.5, 0.45, 25) == -20.0
    # probabilities clamped
    assert compute_eiv(1000, 5.0, 0.0, 0) == 1000.0


# ---- policy engine (deterministic) ----

def test_policy_allows_clean_action():
    result = evaluate_policy(_case(), "SAFE_PAYMENT_RETRY", [], SETTINGS)
    assert result["decision"] == "ALLOW"


def test_policy_retry_limit():
    actions = [_executed() for _ in range(3)]
    result = evaluate_policy(_case(), "SAFE_PAYMENT_RETRY", actions, SETTINGS)
    assert result["decision"] == "BLOCK"
    assert any(r["rule"] == "MAX_EXECUTIONS_REACHED" for r in result["reasons"])


def test_policy_cooldown():
    actions = [_executed(hours_ago=1)]
    result = evaluate_policy(_case(), "SAFE_PAYMENT_RETRY", actions, SETTINGS)
    assert result["decision"] == "BLOCK"
    assert any(r["rule"] == "COOLDOWN_ACTIVE" for r in result["reasons"])


def test_policy_blocks_when_case_closed():
    result = evaluate_policy(_case(status="VERIFIED_RECOVERED"), "SAFE_PAYMENT_RETRY", [], SETTINGS)
    assert result["decision"] == "BLOCK"
    assert any(r["rule"] == "CASE_ALREADY_CLOSED" for r in result["reasons"])


def test_policy_emergency_stop():
    settings = {**SETTINGS, "emergency_stop": True}
    result = evaluate_policy(_case(), "SAFE_PAYMENT_RETRY", [], settings)
    assert result["decision"] == "STOP"
    assert any(r["rule"] == "EMERGENCY_STOP" for r in result["reasons"])


def test_policy_window_expired():
    old = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    result = evaluate_policy(_case(created_at=old), "SAFE_PAYMENT_RETRY", [], SETTINGS)
    assert result["decision"] == "STOP"
    assert any(r["rule"] == "RECOVERY_WINDOW_EXPIRED" for r in result["reasons"])


def test_policy_high_amount_requires_approval():
    result = evaluate_policy(_case(amount_at_risk=75000), "SAFE_PAYMENT_RETRY", [], SETTINGS)
    assert result["decision"] == "APPROVAL"
    assert any(r["rule"] == "AMOUNT_ABOVE_APPROVAL_THRESHOLD" for r in result["reasons"])


def test_policy_low_confidence_requires_approval():
    result = evaluate_policy(_case(confidence=0.3), "SAFE_PAYMENT_RETRY", [], SETTINGS)
    assert result["decision"] == "APPROVAL"
    assert any(r["rule"] == "LOW_CONFIDENCE_REVIEW" for r in result["reasons"])


def test_policy_duplicate_pending():
    actions = [{"action_type": "SAFE_PAYMENT_RETRY", "executed_time": None, "outcome": "PENDING", "estimated_cost": 25}]
    result = evaluate_policy(_case(), "SAFE_PAYMENT_RETRY", actions, SETTINGS)
    assert result["decision"] == "BLOCK"
    assert any(r["rule"] == "DUPLICATE_PENDING_ACTION" for r in result["reasons"])


def test_policy_max_cost_cap():
    actions = [_executed("SEND_RECOVERY_LINK", hours_ago=72, cost=480.0)]
    result = evaluate_policy(_case(), "SAFE_PAYMENT_RETRY", actions, SETTINGS)
    assert result["decision"] == "STOP"
    assert any(r["rule"] == "MAX_INTERVENTION_COST_REACHED" for r in result["reasons"])


def test_policy_unknown_action_blocked():
    result = evaluate_policy(_case(), "CHARGE_CUSTOMER_NOW", [], SETTINGS)
    assert result["decision"] == "BLOCK"
    assert result["reasons"][0]["rule"] == "ACTION_NOT_IN_CATALOG"


def test_policy_do_not_contact():
    settings = {**SETTINGS, "do_not_contact_customers": ["cust_1"]}
    result = evaluate_policy(_case(), "CUSTOMER_REMINDER", [], settings)
    assert result["decision"] == "BLOCK"
    assert any(r["rule"] == "DO_NOT_CONTACT" for r in result["reasons"])


# ---- heuristics ----

def test_heuristic_natural_recovery_bounds():
    for code in ("insufficient_funds", "stolen_card", "", "do_not_honor"):
        p, reasons = heuristic_natural_probability({"failure_code": code, "hours_since_failure": 5, "failed_attempt_count": 1, "previous_successes": 0})
        assert 0.02 <= p <= 0.90
        assert reasons


def test_heuristic_soft_beats_hard_decline():
    soft, _ = heuristic_natural_probability({"failure_code": "insufficient_funds", "hours_since_failure": 2, "failed_attempt_count": 1, "previous_successes": 0})
    hard, _ = heuristic_natural_probability({"failure_code": "stolen_card", "hours_since_failure": 2, "failed_attempt_count": 1, "previous_successes": 0})
    assert soft > hard
    probs_soft = heuristic_action_probabilities({"failure_code": "insufficient_funds"}, soft)
    probs_hard = heuristic_action_probabilities({"failure_code": "stolen_card"}, hard)
    assert probs_soft["SAFE_PAYMENT_RETRY"] > probs_hard["SAFE_PAYMENT_RETRY"]


# ---- webhook signatures ----

def test_signature_roundtrip():
    body = b'{"event_id":"evt_1"}'
    secret = "test-secret"
    sig = compute_signature(body, secret)
    assert verify_signature(body, sig, secret)
    assert not verify_signature(body, "sha256=" + "0" * 64, secret)
    assert not verify_signature(body, None, secret)
    assert not verify_signature(body + b"x", sig, secret)


# ---- CSV/row validation ----

MAPPING = {
    "payment_id": "txn_id",
    "order_id": "order_id",
    "invoice_id": None,
    "customer_reference": "customer_id",
    "amount": "total",
    "currency": "currency",
    "status": "payment_status",
    "failure_code": "error_code",
    "failure_reason": None,
    "payment_method": None,
    "timestamp": "created_at",
}


def test_validate_and_normalize_counts():
    rows = [
        {"txn_id": "t1", "order_id": "o1", "total": "1,000.00", "currency": "inr", "payment_status": "failed", "error_code": "insufficient_funds", "created_at": "2026-06-01T10:00:00Z", "customer_id": "c1"},
        {"txn_id": "t1", "order_id": "o1", "total": "1000", "currency": "INR", "payment_status": "failed", "created_at": "2026-06-01T10:00:00Z"},  # duplicate
        {"txn_id": "t2", "order_id": "o2", "total": "abc", "payment_status": "failed", "created_at": "2026-06-01T10:00:00Z"},  # invalid amount
        {"txn_id": "t3", "order_id": "o3", "total": "500", "payment_status": "weird", "created_at": "2026-06-01T10:00:00Z"},  # bad status
        {"txn_id": "t4", "total": "500", "payment_status": "failed", "created_at": "2026-06-01T10:00:00Z"},  # no linkage
        {"txn_id": "t5", "order_id": "o5", "total": "250", "payment_status": "captured", "created_at": "not-a-date"},  # bad date
    ]
    result = validate_and_normalize(rows, MAPPING, source="CSV_UPLOAD", batch_id="b1")
    report = result["report"]
    assert report["total_rows"] == 6
    assert report["valid_rows"] == 1
    assert report["duplicate_rows"] == 1
    assert report["invalid_amounts"] == 1
    assert report["unsupported_statuses"] == 1
    assert report["missing_linkage"] == 1
    assert report["invalid_dates"] == 1
    record = result["records"][0]
    assert record["amount"] == 1000.0
    assert record["currency"] == "INR"
    assert record["status"] == "failed"
    # 4 invalid rows + 1 duplicate row are all queued as exceptions
    assert len(result["exceptions"]) == 5
    assert result["report"]["rows_to_exception_queue"] == 5


def test_suggest_mapping_synonyms():
    headers = ["transaction_id", "order_id", "payment_amount", "payment_status", "created_at", "error_code"]
    mapping = suggest_mapping(headers)
    assert mapping["payment_id"]["header"] == "transaction_id"
    assert mapping["amount"]["header"] == "payment_amount"
    assert mapping["status"]["header"] == "payment_status"
    assert mapping["timestamp"]["header"] == "created_at"
    assert mapping["failure_code"]["header"] == "error_code"
    assert mapping["invoice_id"]["header"] is None


# ---- engine integration (uses local MongoDB, LLM disabled) ----

def test_engine_full_loop_and_double_counting():
    async def run():
        from database import db
        from detection import process_payment_attempt

        suffix = uuid.uuid4().hex[:8]
        order = f"ORD-TEST-{suffix}"
        await db.payment_attempts.delete_many({"order_id": order})
        await db.recovery_cases.delete_many({"order_key": order})
        await db.recovery_actions.delete_many({"case_id": {"$regex": "^case_"}})
        await db.audit_events.delete_many({"related.order_key": order})

        def attempt(pid, status, amount=5000.0):
            return {
                "payment_id": pid, "order_id": order, "invoice_id": None,
                "customer_reference": "cust_test", "amount": amount, "currency": "INR",
                "status": status, "failure_code": "insufficient_funds" if status == "failed" else None,
                "failure_reason": None, "payment_method": "card", "timestamp": now_iso(),
                "source": "TEST", "source_event_id": None, "simulated": True,
                "ingestion_confidence": 1.0, "raw_data_reference": "test", "batch_id": None,
                "ingested_at": now_iso(),
            }

        r1 = await process_payment_attempt(attempt(f"p1-{suffix}", "failed"), actor="test", allow_llm=False)
        assert r1["result"] == "case_created"
        case_id = r1["case_id"]

        r2 = await process_payment_attempt(attempt(f"p2-{suffix}", "failed"), actor="test", allow_llm=False)
        assert r2["result"] == "case_updated"
        count = await db.recovery_cases.count_documents({"order_key": order})
        assert count == 1, "double counting prevention failed"

        r3 = await process_payment_attempt(attempt(f"p3-{suffix}", "success"), actor="test", allow_llm=False)
        assert r3["result"] in ("verified_recovered", "closed_natural")
        case = await db.recovery_cases.find_one({"case_id": case_id}, {"_id": 0})
        assert case["verification_status"] == "VERIFIED"
        if r3["result"] == "verified_recovered":
            assert case["recovered_amount"] == 5000.0
            assert case["attribution"] == "SYSTEM_ACTION"
        else:
            assert case["recovered_amount"] == 0.0

        # duplicate attempt blocked
        r4 = await process_payment_attempt(attempt(f"p1-{suffix}", "failed"), actor="test", allow_llm=False)
        assert r4["result"] == "duplicate_attempt"

        await db.payment_attempts.delete_many({"order_id": order})
        await db.recovery_cases.delete_many({"order_key": order})

    LOOP.run_until_complete(run())


def test_engine_natural_recovery_no_case():
    async def run():
        from database import db
        from detection import process_payment_attempt

        suffix = uuid.uuid4().hex[:8]
        order = f"ORD-TEST-NAT-{suffix}"
        await db.payment_attempts.delete_many({"order_id": order})
        await db.recovery_cases.delete_many({"order_key": order})

        def attempt(pid, status):
            return {
                "payment_id": pid, "order_id": order, "invoice_id": None,
                "customer_reference": None, "amount": 1200.0, "currency": "USD",
                "status": status, "failure_code": None, "failure_reason": None,
                "payment_method": "card", "timestamp": now_iso(), "source": "TEST",
                "source_event_id": None, "simulated": True, "ingestion_confidence": 1.0,
                "raw_data_reference": "test", "batch_id": None, "ingested_at": now_iso(),
            }

        await process_payment_attempt(attempt(f"pn1-{suffix}", "success"), actor="test", allow_llm=False)
        r = await process_payment_attempt(attempt(f"pn2-{suffix}", "failed"), actor="test", allow_llm=False)
        assert r["result"] == "naturally_recovered"
        count = await db.recovery_cases.count_documents({"order_key": order})
        assert count == 0, "a case was created for an already-paid order"

        await db.payment_attempts.delete_many({"order_id": order})

    LOOP.run_until_complete(run())

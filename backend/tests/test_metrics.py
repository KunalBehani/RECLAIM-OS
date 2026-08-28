"""Dashboard metrics integrity tests — every number must trace to records."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from constants import now_iso  # noqa: E402
from metrics import (  # noqa: E402
    FUNNEL_STAGES,
    case_title,
    compute_funnel,
    compute_kpis,
    humanize_failure,
    source_category,
    why_at_risk,
)
from policy import evaluate_policy  # noqa: E402

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


def test_case_title_priority():
    assert case_title({"order_id": "ORD-1"}) == "Failed Payment for Order ORD-1"
    assert case_title({"invoice_id": "INV-9"}) == "Overdue Invoice INV-9"
    assert case_title({"payment_attempt_ids": ["pay_1"]}) == "Unresolved Payment — pay_1"
    assert case_title({}) == "Unresolved Payment"
    assert case_title({"title": "Merchant Given"}) == "Merchant Given"


def test_source_taxonomy():
    assert source_category({"source": "CSV_UPLOAD", "simulated": False}) == "IMPORTED"
    assert source_category({"source": "XLSX_UPLOAD", "simulated": False}) == "IMPORTED"
    assert source_category({"source": "WEBHOOK", "simulated": False}) == "LIVE"
    assert source_category({"source": "SIMULATOR", "simulated": True}) == "SIMULATED"
    assert source_category({"source": "WEBHOOK", "simulated": True}) == "SIMULATED"  # simulated always wins


def test_why_at_risk_humanized():
    case = {"reason_created": "Payment attempt pay_1 failed (insufficient_funds) and no successful settlement exists for this order."}
    assert why_at_risk(case) == "Payment failed (Insufficient funds); no successful replacement payment found"
    assert humanize_failure("stolen_card") == "Stolen card"
    assert humanize_failure(None) == "Unknown reason"


def test_funnel_monotonic_and_strict():
    cases = [
        {"case_id": "c1", "status": "VERIFIED_RECOVERED", "natural_recovery_probability": 0.3, "policy_result": {"decision": "ALLOW"}, "verification_status": "VERIFIED"},
        {"case_id": "c2", "status": "ACTION_EXECUTED", "natural_recovery_probability": 0.3, "policy_result": {"decision": "ALLOW"}, "verification_status": "PENDING"},
        {"case_id": "c3", "status": "EVALUATED", "natural_recovery_probability": 0.3, "policy_result": {"decision": "BLOCK"}},
        {"case_id": "c4", "status": "OPEN"},
        {"case_id": "c5", "status": "INVALID"},
        {"case_id": "c6", "status": "STOPPED", "natural_recovery_probability": 0.3, "policy_result": {"decision": "STOP"}},
    ]
    actions = [
        {"case_id": "c1", "executed_time": "2026-01-01T00:00:00+00:00", "approval_status": "AUTO_APPROVED"},
        {"case_id": "c2", "executed_time": "2026-01-01T00:00:00+00:00", "approval_status": "AUTO_APPROVED"},
    ]
    funnel = compute_funnel(cases, actions)
    counts = [funnel["stages"][s] for s in FUNNEL_STAGES]
    assert counts == sorted(counts, reverse=True), f"funnel not monotonic: {counts}"
    assert funnel["stages"]["recovered"] == 1
    assert funnel["stages"]["executed"] == 2
    assert funnel["stages"]["verifying"] == 2
    assert funnel["side"]["invalid"] == 1
    assert funnel["side"]["stopped"] == 1
    assert funnel["side"]["blocked"] == 1  # c3 is open with BLOCK decision
    # invalid case c5 never enters eligible or any later stage
    assert "c5" not in funnel["sets"]["eligible"]


def test_funnel_verifying_never_exceeds_executed():
    # cases claim PENDING verification but have no execution evidence at all
    cases = [
        {"case_id": "x1", "status": "ACTION_EXECUTED", "natural_recovery_probability": 0.3, "policy_result": {"decision": "ALLOW"}, "verification_status": "PENDING"},
        {"case_id": "x2", "status": "ACTION_EXECUTED", "natural_recovery_probability": 0.3, "policy_result": {"decision": "ALLOW"}, "verification_status": "PENDING"},
    ]
    funnel = compute_funnel(cases, [])
    assert funnel["stages"]["executed"] == 0
    assert funnel["stages"]["verifying"] == 0  # no evidence of execution -> not verifying
    # audit-trail execution evidence restores the lineage
    funnel2 = compute_funnel(cases, [], {"x1"})
    assert funnel2["stages"]["executed"] == 1
    assert funnel2["stages"]["verifying"] == 1


def test_recovery_rate_known_final_denominator():
    cases = [
        {"case_id": "c1", "status": "VERIFIED_RECOVERED", "currency": "INR", "recovered_amount": 100, "amount_at_risk": 100},
        {"case_id": "c2", "status": "NATURALLY_RECOVERED", "currency": "INR", "natural_recovered_amount": 50},
        {"case_id": "c3", "status": "STOPPED", "currency": "INR"},
        {"case_id": "c4", "status": "INVALID", "currency": "INR"},
        {"case_id": "c5", "status": "OPEN", "currency": "INR", "amount_at_risk": 200},
    ]
    kpis = compute_kpis(cases, [], 3)
    # denominator = c1 + c2 only (known final outcomes); STOPPED/INVALID/OPEN excluded
    assert kpis["recovery_rate_pct"] == 50.0
    assert kpis["recovery_rate_denominator"] == 2
    assert kpis["revenue_at_risk"] == {"INR": 200.0}
    assert kpis["verified_gross_recovery"] == {"INR": 100.0}
    assert kpis["exceptions_open"] == 3


def test_recovery_rate_insufficient_data():
    kpis = compute_kpis([{"case_id": "c1", "status": "OPEN", "amount_at_risk": 10, "currency": "INR"}], [], 0)
    assert kpis["recovery_rate_pct"] is None  # no known final outcomes -> honest null, not 0%


def test_policy_requires_approval_when_confidence_unavailable():
    case = {
        "case_id": "case_t", "status": "EVALUATED", "created_at": now_iso(),
        "amount_at_risk": 10000.0, "currency": "INR", "confidence": None, "customer_reference": "cust_1",
    }
    result = evaluate_policy(case, "SAFE_PAYMENT_RETRY", [], SETTINGS)
    assert result["decision"] == "APPROVAL"
    assert any(r["rule"] == "LOW_CONFIDENCE_REVIEW" for r in result["reasons"])
    # non-money-touching actions still allowed without confidence
    result2 = evaluate_policy(case, "SCHEDULED_RECHECK", [], SETTINGS)
    assert result2["decision"] == "ALLOW"

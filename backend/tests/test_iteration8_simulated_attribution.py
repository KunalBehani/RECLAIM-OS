"""Iteration 8 — independent API-level verification of the USER MANDATE:
a SIMULATED action on a real provider-sourced case must NEVER earn recovery
attribution.

Everything here goes through the PUBLIC ingress (REACT_APP_BACKEND_URL) with
the owner session token. Webhook deliveries use only the built-in Webhook Test
Lab (server-side signing) — the webhook secret is never read or handled here.
"""
import os
import time

import pytest
import requests
from dotenv import dotenv_values

_fe = dotenv_values("/app/frontend/.env")
BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or _fe.get("REACT_APP_BACKEND_URL", "")).rstrip("/")
if not BASE_URL:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")

SESSION = "test_session_smoke_1787904424204"
HEADERS = {"Authorization": f"Bearer {SESSION}", "Content-Type": "application/json"}
REAL_ORDER = "order_TWKE56rzzX1S63"

# Serialize with the other webhook modules: the Webhook Test Lab signs with the
# stored integration doc, which guarded modules swap — running unlocked causes
# mid-scenario signature mismatches.
pytestmark = pytest.mark.usefixtures("razorpay_integration_guard")


def api_get(path, **kw):
    return requests.get(f"{BASE_URL}/api{path}", headers=HEADERS, timeout=90, **kw)


def api_post(path, json=None):
    return requests.post(f"{BASE_URL}/api{path}", headers=HEADERS, json=json or {}, timeout=120)


def find_case_by_order(order_key):
    r = api_get("/cases", params={"q": order_key})
    assert r.status_code == 200, r.text[:300]
    for c in r.json()["cases"]:
        if c["order_key"] == order_key:
            return c
    return None


# ---------- FIX VERIFICATION: the real provider-sourced case ----------
class TestRealCaseAttribution:
    def test_real_case_closed_naturally_with_no_attribution(self):
        summary = find_case_by_order(REAL_ORDER)
        assert summary is not None, f"case for {REAL_ORDER} not found via GET /api/cases"
        r = api_get(f"/cases/{summary['case_id']}")
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        case = data["case"]

        assert case["source"] in ("RAZORPAY_TEST", "RAZORPAY_LIVE"), case["source"]
        assert case["status"] == "NATURALLY_RECOVERED", case["status"]
        assert case["outcome"] == "NATURALLY_RECOVERED", case["outcome"]
        assert case["attribution_strength"] == "NONE", case["attribution_strength"]
        assert case.get("attribution") == "NONE", case.get("attribution")
        assert float(case.get("recovered_amount") or 0) == 0.0
        assert float(case.get("natural_recovered_amount") or 0) == 500.0
        assert case["verification_status"] == "VERIFIED"

        # A SIMULATED SEND_RECOVERY_LINK action really is on record
        sims = [a for a in data["actions"]
                if a.get("action_type") == "SEND_RECOVERY_LINK" and a.get("simulated") and a.get("executed_time")]
        assert sims, f"expected an executed SIMULATED SEND_RECOVERY_LINK, got {[(a.get('action_type'), a.get('simulated')) for a in data['actions']]}"

        closes = [e for e in data["audit_trail"] if e["event_type"] == "CASE_CLOSED"]
        assert closes, "no CASE_CLOSED audit event"
        reason = closes[-1]["reason"]
        assert "SIMULATED" in reason and "SEND_RECOVERY_LINK" in reason, reason
        assert "never earn attribution" in reason or "no genuine customer-facing action" in reason, reason
        assert "NOT counted as system-recovered revenue" in reason, reason

    def test_replay_shows_closure_with_no_attribution(self):
        summary = find_case_by_order(REAL_ORDER)
        r = api_get(f"/cases/{summary['case_id']}/replay")
        assert r.status_code == 200, r.text[:300]
        steps = r.json()["steps"]
        types = [s["event_type"] for s in steps]
        assert "CASE_CLOSED" in types
        closed = [s for s in steps if s["event_type"] == "CASE_CLOSED"][-1]
        assert closed["after_state"]["attribution_strength"] == "NONE"
        assert float(closed["after_state"]["recovered_amount"] or 0) == 0.0


# ---------- METRICS INTEGRITY ----------
class TestMetricsIntegrity:
    def test_natural_recovery_not_in_verified_net_recovery(self):
        r = api_get("/dashboard/summary")
        assert r.status_code == 200, r.text[:300]
        kpis = r.json()["kpis"]
        assert "verified_net_recovery" in kpis
        assert "natural_recovered_not_counted" in kpis
        natural = float(kpis["natural_recovered_not_counted"].get("INR", 0))
        assert natural >= 500.0, f"natural bucket should include the 500 case, got {natural}"

        # Verified recovery must be derived only from VERIFIED_RECOVERED cases,
        # none of which may be this naturally-recovered case.
        cases = api_get("/cases", params={"status": "NATURALLY_RECOVERED"}).json()["cases"]
        assert any(c["order_key"] == REAL_ORDER for c in cases)
        verified = api_get("/cases", params={"status": "VERIFIED_RECOVERED"}).json()["cases"]
        assert all(c["order_key"] != REAL_ORDER for c in verified)

    def test_verified_gross_equals_sum_of_verified_case_amounts(self):
        kpis = api_get("/dashboard/summary").json()["kpis"]
        verified = api_get("/cases", params={"status": "VERIFIED_RECOVERED"}).json()["cases"]
        inr_sum = round(sum(float(c.get("recovered_amount") or 0)
                            for c in verified if (c.get("currency") or "INR") == "INR"), 2)
        assert inr_sum == round(float(kpis["verified_gross_recovery"].get("INR", 0)), 2), \
            f"sum(recovered_amount)={inr_sum} vs KPI={kpis['verified_gross_recovery']}"
        assert all(c["order_key"] != REAL_ORDER for c in verified)

    def test_verified_net_recovery_unchanged_by_repeat_verification(self):
        before = api_get("/dashboard/summary").json()["kpis"]["verified_net_recovery"]
        summary = find_case_by_order(REAL_ORDER)
        v = api_post(f"/cases/{summary['case_id']}/verify")
        assert v.status_code == 200, v.text[:300]
        after = api_get("/dashboard/summary").json()["kpis"]["verified_net_recovery"]
        assert before == after, f"verified_net_recovery changed {before} -> {after}"
        case = api_get(f"/cases/{summary['case_id']}").json()["case"]
        assert case["status"] == "NATURALLY_RECOVERED"
        assert case["attribution_strength"] == "NONE"
        assert float(case["recovered_amount"] or 0) == 0.0


# ---------- REGRESSION via the Webhook Test Lab ----------
@pytest.mark.parametrize("scenario", ["late-success", "replacement-payment"])
def test_lab_simulated_action_never_earns_attribution(scenario):
    """Lab scenario delivers a genuinely-signed payment.failed (case created,
    pipeline auto-executes an action in SIMULATED mode) then payment.captured
    for the same order. The case must NOT become VERIFIED_RECOVERED."""
    r = api_post(f"/integrations/razorpay/test-lab/{scenario}")
    assert r.status_code == 200, r.text[:400]
    body = r.json()
    order = body["order_id"]
    assert all(s["http"] == 200 for s in body["steps"]), body["steps"]

    case = None
    for _ in range(10):
        case = find_case_by_order(order)
        if case and case["status"] not in ("DETECTED", "EVALUATED", "ACTION_IN_PROGRESS"):
            break
        time.sleep(2)
    assert case, f"no case created for lab order {order}"

    detail = api_get(f"/cases/{case['case_id']}").json()
    executed = [a for a in detail["actions"] if a.get("executed_time")]
    assert case["source"] == "RAZORPAY_TEST", case["source"]
    assert all(a.get("simulated") for a in executed), \
        f"lab actions should be simulated: {[(a['action_type'], a.get('simulated')) for a in executed]}"

    assert case["status"] != "VERIFIED_RECOVERED", (
        f"{order}: simulated action earned attribution! status={case['status']} "
        f"strength={case['attribution_strength']} recovered={case['recovered_amount']}")
    assert case["status"] == "NATURALLY_RECOVERED", case["status"]
    assert case["attribution_strength"] in ("NONE", "UNCERTAIN"), case["attribution_strength"]
    assert float(case["recovered_amount"] or 0) == 0.0
    if executed:
        assert case["attribution_strength"] == "NONE", (
            "an executed simulated action before settlement must yield NONE, "
            f"got {case['attribution_strength']}")


def test_manual_execute_then_settlement_no_attribution():
    """Create a fresh lab case, run a manual (SIMULATED) SEND_RECOVERY_LINK via
    POST /api/cases/{id}/execute, then settle the SAME order through the
    server-signed simulator. Must close NATURALLY_RECOVERED / NONE."""
    r = api_post("/integrations/razorpay/test-lab/valid-payment-failed")
    assert r.status_code == 200, r.text[:400]
    order = r.json()["order_id"]
    case = None
    for _ in range(8):
        case = find_case_by_order(order)
        if case:
            break
        time.sleep(1.5)
    assert case, f"no case created for {order}"
    case_id = case["case_id"]

    ex = api_post(f"/cases/{case_id}/execute", {"action_type": "SEND_RECOVERY_LINK"})
    assert ex.status_code == 200, ex.text[:400]
    ex_body = ex.json()
    assert ex_body.get("executed") or ex_body.get("policy_result"), ex_body

    detail = api_get(f"/cases/{case_id}").json()
    executed = [a for a in detail["actions"] if a.get("executed_time")]
    assert executed, "expected at least one executed action"
    assert all(a.get("simulated") for a in executed), \
        f"actions must be simulated: {[(a['action_type'], a.get('simulated')) for a in executed]}"

    time.sleep(1)
    s = api_post("/simulate/payment-event",
                 {"order_id": order, "amount": 6750.0, "status": "success", "currency": "INR"})
    assert s.status_code == 200, s.text[:400]

    final = api_get(f"/cases/{case_id}").json()["case"]
    assert final["status"] == "NATURALLY_RECOVERED", (
        f"status={final['status']} strength={final['attribution_strength']} "
        f"recovered={final['recovered_amount']}")
    assert final["attribution_strength"] == "NONE", final["attribution_strength"]
    assert float(final["recovered_amount"] or 0) == 0.0
    closes = [e for e in api_get(f"/cases/{case_id}").json()["audit_trail"]
              if e["event_type"] == "CASE_CLOSED"]
    assert closes and "SIMULATED" in closes[-1]["reason"], closes[-1]["reason"] if closes else None


# ---------- API SURFACE: diagnostics leaks nothing ----------
class TestDiagnosticsSafety:
    def test_diagnostics_masked_only(self):
        r = api_get("/integrations/razorpay/diagnostics")
        assert r.status_code == 200, r.text[:300]
        raw = r.text
        data = r.json()
        assert data["mode"] == "TEST"
        assert data["key_id_is_test"] is True
        assert data["key_secret_present"] is True
        assert data["webhook_secret_present"] is True
        for banned in ("key_secret\"", "webhook_secret\"", "Authorization", "Basic ", "Bearer "):
            if banned in ("key_secret\"", "webhook_secret\""):
                assert banned not in raw, f"diagnostics exposes {banned}"
            else:
                assert banned not in raw, f"diagnostics leaks {banned}"
        assert "key_secret" not in data
        assert "webhook_secret" not in data
        assert len(data["key_id_prefix"] or "") <= 9

    def test_diagnostics_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/integrations/razorpay/diagnostics", timeout=60)
        assert r.status_code in (401, 403), r.status_code

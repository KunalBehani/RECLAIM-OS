"""Iteration 11 — Phase 2B hardening: explicit state machine, audit completeness,
EIV transparency, per-customer anti-spam cap, cron endpoint, LIVE readiness
diagnostic, case filters, observability fields.

Runs under the shared guard (dummy TEST razorpay config, resend forced off,
LIVE doc + policy settings snapshotted/restored). Sends no real email.
"""
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone

import pytest
import requests
from dotenv import dotenv_values
from pymongo import MongoClient

_be = dotenv_values("/app/backend/.env")
mdb = MongoClient(_be["MONGO_URL"])[_be["DB_NAME"]]
LOCAL = "http://localhost:8001"
HEADERS = {"Authorization": "Bearer test_session_smoke_1787904424204"}
TEST_SECRET = "whsec_test_lab_secret_123"
CRON_SECRET = _be.get("WEBHOOK_CRON_SECRET", "")
SINK = "delivered@resend.dev"  # playbook-sanctioned integration-test recipient

pytestmark = pytest.mark.usefixtures("razorpay_integration_guard")


def _ids(tag):
    suf = f"{tag}{uuid.uuid4().hex[:6]}"
    return suf, f"order_{suf}", f"pay_{suf}A", f"pay_{suf}B"


def _sign(raw: bytes) -> str:
    return hmac.new(TEST_SECRET.encode(), raw, hashlib.sha256).hexdigest()


def _pay(event, pay, oid, amount_paise, ts, email="p2customer@example.com"):
    entity = {"id": pay, "entity": "payment", "amount": amount_paise, "currency": "INR",
              "order_id": oid, "method": "card", "created_at": ts, "email": email}
    if event == "payment.failed":
        entity.update({"status": "failed", "error_code": "insufficient_funds", "error_description": "insufficient funds"})
    else:
        entity["status"] = "captured"
    return {"entity": "event", "account_id": "acc_p2b", "event": event,
            "payload": {"payment": {"entity": entity}}, "created_at": ts}


def _post(payload, eid):
    raw = json.dumps(payload).encode()
    return requests.post(f"{LOCAL}/api/webhooks/razorpay", data=raw, timeout=120,
                         headers={"Content-Type": "application/json",
                                  "X-Razorpay-Signature": _sign(raw), "x-razorpay-event-id": eid})


def _case(oid):
    return mdb.recovery_cases.find_one({"order_key": oid}, {"_id": 0})


def _cleanup(suf):
    oid = f"order_{suf}"
    case = mdb.recovery_cases.find_one({"order_key": oid}, {"_id": 0, "case_id": 1})
    mdb.recovery_cases.delete_many({"order_key": oid})
    mdb.payment_attempts.delete_many({"order_id": oid})
    mdb.provider_events.delete_many({"provider_event_id": {"$regex": f"^evt_{suf}"}})
    if case:
        mdb.recovery_actions.delete_many({"case_id": case["case_id"]})
        mdb.audit_events.delete_many({"case_id": case["case_id"]})


# ---------- 1. explicit state machine ----------
def test_01_state_machine_transitions():
    from case_state import assert_transition
    assert_transition("OPEN", "EVALUATED")
    assert_transition("EVALUATED", "ACTION_EXECUTED")
    assert_transition("APPROVAL_PENDING", "ACTION_EXECUTED")
    assert_transition("ACTION_EXECUTED", "VERIFIED_RECOVERED")
    assert_transition("OPEN", "OPEN")  # no-op allowed
    for bad in [("VERIFIED_RECOVERED", "OPEN"), ("NATURALLY_RECOVERED", "ACTION_EXECUTED"),
                ("STOPPED", "EVALUATED"), ("EVALUATED", "OPEN"), ("NOT_RECOVERED", "EVALUATED")]:
        with pytest.raises(ValueError):
            assert_transition(*bad)


# ---------- 2. webhook flow emits the full audit chain ----------
def test_02_webhook_audit_chain():
    suf, oid, pay1, _ = _ids("p2a")
    try:
        eid = f"evt_{suf}1"
        r = _post(_pay("payment.failed", pay1, oid, 50000, int(datetime.now(timezone.utc).timestamp())), eid)
        assert r.json()["result"]["result"] == "case_created", r.text[:300]
        case = _case(oid)
        event_audits = {e["event_type"] for e in mdb.audit_events.find({"related.provider_event_id": eid}, {"_id": 0, "event_type": 1})}
        assert "WEBHOOK_SIGNATURE_VERIFIED" in event_audits
        assert "WEBHOOK_RECEIVED" in event_audits
        assert "EVENT_NORMALIZED" in event_audits
        case_audits = {e["event_type"] for e in mdb.audit_events.find({"case_id": case["case_id"]}, {"_id": 0, "event_type": 1})}
        assert "CASE_CREATED" in case_audits
        assert "AI_ANALYSIS_STARTED" in case_audits
        assert "AI_ANALYSIS_COMPLETED" in case_audits
        assert "POLICY_DECISION" in case_audits
    finally:
        _cleanup(suf)


# ---------- 3. canonical case model fields ----------
def test_03_case_model_fields():
    suf, oid, pay1, _ = _ids("p2b")
    try:
        _post(_pay("payment.failed", pay1, oid, 75000, int(datetime.now(timezone.utc).timestamp())), f"evt_{suf}1")
        case = _case(oid)
        for field in ("case_id", "merchant_id", "provider", "provider_mode", "provider_order_id",
                      "provider_payment_id", "customer_reference", "amount_at_risk", "currency",
                      "payment_method", "failure_code", "failure_reason", "first_failed_at",
                      "latest_event_at", "status", "verification_status", "attribution_strength",
                      "recovered_amount", "incremental_recovered_amount", "natural_recovered_amount",
                      "created_at"):
            assert field in case, f"missing canonical field: {field}"
        assert case["merchant_id"] == "default_merchant"
        assert case["provider_mode"] == "TEST"
        assert case["failure_code"] == "insufficient_funds"
        assert case["incremental_recovered_amount"] == 0.0
    finally:
        _cleanup(suf)


# ---------- 4. EIV transparency ----------
def test_04_eiv_inputs_stored_and_reproducible():
    suf, oid, pay1, _ = _ids("p2c")
    try:
        _post(_pay("payment.failed", pay1, oid, 50000, int(datetime.now(timezone.utc).timestamp())), f"evt_{suf}1")
        case = _case(oid)
        actions = list(mdb.recovery_actions.find({"case_id": case["case_id"], "executed_time": {"$ne": None}}, {"_id": 0}))
        if not actions:
            r = requests.post(f"{LOCAL}/api/cases/{case['case_id']}/execute",
                              json={"action_type": case["recommended_action"]}, headers=HEADERS, timeout=120)
            assert r.status_code == 200, r.text[:200]
            actions = list(mdb.recovery_actions.find({"case_id": case["case_id"], "executed_time": {"$ne": None}}, {"_id": 0}))
        assert actions, "no executed action"
        eiv_inputs = actions[-1].get("eiv_inputs")
        if actions[-1]["action_type"] == "SCHEDULED_RECHECK":
            pytest.skip("recheck action carries no EIV inputs by design")
        assert eiv_inputs, "executed action must carry reproducible EIV inputs"
        for k in ("recovery_likelihood", "natural_recovery_baseline", "incremental_probability",
                  "recoverable_amount", "action_cost", "risk_penalty", "eiv", "model_version", "policy_version"):
            assert k in eiv_inputs, f"missing EIV input {k}"
        replay = round(eiv_inputs["recoverable_amount"] * eiv_inputs["incremental_probability"]
                       - eiv_inputs["action_cost"] - eiv_inputs["risk_penalty"], 2)
        assert replay == eiv_inputs["eiv"], "EIV must reproduce exactly from stored inputs"
    finally:
        _cleanup(suf)


# ---------- 5. attribution decision audit ----------
def test_05_attribution_decision_audited():
    suf, oid, pay1, pay2 = _ids("p2d")
    now = int(datetime.now(timezone.utc).timestamp())
    try:
        _post(_pay("payment.failed", pay1, oid, 50000, now - 300), f"evt_{suf}1")
        _post(_pay("payment.captured", pay2, oid, 50000, now + 60), f"evt_{suf}2")
        case = _case(oid)
        assert case["status"] == "NATURALLY_RECOVERED"
        attr = list(mdb.audit_events.find({"case_id": case["case_id"], "event_type": "ATTRIBUTION_DECISION"}, {"_id": 0}))
        assert len(attr) == 1
        assert attr[0]["after_state"]["attribution_strength"] == "NONE"
    finally:
        _cleanup(suf)


# ---------- 6. per-customer anti-spam cap ----------
def test_06_per_customer_daily_cap():
    """The cap governs genuine customer contact: REAL sends to the same customer
    are capped across cases; the second case's action is blocked."""
    email = SINK  # real deliverable sink — executions are genuinely REAL
    suf1, oid1, pay1, _ = _ids("p2e1")
    suf2, oid2, pay2, _ = _ids("p2e2")
    now = int(datetime.now(timezone.utc).timestamp())
    try:
        requests.put(f"{LOCAL}/api/settings", json={"auto_execute": False, "max_customer_actions_per_day": 1}, headers=HEADERS, timeout=60)
        requests.put(f"{LOCAL}/api/integrations/resend/config", json={"enabled": True}, headers=HEADERS, timeout=60)
        _post(_pay("payment.failed", pay1, oid1, 50000, now - 300, email=email), f"evt_{suf1}1")
        _post(_pay("payment.failed", pay2, oid2, 50000, now - 300, email=email), f"evt_{suf2}1")
        c1, c2 = _case(oid1), _case(oid2)
        r1 = requests.post(f"{LOCAL}/api/cases/{c1['case_id']}/execute", json={"action_type": "SEND_RECOVERY_LINK"}, headers=HEADERS, timeout=120)
        assert r1.json().get("executed") is True, r1.text[:200]
        a1 = mdb.recovery_actions.find_one({"case_id": c1["case_id"], "executed_time": {"$ne": None}}, {"_id": 0})
        assert a1["simulated"] is False and a1["notification"]["status"] == "SENT"
        r2 = requests.post(f"{LOCAL}/api/cases/{c2['case_id']}/execute", json={"action_type": "SEND_RECOVERY_LINK"}, headers=HEADERS, timeout=120)
        assert r2.json().get("blocked") == "CUSTOMER_RATE_LIMIT", r2.text[:300]
        assert mdb.audit_events.count_documents({"case_id": c2["case_id"], "event_type": "ACTION_BLOCKED"}) >= 1
        assert mdb.recovery_actions.count_documents({"case_id": c2["case_id"], "executed_time": {"$ne": None}}) == 0
    finally:
        requests.put(f"{LOCAL}/api/settings", json={"auto_execute": True, "max_customer_actions_per_day": 10}, headers=HEADERS, timeout=60)
        requests.put(f"{LOCAL}/api/integrations/resend/config", json={"enabled": False}, headers=HEADERS, timeout=60)
        _cleanup(suf1)
        _cleanup(suf2)


# ---------- 7. cron endpoint: auth, idempotency, background completion ----------
def test_07_cron_endpoint():
    r = requests.post(f"{LOCAL}/api/cron/verification-sweep", json={"run_id": "x"}, timeout=30)
    assert r.status_code == 401
    run_id = f"cronrun_p2_{uuid.uuid4().hex[:8]}"
    try:
        r = requests.post(f"{LOCAL}/api/cron/verification-sweep", json={"event": "schedule.triggered", "run_id": run_id, "data": None},
                          headers={"Authorization": f"Bearer {CRON_SECRET}"}, timeout=30)
        assert r.status_code == 200 and r.json()["accepted"] is True
        r2 = requests.post(f"{LOCAL}/api/cron/verification-sweep", json={"event": "schedule.triggered", "run_id": run_id, "data": None},
                           headers={"Authorization": f"Bearer {CRON_SECRET}"}, timeout=30)
        assert r2.json()["duplicate"] is True
        for _ in range(30):
            run = mdb.cron_runs.find_one({"run_id": run_id}, {"_id": 0})
            if run and run.get("status") in ("COMPLETED", "FAILED"):
                break
            time.sleep(2)
        assert run["status"] == "COMPLETED", f"cron run did not complete: {run}"
        assert run.get("results", {}).get("checked") is not None
    finally:
        mdb.cron_runs.delete_many({"run_id": run_id})


# ---------- 8. LIVE readiness diagnostic honesty ----------
def test_08_live_readiness_diagnostic():
    mdb.integrations.delete_many({"provider": "razorpay", "mode": "LIVE"})
    r = requests.get(f"{LOCAL}/api/integrations/razorpay/live/readiness", headers=HEADERS, timeout=60)
    assert r.status_code == 200
    body = r.json()
    assert body["overall"] in ("BLOCKED", "WARNING")
    # no live credentials => credentials component BLOCKED, overall must be BLOCKED
    cred = next(c for c in body["components"] if c["component"] == "LIVE credentials configured")
    assert cred["status"] == "BLOCKED"
    assert body["overall"] == "BLOCKED"
    sig = next(c for c in body["components"] if c["component"] == "LIVE webhook signature verification")
    assert sig["status"] == "WARNING"  # implemented+tested but not proven by a genuine live event
    assert body["fail_closed_defaults"]["live_actions_enabled"] is False
    assert body["fail_closed_defaults"]["live_activation"] is False
    r = requests.get(f"{LOCAL}/api/integrations/razorpay/live/readiness", timeout=30)
    assert r.status_code in (401, 403)


# ---------- 9. case filters and sorts ----------
def test_09_case_filters():
    suf, oid, pay1, _ = _ids("p2f")
    try:
        # keep the case un-executed so its verification state is deterministic
        requests.put(f"{LOCAL}/api/settings", json={"auto_execute": False}, headers=HEADERS, timeout=60)
        _post(_pay("payment.failed", pay1, oid, 42420, int(datetime.now(timezone.utc).timestamp())), f"evt_{suf}1")
        r = requests.get(f"{LOCAL}/api/cases", params={"failure": "insufficient", "limit": 5000}, headers=HEADERS, timeout=60)
        assert any(c["order_key"] == oid for c in r.json()["cases"])
        r = requests.get(f"{LOCAL}/api/cases", params={"failure": "card_stolen_xyz", "limit": 5000}, headers=HEADERS, timeout=60)
        assert not any(c["order_key"] == oid for c in r.json()["cases"])
        r = requests.get(f"{LOCAL}/api/cases", params={"verification": "UNVERIFIED", "limit": 5000}, headers=HEADERS, timeout=60)
        assert any(c["order_key"] == oid for c in r.json()["cases"])
        r = requests.get(f"{LOCAL}/api/cases", params={"min_amount": 400, "max_amount": 425, "limit": 5000}, headers=HEADERS, timeout=60)
        assert any(c["order_key"] == oid for c in r.json()["cases"])
        r = requests.get(f"{LOCAL}/api/cases", params={"min_amount": 500, "limit": 5000}, headers=HEADERS, timeout=60)
        assert not any(c["order_key"] == oid for c in r.json()["cases"])
        r = requests.get(f"{LOCAL}/api/cases", params={"sort": "eiv_desc", "limit": 50}, headers=HEADERS, timeout=60)
        assert r.status_code == 200
    finally:
        _cleanup(suf)


# ---------- 10. safety-switch audit events ----------
def test_10_safety_switch_audits():
    try:
        requests.put(f"{LOCAL}/api/settings", json={"emergency_stop": True}, headers=HEADERS, timeout=60)
        requests.put(f"{LOCAL}/api/settings", json={"emergency_stop": False}, headers=HEADERS, timeout=60)
        requests.put(f"{LOCAL}/api/settings", json={"live_actions_enabled": True}, headers=HEADERS, timeout=60)
        requests.put(f"{LOCAL}/api/settings", json={"live_actions_enabled": False}, headers=HEADERS, timeout=60)
        types = {e["event_type"] for e in mdb.audit_events.find(
            {"event_type": {"$in": ["EMERGENCY_STOP_ENABLED", "EMERGENCY_STOP_DISABLED", "LIVE_ACTIONS_ENABLED", "LIVE_ACTIONS_DISABLED"]}},
            {"_id": 0, "event_type": 1}).sort("timestamp", -1).limit(20)}
        assert {"EMERGENCY_STOP_ENABLED", "EMERGENCY_STOP_DISABLED", "LIVE_ACTIONS_ENABLED", "LIVE_ACTIONS_DISABLED"} <= types
    finally:
        requests.put(f"{LOCAL}/api/settings", json={"emergency_stop": False, "live_actions_enabled": False}, headers=HEADERS, timeout=60)


# ---------- 11. observability fields on integration health ----------
def test_11_health_observability_fields():
    r = requests.get(f"{LOCAL}/api/integrations/razorpay/health", headers=HEADERS, timeout=60)
    assert r.status_code == 200
    body = r.json()
    for field in ("recovery_action_failures", "live_action_blocks", "reconciliation_failures", "policy_blocks", "last_sweep"):
        assert field in body, f"missing health field: {field}"

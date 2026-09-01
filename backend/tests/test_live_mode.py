"""Iteration 10 — Phase 2A: LIVE-mode readiness.

Covers (per mandate): mode isolation, credential rejection both ways, secret
masking, activation gate, webhook signature verification, duplicate delivery,
out-of-order precedence, emergency-stop / live-actions gates, TEST<->LIVE
cross-contamination, honest connection test, explicit LIVE audit events, and
LIVE deletion. Runs under the shared guard (dummy TEST config; resend forced
off; LIVE doc + policy settings snapshotted and restored by the guard).

No genuine LIVE credentials exist in this environment — the connection test
must therefore prove it reports an honest ERROR (real Razorpay 401), never a
fabricated CONNECTED.
"""
import hashlib
import hmac
import json
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
TEST_SECRET = "whsec_test_lab_secret_123"          # dummy TEST webhook secret (guard fixture)
LIVE_KEY_ID = "rzp_live_TESTONLY9f8e7d"
LIVE_KEY_SECRET = "live_dummy_secret_not_real"
LIVE_WH_SECRET = "whsec_live_test_secret_9"

pytestmark = pytest.mark.usefixtures("razorpay_integration_guard")


def _save_live():
    r = requests.put(f"{LOCAL}/api/integrations/razorpay/live/config",
                     json={"key_id": LIVE_KEY_ID, "key_secret": LIVE_KEY_SECRET, "webhook_secret": LIVE_WH_SECRET},
                     headers=HEADERS, timeout=60)
    assert r.status_code == 200, r.text[:300]


def _activate_live():
    r = requests.post(f"{LOCAL}/api/integrations/razorpay/live/activate",
                      json={"confirmation": "ACTIVATE LIVE"}, headers=HEADERS, timeout=60)
    assert r.status_code == 200, r.text[:300]


def _live_sign(raw: bytes) -> str:
    return hmac.new(LIVE_WH_SECRET.encode(), raw, hashlib.sha256).hexdigest()


def _test_sign(raw: bytes) -> str:
    return hmac.new(TEST_SECRET.encode(), raw, hashlib.sha256).hexdigest()


def _pay(event, pay, oid, amount_paise, ts):
    entity = {"id": pay, "entity": "payment", "amount": amount_paise, "currency": "INR",
              "order_id": oid, "method": "card", "created_at": ts}
    if event == "payment.failed":
        entity.update({"status": "failed", "error_code": "BAD_REQUEST_ERROR", "error_description": "insufficient_funds"})
    else:
        entity["status"] = "captured"
    return {"entity": "event", "account_id": "acc_live_test", "event": event,
            "payload": {"payment": {"entity": entity}}, "created_at": ts}


def _post_live(payload, eid):
    raw = json.dumps(payload).encode()
    return requests.post(f"{LOCAL}/api/webhooks/razorpay/live", data=raw, timeout=120,
                         headers={"Content-Type": "application/json",
                                  "X-Razorpay-Signature": _live_sign(raw), "x-razorpay-event-id": eid})


def _cleanup(oid):
    case = mdb.recovery_cases.find_one({"order_key": oid}, {"_id": 0, "case_id": 1})
    mdb.recovery_cases.delete_many({"order_key": oid})
    mdb.payment_attempts.delete_many({"order_id": oid})
    mdb.provider_events.delete_many({"normalized_order_id": oid})
    if case:
        mdb.recovery_actions.delete_many({"case_id": case["case_id"]})
        mdb.audit_events.delete_many({"case_id": case["case_id"]})
    mdb.audit_events.delete_many({"event_type": {"$regex": "^LIVE_"}})


# ---------- 1. mode isolation ----------
def test_01_mode_isolation():
    _save_live()
    live = requests.get(f"{LOCAL}/api/integrations/razorpay/live", headers=HEADERS, timeout=60).json()
    assert live["configured"] is True
    assert live["activated"] is False  # activation always resets on credential save
    assert live["live"]["mode"] == "LIVE"
    # TEST side untouched (guard installed the dummy TEST config)
    test_diag = requests.get(f"{LOCAL}/api/integrations/razorpay/diagnostics", headers=HEADERS, timeout=60).json()
    assert test_diag["mode"] == "TEST"
    assert test_diag["key_id_prefix"] == "rzp_test_"
    live_diag = requests.get(f"{LOCAL}/api/integrations/razorpay/live/diagnostics", headers=HEADERS, timeout=60).json()
    assert live_diag["mode"] == "LIVE"
    assert live_diag["key_id_prefix"] == "rzp_live_"


# ---------- 2. credential rejection both ways ----------
def test_02_credential_rejection():
    r = requests.put(f"{LOCAL}/api/integrations/razorpay/live/config",
                     json={"key_id": "rzp_test_ABC123", "key_secret": "x", "webhook_secret": "y"},
                     headers=HEADERS, timeout=60)
    assert r.status_code == 400 and "rejects rzp_test_" in r.json()["detail"]
    r = requests.put(f"{LOCAL}/api/integrations/razorpay/live/config",
                     json={"key_id": "something_else", "key_secret": "x", "webhook_secret": "y"},
                     headers=HEADERS, timeout=60)
    assert r.status_code == 400 and "rzp_live_" in r.json()["detail"]
    # TEST endpoint rejects live credentials
    r = requests.put(f"{LOCAL}/api/integrations/razorpay",
                     json={"key_id": LIVE_KEY_ID, "key_secret": "x", "webhook_secret": "y", "mode": "TEST"},
                     headers=HEADERS, timeout=60)
    assert r.status_code == 400
    # owner-only
    r = requests.put(f"{LOCAL}/api/integrations/razorpay/live/config",
                     json={"key_id": LIVE_KEY_ID, "key_secret": "x", "webhook_secret": "y"}, timeout=60)
    assert r.status_code in (401, 403)


# ---------- 3. secret masking ----------
def test_03_secret_masking():
    _save_live()
    for url in (f"{LOCAL}/api/integrations/razorpay/live/diagnostics", f"{LOCAL}/api/integrations/razorpay/live"):
        r = requests.get(url, headers=HEADERS, timeout=60)
        assert r.status_code == 200
        assert LIVE_KEY_SECRET not in r.text
        assert LIVE_WH_SECRET not in r.text
        assert "key_secret" not in r.json().get("live", {}) if "live" in r.json() else True
    d = requests.get(f"{LOCAL}/api/integrations/razorpay/live/diagnostics", headers=HEADERS, timeout=60).json()
    assert d["key_secret_present"] is True and d["key_secret_length"] == len(LIVE_KEY_SECRET)
    assert d["webhook_secret_present"] is True
    assert d["key_id_is_live"] is True
    # anonymous callers get nothing
    r = requests.get(f"{LOCAL}/api/integrations/razorpay/live/diagnostics", timeout=60)
    assert r.status_code in (401, 403)


# ---------- 4. activation gate ----------
def test_04_activation_gate():
    _save_live()
    payload = _pay("payment.failed", f"pay_lv{uuid.uuid4().hex[:6]}", f"order_lv{uuid.uuid4().hex[:6]}", 50000,
                   int(datetime.now(timezone.utc).timestamp()))
    r = _post_live(payload, f"evt_lv{uuid.uuid4().hex[:6]}")
    assert r.status_code == 403, f"expected 403 before activation, got {r.status_code}"

    r = requests.post(f"{LOCAL}/api/integrations/razorpay/live/activate",
                      json={"confirmation": "activate live"}, headers=HEADERS, timeout=60)
    assert r.status_code == 400  # exact phrase required
    _activate_live()
    live = requests.get(f"{LOCAL}/api/integrations/razorpay/live", headers=HEADERS, timeout=60).json()
    assert live["activated"] is True and live["activated_by"]
    assert mdb.audit_events.count_documents({"event_type": "LIVE_MODE_ACTIVATED"}) >= 1
    _cleanup(payload["payload"]["payment"]["entity"]["order_id"])


# ---------- 5. webhook verification + duplicate delivery ----------
def test_05_live_webhook_verification_and_idempotency():
    _save_live()
    _activate_live()
    suf = uuid.uuid4().hex[:6]
    oid = f"order_lv{suf}"
    try:
        payload = _pay("payment.failed", f"pay_lv{suf}A", oid, 50000, int(datetime.now(timezone.utc).timestamp()))
        raw = json.dumps(payload).encode()
        # bad signature rejected, security event logged
        r = requests.post(f"{LOCAL}/api/webhooks/razorpay/live", data=raw, timeout=60,
                          headers={"Content-Type": "application/json",
                                   "X-Razorpay-Signature": "0" * 64, "x-razorpay-event-id": f"evt_lv{suf}0"})
        assert r.status_code == 401
        assert mdb.security_events.count_documents({"path": "/api/webhooks/razorpay/live"}) >= 1

        # genuine signature accepted and processed as LIVE
        r = _post_live(payload, f"evt_lv{suf}1")
        assert r.status_code == 200, r.text[:300]
        assert r.json()["status"] == "processed" and r.json()["mode"] == "LIVE"
        ev = mdb.provider_events.find_one({"provider_event_id": f"evt_lv{suf}1"}, {"_id": 0})
        assert ev["mode"] == "LIVE" and ev["source"] == "LIVE" and ev["signature_verified"] is True

        # exact replay is an idempotent duplicate — no double processing
        r2 = _post_live(payload, f"evt_lv{suf}1")
        assert r2.json()["duplicate"] is True
        ev = mdb.provider_events.find_one({"provider_event_id": f"evt_lv{suf}1"}, {"_id": 0})
        assert ev["duplicate_deliveries"] == 1
    finally:
        _cleanup(oid)


# ---------- 6. live case source + metric segregation ----------
def test_06_live_case_source_and_metric_segregation():
    _save_live()
    _activate_live()
    suf = uuid.uuid4().hex[:6]
    oid = f"order_lv{suf}"
    try:
        before = requests.get(f"{LOCAL}/api/dashboard/summary", headers=HEADERS, timeout=120).json()
        before_buckets = {s["source"]: s["count"] for s in before["charts"]["sources"]}

        r = _post_live(_pay("payment.failed", f"pay_lv{suf}A", oid, 75000, int(datetime.now(timezone.utc).timestamp())), f"evt_lv{suf}1")
        assert r.json()["result"]["result"] == "case_created", r.text[:300]
        case = mdb.recovery_cases.find_one({"order_key": oid}, {"_id": 0})
        assert case["source"] == "RAZORPAY_LIVE"
        # 2A: no action may have executed for a LIVE case (gate default)
        assert mdb.recovery_actions.count_documents({"case_id": case["case_id"], "executed_time": {"$ne": None}}) == 0

        after = requests.get(f"{LOCAL}/api/dashboard/summary", headers=HEADERS, timeout=120).json()
        after_buckets = {s["source"]: s["count"] for s in after["charts"]["sources"]}
        assert after_buckets.get("LIVE", 0) == before_buckets.get("LIVE", 0) + 1
        assert after_buckets.get("TEST_MODE", 0) == before_buckets.get("TEST_MODE", 0)
    finally:
        _cleanup(oid)


# ---------- 7. out-of-order precedence ----------
def test_07_live_out_of_order_precedence():
    _save_live()
    _activate_live()
    suf = uuid.uuid4().hex[:6]
    oid = f"order_lv{suf}"
    now = int(datetime.now(timezone.utc).timestamp())
    try:
        # settlement arrives BEFORE the failure — precedence-aware engine must not create an at-risk case
        r1 = _post_live(_pay("payment.captured", f"pay_lv{suf}B", oid, 50000, now + 60), f"evt_lv{suf}2")
        assert r1.status_code == 200
        r2 = _post_live(_pay("payment.failed", f"pay_lv{suf}A", oid, 50000, now - 300), f"evt_lv{suf}1")
        assert r2.status_code == 200
        open_cases = mdb.recovery_cases.count_documents({"order_key": oid, "status": {"$in": ["OPEN", "EVALUATED", "ACTION_EXECUTED", "APPROVAL_PENDING", "VERIFYING"]}})
        assert open_cases == 0, "a late failure after settlement must not create an at-risk case"
    finally:
        _cleanup(oid)


# ---------- 8. emergency stop + live-actions gates ----------
def test_08_live_action_gates():
    _save_live()
    _activate_live()
    suf = uuid.uuid4().hex[:6]
    oid = f"order_lv{suf}"
    try:
        _post_live(_pay("payment.failed", f"pay_lv{suf}A", oid, 50000, int(datetime.now(timezone.utc).timestamp())), f"evt_lv{suf}1")
        case = mdb.recovery_cases.find_one({"order_key": oid}, {"_id": 0})
        cid = case["case_id"]

        # (a) emergency stop blocks at the policy layer before any execution
        requests.put(f"{LOCAL}/api/settings", json={"emergency_stop": True}, headers=HEADERS, timeout=60)
        r = requests.post(f"{LOCAL}/api/cases/{cid}/execute", json={"action_type": "SEND_RECOVERY_LINK"}, headers=HEADERS, timeout=120)
        assert r.json()["executed"] is False
        assert mdb.recovery_actions.count_documents({"case_id": cid, "executed_time": {"$ne": None}}) == 0
        requests.put(f"{LOCAL}/api/settings", json={"emergency_stop": False}, headers=HEADERS, timeout=60)

        # (b) LIVE actions disabled by default — execution gate blocks with audit
        r = requests.post(f"{LOCAL}/api/cases/{cid}/execute", json={"action_type": "SEND_RECOVERY_LINK"}, headers=HEADERS, timeout=120)
        body = r.json()
        assert body.get("blocked") == "LIVE_ACTIONS_DISABLED", body
        assert mdb.audit_events.count_documents({"case_id": cid, "event_type": "LIVE_ACTION_BLOCKED"}) >= 1
        assert mdb.recovery_actions.count_documents({"case_id": cid, "executed_time": {"$ne": None}}) == 0

        # (c) explicit owner enablement allows execution (SIMULATED — channel is off)
        requests.put(f"{LOCAL}/api/settings", json={"live_actions_enabled": True}, headers=HEADERS, timeout=60)
        r = requests.post(f"{LOCAL}/api/cases/{cid}/execute", json={"action_type": "SEND_RECOVERY_LINK"}, headers=HEADERS, timeout=120)
        assert r.json()["executed"] is True, r.text[:300]
        action = mdb.recovery_actions.find_one({"case_id": cid, "executed_time": {"$ne": None}}, {"_id": 0})
        assert action["simulated"] is True  # no notification channel in tests
    finally:
        requests.put(f"{LOCAL}/api/settings", json={"emergency_stop": False, "live_actions_enabled": False}, headers=HEADERS, timeout=60)
        _cleanup(oid)


# ---------- 9. TEST<->LIVE cross-contamination ----------
def test_09_cross_contamination():
    _save_live()
    _activate_live()
    suf = uuid.uuid4().hex[:6]
    payload = _pay("payment.failed", f"pay_lv{suf}A", f"order_lv{suf}", 50000, int(datetime.now(timezone.utc).timestamp()))
    raw = json.dumps(payload).encode()
    # TEST-secret signature must NOT verify against the LIVE endpoint
    r = requests.post(f"{LOCAL}/api/webhooks/razorpay/live", data=raw, timeout=60,
                      headers={"Content-Type": "application/json",
                               "X-Razorpay-Signature": _test_sign(raw), "x-razorpay-event-id": f"evt_lv{suf}x"})
    assert r.status_code == 401
    # LIVE-secret signature must NOT verify against the TEST endpoint
    r = requests.post(f"{LOCAL}/api/webhooks/razorpay", data=raw, timeout=60,
                      headers={"Content-Type": "application/json",
                               "X-Razorpay-Signature": _live_sign(raw), "x-razorpay-event-id": f"evt_lv{suf}y"})
    assert r.status_code == 401
    _cleanup(f"order_lv{suf}")


# ---------- 10. honest connection test (no genuine LIVE creds available) ----------
def test_10_live_connection_test_is_honest():
    _save_live()
    r = requests.post(f"{LOCAL}/api/integrations/razorpay/live/test-connection", headers=HEADERS, timeout=120)
    body = r.json()
    # dummy live credentials are genuinely rejected by Razorpay — ERROR, never a fabricated CONNECTED
    assert body["status"] == "ERROR" and "401" in body["detail"]
    diag = requests.get(f"{LOCAL}/api/integrations/razorpay/live/diagnostics", headers=HEADERS, timeout=60).json()
    assert diag["status"] == "ERROR"
    assert mdb.audit_events.count_documents({"event_type": "LIVE_CONNECTION_TEST_FAILED"}) >= 1


# ---------- 11. explicit LIVE audit trail ----------
def test_11_live_audit_events_present():
    _save_live()
    _activate_live()
    suf = uuid.uuid4().hex[:6]
    oid = f"order_lv{suf}"
    try:
        # produce LIVE_EVENT_PROCESSED via a genuine signed live webhook
        _post_live(_pay("payment.failed", f"pay_lv{suf}A", oid, 50000, int(datetime.now(timezone.utc).timestamp())), f"evt_lv{suf}1")
        # produce LIVE_ACTION_BLOCKED (LIVE actions disabled by default)
        case = mdb.recovery_cases.find_one({"order_key": oid}, {"_id": 0})
        requests.post(f"{LOCAL}/api/cases/{case['case_id']}/execute",
                      json={"action_type": "SEND_RECOVERY_LINK"}, headers=HEADERS, timeout=120)
        found = {e["event_type"] for e in mdb.audit_events.find({"event_type": {"$regex": "^LIVE_"}}, {"_id": 0, "event_type": 1})}
        assert "LIVE_CREDENTIALS_UPDATED" in found
        assert "LIVE_MODE_ACTIVATED" in found
        assert "LIVE_CONNECTION_TEST_FAILED" in found
        assert "LIVE_EVENT_PROCESSED" in found
        assert "LIVE_ACTION_BLOCKED" in found
    finally:
        _cleanup(oid)


# ---------- 12. deletion ----------
def test_12_live_delete():
    _save_live()
    r = requests.delete(f"{LOCAL}/api/integrations/razorpay/live", headers=HEADERS, timeout=60)
    assert r.status_code == 200
    live = requests.get(f"{LOCAL}/api/integrations/razorpay/live", headers=HEADERS, timeout=60).json()
    assert live["configured"] is False and live["activated"] is False
    assert mdb.audit_events.count_documents({"event_type": "LIVE_CREDENTIALS_REMOVED"}) >= 1
    # TEST side still intact
    test_diag = requests.get(f"{LOCAL}/api/integrations/razorpay/diagnostics", headers=HEADERS, timeout=60).json()
    assert test_diag["mode"] == "TEST" and test_diag["key_id_prefix"] == "rzp_test_"
    mdb.audit_events.delete_many({"event_type": {"$regex": "^LIVE_"}})

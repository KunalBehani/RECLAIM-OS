"""Iteration 9 — Phase 1.5: genuine customer notification channel (Resend).

Mandated coverage:
 1. notification adapter unit behavior (mocked transport)
 2. secret masking (adapter diagnostics + endpoint)
 3. duplicate notification protection
 4. same-order attribution wiring (tokenized retry page + signature-verified completion)
 5. natural recovery remains attribution NONE
 6. simulated execution remains non-attributable
 7. real notification + same-order settlement => VERIFIED_RECOVERED / STRONG
 8. duplicate captured webhook cannot double-count recovery
 9. partial settlement attribution and amounts

Webhook parts use the guard fixture (dummy razorpay config + shared test
secret). Real email sends go only to delivered@resend.dev — the sink address
sanctioned by the email playbook for integration testing.
"""
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timezone
from unittest import mock

import pytest
import requests
from dotenv import dotenv_values
from pymongo import MongoClient

_be = dotenv_values("/app/backend/.env")
_fe = dotenv_values("/app/frontend/.env")
mdb = MongoClient(_be["MONGO_URL"])[_be["DB_NAME"]]

# Adapter reads env at import time — ensure it is populated before any import.
os.environ.setdefault("EMERGENT_EMAIL_KEY", _be.get("EMERGENT_EMAIL_KEY") or "")
os.environ.setdefault("EMAIL_FROM_NAME", _be.get("EMAIL_FROM_NAME") or "RECLAIM OS")
os.environ.setdefault("PUBLIC_APP_URL", _be.get("PUBLIC_APP_URL") or "")

LOCAL = "http://localhost:8001"
SESSION = "test_session_smoke_1787904424204"
HEADERS = {"Authorization": f"Bearer test_session_smoke_1787904424204"}
TEST_SECRET = "whsec_test_lab_secret_123"
DUMMY_KEY_SECRET = "dummy_secret_not_real"
SINK = "delivered@resend.dev"

pytestmark = pytest.mark.usefixtures("razorpay_integration_guard")


@pytest.fixture(scope="module", autouse=True)
def _resend_config_guard():
    """Snapshot/restore the resend config so the suite never changes the
    operator's real channel state."""
    saved = mdb.integrations.find_one({"provider": "resend"})
    yield
    if saved is None:
        mdb.integrations.delete_many({"provider": "resend"})
    else:
        saved.pop("_id", None)
        mdb.integrations.replace_one({"provider": "resend"}, saved, upsert=True)


def _set_resend(enabled: bool):
    r = requests.put(f"{LOCAL}/api/integrations/resend/config", json={"enabled": enabled}, headers=HEADERS, timeout=60)
    assert r.status_code == 200, r.text[:300]


def _ids(tag):
    suf = f"{tag}{uuid.uuid4().hex[:6]}"
    return suf, f"order_{suf}", f"pay_{suf}A", f"pay_{suf}B"


def _sign(raw: bytes) -> str:
    return hmac.new(TEST_SECRET.encode(), raw, hashlib.sha256).hexdigest()


def _pay(event, pay, oid, amount_paise, ts, email=SINK):
    entity = {"id": pay, "entity": "payment", "amount": amount_paise, "currency": "INR",
              "order_id": oid, "method": "card", "created_at": ts, "email": email}
    if event == "payment.failed":
        entity.update({"status": "failed", "error_code": "BAD_REQUEST_ERROR", "error_description": "insufficient_funds"})
    else:
        entity["status"] = "captured"
    return {"entity": "event", "account_id": "acc_test", "event": event,
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


def _ensure_executed_action(case):
    """Autopilot may already have executed; otherwise execute manually. Returns the executed action."""
    actions = list(mdb.recovery_actions.find({"case_id": case["case_id"], "executed_time": {"$ne": None}}, {"_id": 0}))
    if actions:
        return actions[-1]
    r = requests.post(f"{LOCAL}/api/cases/{case['case_id']}/execute",
                      json={"action_type": "SEND_RECOVERY_LINK"}, headers=HEADERS, timeout=120)
    assert r.status_code == 200, r.text[:300]
    actions = list(mdb.recovery_actions.find({"case_id": case["case_id"], "executed_time": {"$ne": None}}, {"_id": 0}))
    assert actions, f"no executed action after manual execute: {r.text[:300]}"
    return actions[-1]


# ---------- 1. adapter unit behavior (mocked transport, no network) ----------
def test_01_adapter_send_unit():
    from notifications.resend_adapter import ResendNotificationAdapter

    captured = {}

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "em_test123"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            captured.update({"url": url, "headers": headers, "json": json})
            return FakeResp()

    with mock.patch("notifications.resend_adapter.httpx.AsyncClient", return_value=FakeClient()):
        import asyncio
        result = asyncio.run(ResendNotificationAdapter().send_recovery_email(
            recipient=SINK, subject="Unit test", html="<p>hello</p>"))
    assert result.status == "SENT" and result.provider_reference == "em_test123"
    assert captured["url"] == "https://integrations.emergentagent.com/api/v1/email/send"
    assert captured["json"]["to"] == [SINK]
    assert captured["json"]["from_name"] == "RECLAIM OS"
    assert "X-Email-Key" in captured["headers"]


def test_01b_adapter_failure_is_sanitized():
    import asyncio
    from notifications.base import NotificationError
    from notifications.resend_adapter import ResendNotificationAdapter

    class BoomClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            raise ConnectionError("simulated transport failure")

    with mock.patch("notifications.resend_adapter.httpx.AsyncClient", return_value=BoomClient()):
        with pytest.raises(NotificationError) as exc:
            asyncio.run(ResendNotificationAdapter().send_recovery_email(recipient=SINK, subject="s", html="<p>x</p>"))
    key = os.environ.get("EMERGENT_EMAIL_KEY", "")
    assert key and key not in str(exc.value)
    assert "connection error" in str(exc.value)


def test_01c_guardrail_gate_blocks_forms_and_foreign_anchors():
    from notifications.resend_adapter import _assert_safe_email
    with pytest.raises(ValueError):
        _assert_safe_email("s", '<form><input name="card"/></form>')
    with pytest.raises(ValueError):
        _assert_safe_email("s", '<a href="https://evil.example.com">razorpay.com</a>')
    with pytest.raises(ValueError):
        _assert_safe_email("s", '<a href="http://insecure.example.com">pay</a>')
    _assert_safe_email("s", '<a href="https://reclaim-verify.preview.emergentagent.com/pay/x">Complete your payment</a>')


# ---------- 2. secret masking ----------
def test_02_secret_masking():
    from notifications.base import mask_email
    from notifications.resend_adapter import masked_diagnostics

    key = os.environ.get("EMERGENT_EMAIL_KEY", "")
    d = masked_diagnostics(True)
    assert key and key not in json.dumps(d)
    assert d["api_key_present"] is True
    assert d["api_key_length"] == len(key)
    assert d["api_key_prefix"] == key[:3]
    assert mask_email("customer@example.com") == "cu***@example.com"

    r = requests.get(f"{LOCAL}/api/integrations/resend/diagnostics", headers=HEADERS, timeout=60)
    assert r.status_code == 200
    assert key not in r.text
    r = requests.get(f"{LOCAL}/api/integrations/resend/diagnostics", timeout=60)
    assert r.status_code in (401, 403)


# ---------- 3. duplicate notification protection ----------
def test_03_duplicate_notification_protection():
    _set_resend(True)
    suf, oid, pay1, _ = _ids("n3")
    try:
        now = int(datetime.now(timezone.utc).timestamp())
        r = _post(_pay("payment.failed", pay1, oid, 50000, now - 300), f"evt_{suf}1")
        assert r.json()["result"]["result"] == "case_created", r.text[:300]
        case = _case(oid)
        first = _ensure_executed_action(case)
        assert first["simulated"] is False and first["execution_mode"] == "REAL"
        assert first["notification"]["status"] == "SENT" and first["notification"]["email_id"]
        assert first["recovery_token"].startswith("rct_")

        # second manual execute must be blocked (policy engine / idempotency) — no second send
        r2 = requests.post(f"{LOCAL}/api/cases/{case['case_id']}/execute",
                           json={"action_type": "SEND_RECOVERY_LINK"}, headers=HEADERS, timeout=120)
        assert r2.status_code == 200, r2.text[:300]
        body2 = r2.json()
        assert body2.get("executed") is False or body2.get("duplicate") is True, f"second execute was not blocked: {body2}"
        executed = list(mdb.recovery_actions.find({"case_id": case["case_id"], "executed_time": {"$ne": None}}))
        assert len(executed) == 1, f"expected exactly 1 executed action, got {len(executed)}"
        sent = [a for a in executed if (a.get("notification") or {}).get("status") == "SENT"]
        assert len(sent) == 1, "exactly one genuine email may be sent per case+action"
    finally:
        _cleanup(suf)


# ---------- 4 + 7. real notification + same-order settlement => STRONG ----------
def test_04_real_notification_same_order_strong_attribution():
    _set_resend(True)
    suf, oid, pay1, pay2 = _ids("n4")
    now = int(datetime.now(timezone.utc).timestamp())
    try:
        _post(_pay("payment.failed", pay1, oid, 50000, now - 300), f"evt_{suf}1")
        case = _case(oid)
        action = _ensure_executed_action(case)
        assert action["simulated"] is False, "expected REAL execution with notifications enabled"
        token = action["recovery_token"]

        # public launch config — same order, public key only
        r = requests.get(f"{LOCAL}/api/recovery/pay/{token}", timeout=60)
        assert r.status_code == 200, r.text[:300]
        cfg = r.json()
        assert cfg["order_id"] == oid and cfg["amount_paise"] == 50000 and cfg["currency"] == "INR"
        assert cfg["key_id"].startswith("rzp_test_")
        assert "key_secret" not in r.text

        # invalid checkout signature is rejected; nothing gets linked
        bad = requests.post(f"{LOCAL}/api/recovery/pay/{token}/complete",
                            json={"razorpay_payment_id": pay2, "razorpay_order_id": oid,
                                  "razorpay_signature": "0" * 64}, timeout=60)
        assert bad.status_code == 400

        # genuine checkout signature links the payment to the action (idempotent)
        sig = hmac.new(DUMMY_KEY_SECRET.encode(), f"{oid}|{pay2}".encode(), hashlib.sha256).hexdigest()
        ok = requests.post(f"{LOCAL}/api/recovery/pay/{token}/complete",
                           json={"razorpay_payment_id": pay2, "razorpay_order_id": oid,
                                 "razorpay_signature": sig}, timeout=60)
        assert ok.status_code == 200 and ok.json()["linked"] is True
        dup = requests.post(f"{LOCAL}/api/recovery/pay/{token}/complete",
                            json={"razorpay_payment_id": pay2, "razorpay_order_id": oid,
                                  "razorpay_signature": sig}, timeout=60)
        assert dup.json().get("duplicate") is True

        # genuine signed payment.captured for the SAME order closes STRONG
        r2 = _post(_pay("payment.captured", pay2, oid, 50000, now + 60), f"evt_{suf}2")
        assert r2.status_code == 200, r2.text[:300]
        case = _case(oid)
        assert case["status"] == "VERIFIED_RECOVERED", case["status"]
        assert case["attribution_strength"] == "STRONG", case.get("attribution_strength")
        assert case["attributed_action"] == "SEND_RECOVERY_LINK"
        assert case["recovered_amount"] == 500.0
    finally:
        _cleanup(suf)


# ---------- 5. natural recovery remains attribution NONE ----------
def test_05_natural_recovery_stays_none():
    _set_resend(False)
    suf, oid, pay1, pay2 = _ids("n5")
    now = int(datetime.now(timezone.utc).timestamp())
    try:
        _post(_pay("payment.failed", pay1, oid, 50000, now - 300), f"evt_{suf}1")
        _post(_pay("payment.captured", pay2, oid, 50000, now + 60), f"evt_{suf}2")
        case = _case(oid)
        assert case["status"] == "NATURALLY_RECOVERED", case["status"]
        assert case["attribution_strength"] == "NONE"
        assert case["recovered_amount"] == 0.0
        assert case["natural_recovered_amount"] == 500.0
    finally:
        _cleanup(suf)


# ---------- 6. simulated execution remains non-attributable ----------
def test_06_simulated_execution_non_attributable():
    _set_resend(False)
    suf, oid, pay1, pay2 = _ids("n6")
    now = int(datetime.now(timezone.utc).timestamp())
    try:
        _post(_pay("payment.failed", pay1, oid, 50000, now - 300), f"evt_{suf}1")
        case = _case(oid)
        action = _ensure_executed_action(case)
        assert action["simulated"] is True, "with notifications disabled execution must stay SIMULATED"
        _post(_pay("payment.captured", pay2, oid, 50000, now + 60), f"evt_{suf}2")
        case = _case(oid)
        assert case["status"] == "NATURALLY_RECOVERED", case["status"]
        assert case["attribution_strength"] == "NONE"
        assert case["recovered_amount"] == 0.0
    finally:
        _cleanup(suf)


# ---------- 8. duplicate captured webhook cannot double-count ----------
def test_08_duplicate_captured_webhook_no_double_count():
    _set_resend(True)
    suf, oid, pay1, pay2 = _ids("n8")
    now = int(datetime.now(timezone.utc).timestamp())
    try:
        _post(_pay("payment.failed", pay1, oid, 50000, now - 300), f"evt_{suf}1")
        case = _case(oid)
        action = _ensure_executed_action(case)
        sig = hmac.new(DUMMY_KEY_SECRET.encode(), f"{oid}|{pay2}".encode(), hashlib.sha256).hexdigest()
        requests.post(f"{LOCAL}/api/recovery/pay/{action['recovery_token']}/complete",
                      json={"razorpay_payment_id": pay2, "razorpay_order_id": oid, "razorpay_signature": sig}, timeout=60)
        payload = _pay("payment.captured", pay2, oid, 50000, now + 60)
        _post(payload, f"evt_{suf}2")
        case = _case(oid)
        assert case["status"] == "VERIFIED_RECOVERED" and case["recovered_amount"] == 500.0

        # exact replay (same event id) and same payment with a new event id
        _post(payload, f"evt_{suf}2")
        _post(payload, f"evt_{suf}3")
        case = _case(oid)
        assert case["recovered_amount"] == 500.0, case["recovered_amount"]
        assert case["status"] == "VERIFIED_RECOVERED"
        closes = mdb.audit_events.count_documents({"case_id": case["case_id"], "event_type": "CASE_CLOSED"})
        assert closes == 1, f"expected exactly 1 CASE_CLOSED, got {closes}"
        assert mdb.recovery_cases.count_documents({"order_key": oid}) == 1
    finally:
        _cleanup(suf)


# ---------- 9. partial settlement attribution and amounts ----------
def test_09_partial_settlement_strong_attribution():
    _set_resend(True)
    suf, oid, pay1, pay2 = _ids("n9")
    now = int(datetime.now(timezone.utc).timestamp())
    try:
        _post(_pay("payment.failed", pay1, oid, 50000, now - 300), f"evt_{suf}1")
        case = _case(oid)
        action = _ensure_executed_action(case)
        sig = hmac.new(DUMMY_KEY_SECRET.encode(), f"{oid}|{pay2}".encode(), hashlib.sha256).hexdigest()
        requests.post(f"{LOCAL}/api/recovery/pay/{action['recovery_token']}/complete",
                      json={"razorpay_payment_id": pay2, "razorpay_order_id": oid, "razorpay_signature": sig}, timeout=60)
        _post(_pay("payment.captured", pay2, oid, 30000, now + 60), f"evt_{suf}2")
        case = _case(oid)
        assert case["status"] == "VERIFIED_RECOVERED", case["status"]
        assert case["outcome"] == "PARTIALLY_RECOVERED", case.get("outcome")
        assert case["attribution_strength"] == "STRONG"
        assert case["recovered_amount"] == 300.0, case["recovered_amount"]
    finally:
        _cleanup(suf)

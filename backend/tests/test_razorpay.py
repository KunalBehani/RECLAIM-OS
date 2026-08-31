"""Razorpay TEST MODE pipeline tests (spec §28, cases A-T).

Webhook tests POST genuinely-signed Razorpay-format payloads to the real
endpoint on the live local server. Engine-level tests use motor directly.
"""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from unittest import mock

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LOCAL = "http://localhost:8001"
TEST_SECRET = "whsec_test_lab_secret_123"

LOOP = asyncio.new_event_loop()
# Motor futures bind to the thread's current default loop at call time;
# pin LOOP as default so dedicated-client futures always belong to LOOP.
asyncio.set_event_loop(LOOP)


_OWN_CLIENT = None


def _db():
    """Dedicated Motor client for this module: the shared `database.db` client
    binds to whichever event loop touches it first in the worker process, which
    conflicts with this module's LOOP under xdist scheduling."""
    global _OWN_CLIENT
    if _OWN_CLIENT is None:
        from dotenv import dotenv_values
        from motor.motor_asyncio import AsyncIOMotorClient

        _be = dotenv_values("/app/backend/.env")
        _OWN_CLIENT = AsyncIOMotorClient(os.environ.get("MONGO_URL") or _be.get("MONGO_URL"))
        _OWN_DB = os.environ.get("DB_NAME") or _be.get("DB_NAME")
        _OWN_CLIENT = _OWN_CLIENT[_OWN_DB]
    return _OWN_CLIENT


def _run(coro):
    return LOOP.run_until_complete(coro)


pytestmark = pytest.mark.usefixtures("razorpay_integration_guard")


def _pay_payload(event, pid, oid, amount_paise, ts, code=None, method="card"):
    entity = {
        "id": pid, "entity": "payment", "amount": amount_paise, "currency": "INR",
        "status": {"payment.failed": "failed", "payment.captured": "captured", "payment.authorized": "authorized"}[event],
        "order_id": oid, "method": method, "created_at": ts, "email": "test@example.com",
    }
    if code:
        entity["error_code"] = code
        entity["error_description"] = code.replace("_", " ")
    return {"entity": "event", "account_id": "acc_TEST", "event": event,
            "payload": {"payment": {"entity": entity}}, "created_at": ts}


def _order_payload(oid, amount_paise, ts):
    return {"entity": "event", "account_id": "acc_TEST", "event": "order.paid",
            "payload": {"order": {"entity": {"id": oid, "entity": "order", "amount": amount_paise,
                                             "amount_paid": amount_paise, "amount_due": 0,
                                             "currency": "INR", "status": "paid",
                                             "receipt": f"rcpt_{oid[-6:]}", "created_at": ts}}},
            "created_at": ts}


def _post(payload, event_id, valid=True, raw_override=None):
    raw = raw_override if raw_override is not None else json.dumps(payload).encode()
    sig = hmac.new(TEST_SECRET.encode(), raw, hashlib.sha256).hexdigest() if valid else "0" * 64
    return requests.post(
        f"{LOCAL}/api/webhooks/razorpay", data=raw, timeout=180,
        headers={"Content-Type": "application/json", "X-Razorpay-Signature": sig, "x-razorpay-event-id": event_id},
    )


def _case_for(order_key):
    return _run(_db().recovery_cases.find_one({"order_key": order_key}, {"_id": 0}))


def _attempt(pid):
    return _run(_db().payment_attempts.find_one({"payment_id": pid}, {"_id": 0}))


def _provider_event(eid):
    return _run(_db().provider_events.find_one({"provider_event_id": eid}, {"_id": 0}))


def _clear_actions(case_id):
    _run(_db().recovery_actions.delete_many({"case_id": case_id}))


def _cleanup(prefix):
    async def _run_cleanup():
        db = _db()
        cases = await db.recovery_cases.find({"order_key": {"$regex": f"^order_{prefix}"}}, {"case_id": 1}).to_list(100)
        case_ids = [c["case_id"] for c in cases]
        await db.payment_attempts.delete_many({"$or": [{"payment_id": {"$regex": f"^pay_{prefix}"}}, {"order_id": {"$regex": f"^order_{prefix}"}}]})
        await db.recovery_cases.delete_many({"order_key": {"$regex": f"^order_{prefix}"}})
        await db.orders.delete_many({"order_id": {"$regex": f"^order_{prefix}"}})
        await db.provider_events.delete_many({"provider_event_id": {"$regex": f"^evt_{prefix}"}})
        if case_ids:
            await db.recovery_actions.delete_many({"case_id": {"$in": case_ids}})
    _run(_run_cleanup())


def _ids(tag):
    suf = f"{tag}{uuid.uuid4().hex[:6]}"
    return suf, f"order_{suf}", f"pay_{suf}A", f"pay_{suf}B"


# A. Valid webhook accepted, stored, normalized, processed
def test_a_valid_webhook():
    suf, oid, pay1, _ = _ids("ta")
    r = _post(_pay_payload("payment.failed", pay1, oid, 500000, int(datetime.now(timezone.utc).timestamp()), "insufficient_funds"), f"evt_{suf}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "processed"
    assert body["mode"] == "TEST"
    event = _provider_event(f"evt_{suf}")
    assert event["signature_verified"] is True
    assert event["processing_status"] == "PROCESSED"
    assert event["source"] == "TEST_MODE"
    assert event["normalized_order_id"] == oid
    _cleanup(suf)


# B. Invalid signature rejected with security event, zero state change
def test_b_invalid_signature():
    suf, oid, pay1, _ = _ids("tb")
    before = _run(_db().payment_attempts.count_documents({"payment_id": pay1}))
    r = _post(_pay_payload("payment.failed", pay1, oid, 500000, int(datetime.now(timezone.utc).timestamp())), f"evt_{suf}", valid=False)
    assert r.status_code == 401
    after = _run(_db().payment_attempts.count_documents({"payment_id": pay1}))
    assert before == after == 0
    sec = _run(_db().security_events.find_one({"path": "/api/webhooks/razorpay"}, sort=[("received_at", -1)]))
    assert sec is not None
    assert _provider_event(f"evt_{suf}") is None  # never persisted


# C. Duplicate delivery — second is recognized, no duplicates created
def test_c_duplicate_event():
    suf, oid, pay1, _ = _ids("tc")
    payload = _pay_payload("payment.failed", pay1, oid, 300000, int(datetime.now(timezone.utc).timestamp()), "do_not_honor")
    r1 = _post(payload, f"evt_{suf}")
    r2 = _post(payload, f"evt_{suf}")
    assert r1.status_code == 200 and r1.json()["status"] == "processed"
    assert r2.status_code == 200 and r2.json()["status"] == "duplicate"
    assert _run(_db().payment_attempts.count_documents({"payment_id": pay1})) == 1
    assert _run(_db().recovery_cases.count_documents({"order_key": oid})) == 1
    assert _provider_event(f"evt_{suf}")["duplicate_deliveries"] == 1
    _cleanup(suf)


# D. Triple duplicate
def test_d_triple_duplicate():
    suf, oid, pay1, _ = _ids("td")
    payload = _pay_payload("payment.failed", pay1, oid, 300000, int(datetime.now(timezone.utc).timestamp()), "do_not_honor")
    for _ in range(3):
        _post(payload, f"evt_{suf}")
    assert _run(_db().payment_attempts.count_documents({"payment_id": pay1})) == 1
    assert _run(_db().recovery_cases.count_documents({"order_key": oid})) == 1
    assert _provider_event(f"evt_{suf}")["duplicate_deliveries"] == 2
    _cleanup(suf)


# E. Failed payment creates a genuine risk candidate with stored evidence
def test_e_failed_payment_risk_case():
    suf, oid, pay1, _ = _ids("te")
    r = _post(_pay_payload("payment.failed", pay1, oid, 675000, int(datetime.now(timezone.utc).timestamp()), "insufficient_funds"), f"evt_{suf}")
    assert r.json()["result"]["result"] == "case_created"
    case = _case_for(oid)
    assert case is not None
    assert case["source"] == "RAZORPAY_TEST"
    assert case["simulated"] is False
    assert case["title"] == f"Failed Payment for Order {oid}"
    evidence = case["risk_evidence"]
    assert evidence["payment_failed"] is True
    assert evidence["failure_code"] == "insufficient_funds"
    assert evidence["order_amount"] == 6750.0
    assert evidence["successful_payment_found"] is False
    audit = _run(_db().audit_events.find({"case_id": case["case_id"]}, {"_id": 0}).to_list(50))
    assert any(e["event_type"] == "CASE_CREATED" for e in audit)
    _cleanup(suf)


# F. Failed -> captured (no intervention) => NATURAL recovery, never RECLAIM-attributed
def test_f_natural_recovery_not_attributed():
    suf, oid, pay1, pay2 = _ids("tf")
    now = int(datetime.now(timezone.utc).timestamp())
    _post(_pay_payload("payment.failed", pay1, oid, 400000, now - 300, "insufficient_funds"), f"evt_{suf}1")
    case = _case_for(oid)
    _clear_actions(case["case_id"])  # guarantee zero intervention before settlement
    r2 = _post(_pay_payload("payment.captured", pay2, oid, 400000, now), f"evt_{suf}2")
    assert r2.json()["result"]["result"] == "closed_natural"
    case = _case_for(oid)
    assert case["status"] == "NATURALLY_RECOVERED"
    assert case["attribution_strength"] == "NONE"
    assert case["recovered_amount"] == 0.0
    assert case["natural_recovered_amount"] == 4000.0
    _cleanup(suf)


# G. Failed -> order.paid closes the case
def test_g_failed_then_order_paid():
    suf, oid, pay1, _ = _ids("tg")
    now = int(datetime.now(timezone.utc).timestamp())
    _post(_pay_payload("payment.failed", pay1, oid, 250000, now - 300, "do_not_honor"), f"evt_{suf}1")
    case = _case_for(oid)
    _clear_actions(case["case_id"])
    _post(_order_payload(oid, 250000, now), f"evt_{suf}2")
    case = _case_for(oid)
    assert case["status"] in ("NATURALLY_RECOVERED", "VERIFIED_RECOVERED")
    assert case["verification_status"] == "VERIFIED"
    order = _run(_db().orders.find_one({"order_id": oid}, {"_id": 0}))
    assert order["status"] == "paid"
    _cleanup(suf)


# H. payment.captured BEFORE payment.authorized — no downgrade, final state success
def test_h_captured_before_authorized():
    suf, oid, pay1, _ = _ids("th")
    now = int(datetime.now(timezone.utc).timestamp())
    _post(_pay_payload("payment.captured", pay1, oid, 150000, now), f"evt_{suf}1")
    _post(_pay_payload("payment.authorized", pay1, oid, 150000, now - 300), f"evt_{suf}2")
    attempt = _attempt(pay1)
    assert attempt["status"] == "success"
    assert _run(_db().recovery_cases.count_documents({"order_key": oid})) == 0
    _cleanup(suf)


# I. order.paid before its payment event — reconcile correctly, no case
def test_i_order_paid_before_payment():
    suf, oid, pay1, _ = _ids("ti")
    now = int(datetime.now(timezone.utc).timestamp())
    _post(_order_payload(oid, 150000, now), f"evt_{suf}1")
    r2 = _post(_pay_payload("payment.captured", pay1, oid, 150000, now), f"evt_{suf}2")
    assert r2.status_code == 200
    assert _run(_db().recovery_cases.count_documents({"order_key": oid})) == 0
    order = _run(_db().orders.find_one({"order_id": oid}, {"_id": 0}))
    assert order["status"] == "paid"
    _cleanup(suf)


# J. Failed with no replacement stays at risk
def test_j_failed_no_replacement():
    suf, oid, pay1, _ = _ids("tj")
    _post(_pay_payload("payment.failed", pay1, oid, 900000, int(datetime.now(timezone.utc).timestamp()), "insufficient_funds"), f"evt_{suf}")
    case = _case_for(oid)
    assert case["status"] in ("OPEN", "EVALUATED", "APPROVAL_PENDING", "ACTION_EXECUTED")
    assert case["outcome"] == "PENDING"
    _cleanup(suf)


# K. Multiple failed attempts — one case, no double counting
def test_k_multiple_failures_one_case():
    suf, oid, pay1, pay2 = _ids("tk")
    now = int(datetime.now(timezone.utc).timestamp())
    _post(_pay_payload("payment.failed", pay1, oid, 100000, now - 600, "insufficient_funds"), f"evt_{suf}1")
    r2 = _post(_pay_payload("payment.failed", pay2, oid, 100000, now, "insufficient_funds"), f"evt_{suf}2")
    assert r2.json()["result"]["result"] == "case_updated"
    assert _run(_db().recovery_cases.count_documents({"order_key": oid})) == 1
    case = _case_for(oid)
    assert set(case["payment_attempt_ids"]) == {pay1, pay2}
    _cleanup(suf)


# L. Failed then successful replacement (different payment) — closed
def test_l_replacement_success():
    suf, oid, pay1, pay2 = _ids("tl")
    now = int(datetime.now(timezone.utc).timestamp())
    _post(_pay_payload("payment.failed", pay1, oid, 199900, now - 300, "expired_card"), f"evt_{suf}1")
    case = _case_for(oid)
    _clear_actions(case["case_id"])
    _post(_pay_payload("payment.captured", pay2, oid, 199900, now, method="upi"), f"evt_{suf}2")
    case = _case_for(oid)
    assert case["status"] == "NATURALLY_RECOVERED"
    assert case["verification_status"] == "VERIFIED"
    _cleanup(suf)


# M. Partial payment after intervention => VERIFIED_RECOVERED + PARTIALLY_RECOVERED
def test_m_partial_payment():
    suf, oid, pay1, pay2 = _ids("tm")
    now = int(datetime.now(timezone.utc).timestamp())
    _post(_pay_payload("payment.failed", pay1, oid, 800000, now - 300, "insufficient_funds"), f"evt_{suf}1")
    case = _case_for(oid)
    _clear_actions(case["case_id"])

    async def _insert_action():
        await _db().recovery_actions.insert_one({
            "action_id": f"act_{uuid.uuid4().hex[:12]}", "case_id": case["case_id"],
            "action_type": "SEND_RECOVERY_LINK", "label": "Send Payment Recovery Link",
            "scheduled_time": datetime.now(timezone.utc).isoformat(),
            "executed_time": datetime.now(timezone.utc).isoformat(),
            "execution_mode": "SIMULATED", "simulated": True, "approval_status": "AUTO_APPROVED",
            "policy_result": "ALLOW", "expected_incremental_value": 0, "estimated_cost": 12.0,
            "outcome": "PENDING", "idempotency_key": f"{case['case_id']}:SEND_RECOVERY_LINK:test",
            "provider_reference": "SIM-TEST", "created_at": datetime.now(timezone.utc).isoformat(),
        })
    _run(_insert_action())
    settled = int(datetime.now(timezone.utc).timestamp()) + 1
    _post(_pay_payload("payment.captured", pay2, oid, 500000, settled), f"evt_{suf}2")
    case = _case_for(oid)
    assert case["status"] == "VERIFIED_RECOVERED"
    assert case["outcome"] == "PARTIALLY_RECOVERED"
    assert case["recovered_amount"] == 5000.0
    assert case["attribution_strength"] == "MODERATE"
    _cleanup(suf)


# N. Multiple successful payments — recovered once, capped at amount at risk
def test_n_multiple_successes():
    suf, oid, pay1, pay2 = _ids("tn")
    pay3 = f"pay_{suf}C"
    now = int(datetime.now(timezone.utc).timestamp())
    _post(_pay_payload("payment.failed", pay1, oid, 200000, now - 300, "do_not_honor"), f"evt_{suf}1")
    case = _case_for(oid)
    _clear_actions(case["case_id"])
    _post(_pay_payload("payment.captured", pay2, oid, 200000, now), f"evt_{suf}2")
    r3 = _post(_pay_payload("payment.captured", pay3, oid, 200000, now), f"evt_{suf}3")
    assert r3.json()["result"]["result"] == "payment_recorded"
    case = _case_for(oid)
    assert case["status"] == "NATURALLY_RECOVERED"
    assert case["natural_recovered_amount"] == 2000.0  # capped at amount at risk
    _cleanup(suf)


# O. Unknown event type — accepted, stored, ignored
def test_o_unknown_event():
    suf, oid, pay1, _ = _ids("to")
    payload = _pay_payload("payment.failed", pay1, oid, 100000, int(datetime.now(timezone.utc).timestamp()))
    payload["event"] = "payment.dispute.created"
    r = _post(payload, f"evt_{suf}")
    assert r.status_code == 200
    assert r.json()["status"] == "ignored_unsupported"
    assert _provider_event(f"evt_{suf}")["processing_status"] == "IGNORED_UNSUPPORTED"
    assert _run(_db().payment_attempts.count_documents({"payment_id": pay1})) == 0
    _cleanup(suf)


# P. Malformed payload with valid signature -> 400, nothing stored
def test_p_malformed_payload():
    suf = f"tp{uuid.uuid4().hex[:6]}"
    raw = b'{"entity":"event","event":"payment.failed",BROKEN'
    r = _post(None, f"evt_{suf}", raw_override=raw)
    assert r.status_code == 400
    assert _provider_event(f"evt_{suf}") is None


# Q. Provider API timeout handled as a sanitized IntegrationError
def test_q_provider_timeout():
    from providers.base import IntegrationError
    from providers.razorpay_adapter import RazorpayAdapter

    adapter = RazorpayAdapter({"key_id": "rzp_test_x", "key_secret": "y", "mode": "TEST"})
    with mock.patch("providers.razorpay_adapter.requests.request", side_effect=requests.Timeout()):
        with pytest.raises(IntegrationError, match="timeout"):
            adapter.fetch_order("order_x")


# R. Retry safety — processing the same normalized attempt twice never duplicates
def test_r_retry_idempotent():
    from detection import process_payment_attempt

    suf, oid, pay1, _ = _ids("tr")

    def attempt():
        return {
            "payment_id": pay1, "order_id": oid, "invoice_id": None, "customer_reference": None,
            "amount": 1000.0, "currency": "INR", "status": "failed", "failure_code": "do_not_honor",
            "failure_reason": None, "payment_method": "card",
            "timestamp": datetime.now(timezone.utc).isoformat(), "source": "RAZORPAY_TEST",
            "source_event_id": None, "simulated": False, "ingestion_confidence": 1.0,
            "raw_data_reference": "test", "batch_id": None, "ingested_at": datetime.now(timezone.utc).isoformat(),
        }

    r1 = _run(process_payment_attempt(attempt(), actor="test", allow_llm=False))
    r2 = _run(process_payment_attempt(attempt(), actor="test", allow_llm=False))
    assert r1["result"] == "case_created"
    assert r2["result"] == "duplicate_attempt"
    assert _run(_db().recovery_cases.count_documents({"order_key": oid})) == 1
    assert _run(_db().payment_attempts.count_documents({"payment_id": pay1})) == 1
    _cleanup(suf)


# S. Replayed OLD event must not downgrade an authoritative state
def test_s_replayed_old_event():
    suf, oid, pay1, _ = _ids("ts")
    now = int(datetime.now(timezone.utc).timestamp())
    _post(_pay_payload("payment.captured", pay1, oid, 150000, now), f"evt_{suf}1")
    r2 = _post(_pay_payload("payment.failed", pay1, oid, 150000, now - 86400, "insufficient_funds"), f"evt_{suf}2")
    assert r2.json()["result"]["result"] == "stale_event_ignored"
    assert _attempt(pay1)["status"] == "success"
    assert _run(_db().recovery_cases.count_documents({"order_key": oid})) == 0
    _cleanup(suf)


# T. Same event processed concurrently — exactly one wins
def test_t_concurrent_same_event():
    from detection import process_payment_attempt

    suf, oid, pay1, _ = _ids("tt")

    def attempt():
        return {
            "payment_id": pay1, "order_id": oid, "invoice_id": None, "customer_reference": None,
            "amount": 1000.0, "currency": "INR", "status": "failed", "failure_code": "do_not_honor",
            "failure_reason": None, "payment_method": "card",
            "timestamp": datetime.now(timezone.utc).isoformat(), "source": "RAZORPAY_TEST",
            "source_event_id": None, "simulated": False, "ingestion_confidence": 1.0,
            "raw_data_reference": "test", "batch_id": None, "ingested_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _both():
        return await asyncio.gather(
            process_payment_attempt(attempt(), actor="test", allow_llm=False),
            process_payment_attempt(attempt(), actor="test", allow_llm=False),
        )

    results = _run(_both())
    outcomes = sorted(r["result"] for r in results)
    assert outcomes == ["case_created", "duplicate_attempt"]
    assert _run(_db().recovery_cases.count_documents({"order_key": oid})) == 1
    _cleanup(suf)


# U. Credentials with leading/trailing whitespace are trimmed before Basic Auth
def test_u_whitespace_trimmed_basic_auth():
    from providers.razorpay_adapter import RazorpayAdapter

    adapter = RazorpayAdapter({"key_id": "  rzp_test_ABC123  ", "key_secret": "  secret_xyz\t", "mode": "TEST"})
    with mock.patch("providers.razorpay_adapter.requests.request") as m:
        m.return_value = mock.Mock(status_code=200, json=lambda: {"count": 0})
        result = adapter.test_connection()
    assert result["ok"] is True
    assert m.call_args.kwargs["auth"] == ("rzp_test_ABC123", "secret_xyz")
    assert m.call_args.args[1] == "https://api.razorpay.com/v1/orders?count=1"


# V. Credentials with accidental newline characters are trimmed before Basic Auth
def test_v_newline_trimmed_basic_auth():
    from providers.razorpay_adapter import RazorpayAdapter

    adapter = RazorpayAdapter({"key_id": "rzp_test_ABC123\n", "key_secret": "\nsecret_xyz\r\n", "mode": "TEST"})
    with mock.patch("providers.razorpay_adapter.requests.request") as m:
        m.return_value = mock.Mock(status_code=200, json=lambda: {"count": 0})
        adapter.test_connection()
    assert m.call_args.kwargs["auth"] == ("rzp_test_ABC123", "secret_xyz")


# W. 401 produces masked diagnostics only — never the secret or auth header
def test_w_401_masked_diagnostics():
    from providers.base import IntegrationError
    from providers.razorpay_adapter import RazorpayAdapter

    secret = "super_secret_value_123"
    adapter = RazorpayAdapter({"key_id": "rzp_test_ABC123", "key_secret": secret, "mode": "TEST"})
    with mock.patch("providers.razorpay_adapter.requests.request") as m:
        m.return_value = mock.Mock(status_code=401)
        with pytest.raises(IntegrationError) as exc:
            adapter.fetch_order("order_x")
    msg = str(exc.value)
    assert "401" in msg
    assert "mode=TEST" in msg
    assert "auth_method=basic" in msg
    assert "endpoint=https://api.razorpay.com/v1" in msg
    assert "key_id_prefix=rzp_test_" in msg
    assert secret not in msg
    assert "super_secret" not in msg


# X. Mode selection — TEST credentials produce TEST source; nothing silently flips to LIVE
def test_x_test_mode_source():
    from providers.razorpay_adapter import RazorpayAdapter

    assert RazorpayAdapter({"mode": "TEST"}).source == "RAZORPAY_TEST"
    assert RazorpayAdapter({}).source == "RAZORPAY_TEST"
    assert RazorpayAdapter({"mode": "LIVE"}).source == "RAZORPAY_LIVE"

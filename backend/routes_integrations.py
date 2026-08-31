import asyncio
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timezone

import requests
from fastapi import APIRouter, HTTPException, Request

from audit import write_audit
from auth import get_current_user
from constants import OPEN_CASE_STATUSES, now_iso
from database import db
from detection import verify_case
from integrations_store import get_integration, public_config
from providers.base import IntegrationError
from providers.razorpay_adapter import RazorpayAdapter

router = APIRouter(prefix="/integrations", tags=["integrations"])

INTERNAL_WEBHOOK_URL = os.environ.get("INTERNAL_WEBHOOK_BASE_URL", "http://localhost:8001") + "/api/webhooks/razorpay"


async def require_owner(request: Request) -> dict:
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only the owner role can manage integrations.")
    return user


@router.get("")
async def list_integrations(request: Request):
    await get_current_user(request)
    doc = await get_integration("razorpay")
    return {
        "integrations": [public_config(doc)],
        "live_mode": {"status": "UNAVAILABLE", "note": "Live mode is not enabled in this phase. Live credentials are not accepted."},
        "webhook_endpoint_path": "/api/webhooks/razorpay",
    }


@router.put("/razorpay")
async def save_razorpay_config(request: Request):
    user = await require_owner(request)
    body = await request.json()
    key_id = (body.get("key_id") or "").strip()
    key_secret = (body.get("key_secret") or "").strip()
    webhook_secret = (body.get("webhook_secret") or "").strip()
    mode = (body.get("mode") or "TEST").upper()

    if mode != "TEST":
        raise HTTPException(status_code=400, detail="Only TEST mode is supported in this phase. Live credentials are not accepted.")
    if key_id and not key_id.startswith("rzp_test_"):
        raise HTTPException(status_code=400, detail="Test mode requires an rzp_test_… key ID. Live keys are not accepted.")
    if not key_id or not key_secret or not webhook_secret:
        raise HTTPException(status_code=400, detail="key_id, key_secret and webhook_secret are all required.")

    now = now_iso()
    await db.integrations.update_one(
        {"provider": "razorpay"},
        {"$set": {
            "provider": "razorpay",
            "mode": "TEST",
            "key_id": key_id,
            "key_secret": key_secret,
            "webhook_secret": webhook_secret,
            "status": "NOT_CONNECTED",
            "updated_at": now,
            "last_error": None,
        }, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    await write_audit(
        actor=user["email"],
        event_type="INTEGRATION_CONFIGURED",
        reason="Razorpay TEST MODE credentials saved (secrets stored server-side only; never logged or returned).",
        after_state={"provider": "razorpay", "mode": "TEST", "key_id_masked": f"{key_id[:9]}********"},
    )
    return {"integration": public_config(await get_integration("razorpay"))}


@router.post("/razorpay/test-connection")
async def test_connection(request: Request):
    user = await require_owner(request)
    config = await get_integration("razorpay")
    if not config:
        raise HTTPException(status_code=400, detail="Razorpay integration is not configured.")
    adapter = RazorpayAdapter(config)
    try:
        result = await asyncio.to_thread(adapter.test_connection)
        await db.integrations.update_one({"provider": "razorpay"}, {"$set": {"status": "CONNECTED", "last_error": None, "last_error_at": None}})
        await write_audit(actor=user["email"], event_type="INTEGRATION_TEST_SUCCEEDED", reason="Razorpay TEST MODE API connection verified against the live provider API.")
        return {"status": "CONNECTED", "detail": result}
    except IntegrationError as exc:
        await db.integrations.update_one({"provider": "razorpay"}, {"$set": {"status": "ERROR", "last_error": str(exc), "last_error_at": now_iso()}})
        await write_audit(actor=user["email"], event_type="INTEGRATION_TEST_FAILED", reason=f"Razorpay connection test failed: {exc}")
        return {"status": "ERROR", "detail": str(exc)}


# ---------------- Resend notification channel (Phase 1.5) ----------------
# API key lives ONLY in backend env; the stored doc holds non-secret state
# (enabled flag + test result). Nothing here ever returns the key.

@router.get("/resend")
async def resend_status(request: Request):
    await require_owner(request)
    doc = await db.integrations.find_one({"provider": "resend"}, {"_id": 0}) or {}
    key_present = bool(os.environ.get("EMERGENT_EMAIL_KEY"))
    enabled = bool(doc.get("enabled"))
    if not key_present or not enabled:
        status = "NOT_CONFIGURED"
    elif doc.get("last_test_ok") is False:
        status = "ERROR"
    elif doc.get("last_test_ok"):
        status = "CONNECTED"
    else:
        status = "NOT_CONFIGURED"
    return {
        "provider": "resend", "channel": "email", "enabled": enabled, "status": status,
        "last_test_at": doc.get("last_test_at"), "last_error": doc.get("last_error"),
    }


@router.put("/resend/config")
async def resend_config(request: Request):
    user = await require_owner(request)
    body = await request.json()
    enabled = bool(body.get("enabled"))
    await db.integrations.update_one(
        {"provider": "resend"},
        {"$set": {"provider": "resend", "channel": "email", "enabled": enabled,
                  "updated_at": datetime.now(timezone.utc).isoformat()},
         "$setOnInsert": {"created_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )
    await write_audit(actor=user["email"], event_type="NOTIFICATION_CONFIG_UPDATED",
                      reason=f"Customer notification channel (Resend) {'ENABLED' if enabled else 'DISABLED'}.",
                      after_state={"provider": "resend", "enabled": enabled})
    return {"saved": True, "enabled": enabled}


@router.post("/resend/test-connection")
async def resend_test_connection(request: Request):
    """Genuine end-to-end check: sends a real test email to the owner address
    from server-side config (never caller-supplied recipients)."""
    user = await require_owner(request)
    from notifications.base import NotificationError
    from notifications.resend_adapter import ResendNotificationAdapter

    owner = os.environ.get("OWNER_EMAIL")
    now = datetime.now(timezone.utc).isoformat()
    try:
        result = await ResendNotificationAdapter().test_connection(recipient=owner)
    except NotificationError as exc:
        await db.integrations.update_one({"provider": "resend"},
            {"$set": {"last_test_ok": False, "last_test_at": now, "last_error": str(exc)}}, upsert=True)
        await write_audit(actor=user["email"], event_type="NOTIFICATION_TEST_FAILED",
                          reason=f"Notification channel test failed: {exc}")
        return {"status": "ERROR", "detail": str(exc)}
    await db.integrations.update_one({"provider": "resend"},
        {"$set": {"provider": "resend", "enabled": True, "last_test_ok": True, "last_test_at": now, "last_error": None}},
        upsert=True)
    await write_audit(actor=user["email"], event_type="NOTIFICATION_TEST_PASSED",
                      reason=f"Genuine test email delivered to the owner inbox (provider ref {result.provider_reference}). Channel CONNECTED.",
                      after_state={"provider": "resend", "status": "CONNECTED"})
    return {"status": "CONNECTED", "detail": f"Test email sent to the owner inbox (ref {result.provider_reference})."}


@router.get("/resend/diagnostics")
async def resend_diagnostics(request: Request):
    """Masked metadata only — never the API key."""
    await require_owner(request)
    from notifications.resend_adapter import masked_diagnostics
    doc = await db.integrations.find_one({"provider": "resend"}, {"_id": 0}) or {}
    return masked_diagnostics(bool(doc.get("enabled")))


@router.get("/razorpay/diagnostics")
async def razorpay_diagnostics(request: Request):
    """Owner-only safe diagnostic view of the stored Razorpay credential state.
    Returns masked metadata ONLY — never the raw key_secret, webhook_secret,
    or the Authorization header."""
    await require_owner(request)
    config = await get_integration("razorpay")
    if not config:
        raise HTTPException(status_code=400, detail="Razorpay integration is not configured.")
    key_id = (config.get("key_id") or "").strip()
    key_secret = (config.get("key_secret") or "").strip()
    return {
        "provider": "razorpay",
        "mode": config.get("mode", "TEST"),
        "status": config.get("status"),
        "key_id_prefix": key_id[:9] if key_id else None,
        "key_id_length": len(key_id),
        "key_id_is_test": key_id.startswith("rzp_test_"),
        "key_secret_present": bool(key_secret),
        "key_secret_length": len(key_secret),
        "credential_source": "integration_store",
        "endpoint": "https://api.razorpay.com/v1",
        "auth_method": "basic",
        "webhook_secret_present": bool(config.get("webhook_secret")),
        "last_error": config.get("last_error"),
    }


@router.post("/razorpay/test-checkout/order")
async def create_test_checkout_order(request: Request):
    """Phase-1 verification: create a GENUINE Razorpay TEST order through the
    provider API and return the Standard Checkout launch config. key_id is
    returned in full because it is public by design (embedded in every
    checkout page); key_secret never leaves the server."""
    user = await require_owner(request)
    config = await get_integration("razorpay")
    if not config:
        raise HTTPException(status_code=400, detail="Razorpay integration is not configured.")
    body = await request.json()
    try:
        amount_inr = float(body.get("amount", 500))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="amount must be a number (INR).")
    if not 1 <= amount_inr <= 100000:
        raise HTTPException(status_code=400, detail="amount must be between ₹1 and ₹100,000.")
    amount_paise = int(round(amount_inr * 100))
    receipt = f"rcpt_{uuid.uuid4().hex[:12]}"
    adapter = RazorpayAdapter(config)
    try:
        order = await asyncio.to_thread(adapter.create_order, amount_paise, receipt)
    except IntegrationError as exc:
        await write_audit(actor=user["email"], event_type="TEST_CHECKOUT_ORDER_FAILED",
                          reason=f"Genuine Razorpay TEST order creation failed: {exc}")
        return {"status": "ERROR", "detail": str(exc)}
    await write_audit(
        actor=user["email"], event_type="TEST_CHECKOUT_ORDER_CREATED",
        reason=f"Genuine Razorpay TEST order created for checkout verification (₹{amount_paise / 100:.2f}).",
        after_state={"order_id": order.get("id"), "amount_paise": amount_paise, "currency": "INR", "mode": "TEST", "receipt": receipt},
    )
    return {
        "status": "READY",
        "order_id": order.get("id"),
        "amount_paise": amount_paise,
        "amount_inr": amount_paise / 100,
        "currency": "INR",
        "key_id": (config.get("key_id") or "").strip(),
        "mode": "TEST",
    }


@router.delete("/razorpay")
async def disconnect_razorpay(request: Request):
    user = await require_owner(request)
    await db.integrations.delete_one({"provider": "razorpay"})
    await write_audit(actor=user["email"], event_type="INTEGRATION_DISCONNECTED", reason="Razorpay integration disconnected; stored credentials removed.")
    return {"status": "DISCONNECTED"}


@router.get("/razorpay/health")
async def integration_health(request: Request):
    await get_current_user(request)
    config = await get_integration("razorpay")
    base = {"provider": "razorpay"}
    total = await db.provider_events.count_documents(base)
    processed = await db.provider_events.count_documents({**base, "processing_status": "PROCESSED"})
    failed = await db.provider_events.count_documents({**base, "processing_status": "FAILED"})
    ignored = await db.provider_events.count_documents({**base, "processing_status": "IGNORED_UNSUPPORTED"})
    duplicate_events = await db.provider_events.count_documents({**base, "duplicate_deliveries": {"$gt": 0}})
    pipeline = [{"$match": base}, {"$group": {"_id": None, "dupes": {"$sum": "$duplicate_deliveries"}}}]
    dup_sum = await db.provider_events.aggregate(pipeline).to_list(1)
    duplicates_ignored = dup_sum[0]["dupes"] if dup_sum else 0
    signature_failures = await db.security_events.count_documents({"path": "/api/webhooks/razorpay"})
    source = "RAZORPAY_LIVE" if (config or {}).get("mode") == "LIVE" else "RAZORPAY_TEST"
    cases_created = await db.recovery_cases.count_documents({"source": source})
    recovered = await db.recovery_cases.count_documents({"source": source, "status": "VERIFIED_RECOVERED"})
    last_event = await db.provider_events.find(base, {"_id": 0}).sort("received_at", -1).limit(1).to_list(1)
    last_failed_doc = await db.provider_events.find({**base, "processing_status": "FAILED"}, {"_id": 0}).sort("received_at", -1).limit(1).to_list(1)
    return {
        "provider": "Razorpay",
        "mode": (config or {}).get("mode", "TEST"),
        "status": (config or {}).get("status", "NOT_CONFIGURED"),
        "webhook": "CONNECTED" if config and config.get("webhook_secret") else "NOT CONFIGURED",
        "events_received": total,
        "events_processed": processed,
        "events_failed": failed,
        "events_ignored_unsupported": ignored,
        "duplicate_events": duplicate_events,
        "duplicates_ignored": duplicates_ignored,
        "signature_failures": signature_failures,
        "cases_created_from_provider": cases_created,
        "recovered_outcomes_detected": recovered,
        "last_webhook_at": last_event[0]["received_at"] if last_event else None,
        "last_webhook_type": last_event[0]["event_type"] if last_event else None,
        "last_successful_processing_at": (config or {}).get("last_successful_event_at"),
        "last_failed_event_at": last_failed_doc[0]["received_at"] if last_failed_doc else None,
    }


@router.post("/verification/sweep")
async def verification_sweep(request: Request):
    """Reconciliation sweep: re-verifies every open case against source-of-truth
    payment data, and reconciles provider-sourced cases against the Razorpay
    API when connected. Never claims recovery — only verifies state."""
    user = await get_current_user(request)
    await write_audit(actor=user["email"], event_type="RECONCILIATION_STARTED", reason="Verification sweep started.")
    open_cases = await db.recovery_cases.find({"status": {"$in": OPEN_CASE_STATUSES}}, {"_id": 0}).to_list(2000)
    results = {"checked": 0, "verified_recovered": 0, "closed_natural": 0, "not_recovered": 0, "pending": 0, "provider_reconciled": 0, "provider_errors": 0, "already_closed": 0}

    config = await get_integration("razorpay")
    adapter = RazorpayAdapter(config) if config and config.get("status") == "CONNECTED" else None

    for case in open_cases:
        results["checked"] += 1
        if adapter and case.get("source") in ("RAZORPAY_TEST", "RAZORPAY_LIVE"):
            try:
                remote = await asyncio.to_thread(adapter.fetch_order, case["order_key"])
                if remote.get("status") == "paid":
                    from detection import process_normalized_order_event
                    await process_normalized_order_event({
                        "kind": "order", "provider": "razorpay", "provider_event_id": None,
                        "event_type": "order.paid", "order_id": case["order_key"],
                        "amount": round((remote.get("amount") or 0) / 100, 2),
                        "amount_paid": round((remote.get("amount_paid") or 0) / 100, 2),
                        "amount_due": round((remote.get("amount_due") or 0) / 100, 2),
                        "currency": remote.get("currency"), "status": "paid",
                        "receipt": remote.get("receipt"), "created_at": None,
                        "source": case.get("source"), "source_mode": (config or {}).get("mode", "TEST"),
                    }, actor=f"reconciliation:{user['email']}")
                    results["provider_reconciled"] += 1
            except IntegrationError as exc:
                results["provider_errors"] += 1
                await write_audit(case_id=case["case_id"], actor=user["email"], event_type="RECONCILIATION_FAILED",
                                  reason=f"Provider reconciliation failed: {exc}")
        outcome = await verify_case(case["case_id"], actor=f"sweep:{user['email']}")
        key = outcome.get("result", "pending")
        results[key] = results.get(key, 0) + 1

    await write_audit(actor=user["email"], event_type="RECONCILIATION_COMPLETED", reason=f"Verification sweep completed: {results['checked']} cases checked.", after_state=results)
    return results


# ---------- Webhook Test Lab ----------

def _rz_payment_payload(event, payment_id, order_id, amount_paise, created_epoch, failure_code=None, method="card"):
    entity = {
        "id": payment_id, "entity": "payment", "amount": amount_paise, "currency": "INR",
        "status": {"payment.failed": "failed", "payment.captured": "captured", "payment.authorized": "authorized"}[event],
        "order_id": order_id, "method": method, "created_at": created_epoch,
        "email": "lab@example.com",
    }
    if failure_code:
        entity["error_code"] = failure_code
        entity["error_description"] = failure_code.replace("_", " ")
    return {"entity": "event", "account_id": "acc_TESTLAB", "event": event,
            "payload": {"payment": {"entity": entity}}, "created_at": created_epoch}


def _rz_order_payload(order_id, amount_paise, created_epoch):
    return {"entity": "event", "account_id": "acc_TESTLAB", "event": "order.paid",
            "payload": {"order": {"entity": {"id": order_id, "entity": "order", "amount": amount_paise,
                                             "amount_paid": amount_paise, "amount_due": 0, "currency": "INR",
                                             "status": "paid", "receipt": f"rcpt_{order_id[-6:]}", "created_at": created_epoch}}},
            "created_at": created_epoch}


async def _deliver(payload, secret, event_id, valid_signature=True):
    raw = json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not valid_signature:
        sig = "0" * 64

    def _post():
        return requests.post(
            INTERNAL_WEBHOOK_URL,
            data=raw,
            headers={"Content-Type": "application/json", "X-Razorpay-Signature": sig, "x-razorpay-event-id": event_id},
            timeout=15,
        )

    resp = await asyncio.to_thread(_post)
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text[:200]}
    return {"http": resp.status_code, "body": body}


@router.post("/razorpay/test-lab/{test_name}")
async def run_test_lab(test_name: str, request: Request):
    """Developer test lab. Every scenario delivers genuinely-signed Razorpay-
    format payloads through the REAL webhook endpoint — same code path."""
    await require_owner(request)
    config = await get_integration("razorpay")
    if not config or not config.get("webhook_secret"):
        raise HTTPException(status_code=400, detail="Configure Razorpay TEST MODE (including the webhook secret) before using the test lab.")
    secret = config["webhook_secret"]
    suf = uuid.uuid4().hex[:6]
    order = f"order_LAB{suf.upper()}"
    pay1 = f"pay_LAB{suf}A"
    pay2 = f"pay_LAB{suf}B"
    now = int(datetime.now(timezone.utc).timestamp())
    steps = []

    async def deliver(payload, event_id, label, valid_signature=True):
        res = await _deliver(payload, secret, event_id, valid_signature)
        steps.append({"label": label, "http": res["http"], "result": res["body"].get("status") or res["body"].get("detail"), "detail": res["body"]})
        return res

    if test_name == "valid-payment-failed":
        await deliver(_rz_payment_payload("payment.failed", pay1, order, 675000, now, "insufficient_funds"), f"evt_LAB{suf}1", "payment.failed delivered")
    elif test_name == "valid-payment-captured":
        await deliver(_rz_payment_payload("payment.captured", pay1, order, 500000, now), f"evt_LAB{suf}1", "payment.captured delivered")
    elif test_name == "valid-order-paid":
        await deliver(_rz_payment_payload("payment.failed", pay1, order, 300000, now - 60, "do_not_honor"), f"evt_LAB{suf}1", "payment.failed delivered")
        await deliver(_rz_order_payload(order, 300000, now), f"evt_LAB{suf}2", "order.paid delivered")
    elif test_name == "duplicate-event":
        payload = _rz_payment_payload("payment.failed", pay1, order, 100000, now, "insufficient_funds")
        await deliver(payload, f"evt_LAB{suf}1", "first delivery")
        await deliver(payload, f"evt_LAB{suf}1", "second delivery (same event id)")
    elif test_name == "invalid-signature":
        await deliver(_rz_payment_payload("payment.failed", pay1, order, 100000, now, "insufficient_funds"), f"evt_LAB{suf}1", "forged signature delivery", valid_signature=False)
    elif test_name == "out-of-order":
        await deliver(_rz_payment_payload("payment.captured", pay1, order, 250000, now), f"evt_LAB{suf}1", "payment.captured FIRST")
        await deliver(_rz_payment_payload("payment.authorized", pay1, order, 250000, now - 300), f"evt_LAB{suf}2", "payment.authorized SECOND (older)")
    elif test_name == "late-success":
        await deliver(_rz_payment_payload("payment.failed", pay1, order, 420000, now - 600, "insufficient_funds"), f"evt_LAB{suf}1", "payment.failed")
        await deliver(_rz_payment_payload("payment.captured", pay2, order, 420000, now), f"evt_LAB{suf}2", "payment.captured (same order, new payment)")
    elif test_name == "replacement-payment":
        await deliver(_rz_payment_payload("payment.failed", pay1, order, 199900, now - 300, "expired_card"), f"evt_LAB{suf}1", "attempt 1 failed (expired card)")
        await deliver(_rz_payment_payload("payment.captured", pay2, order, 199900, now, method="upi"), f"evt_LAB{suf}2", "attempt 2 captured via UPI")
    elif test_name == "partial-payment":
        await deliver(_rz_payment_payload("payment.failed", pay1, order, 800000, now - 300, "insufficient_funds"), f"evt_LAB{suf}1", "payment.failed for ₹8,000")
        await deliver(_rz_payment_payload("payment.captured", pay2, order, 500000, now), f"evt_LAB{suf}2", "partial capture ₹5,000")
    elif test_name == "unknown-event":
        payload = _rz_payment_payload("payment.failed", pay1, order, 100000, now, "insufficient_funds")
        payload["event"] = "payment.dispute.created"
        await deliver(payload, f"evt_LAB{suf}1", "unsupported event type")
    elif test_name == "malformed-payload":
        raw = b'{"entity":"event","event":"payment.failed",BROKEN'
        sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()

        def _post():
            return requests.post(INTERNAL_WEBHOOK_URL, data=raw, headers={"Content-Type": "application/json", "X-Razorpay-Signature": sig, "x-razorpay-event-id": f"evt_LAB{suf}1"}, timeout=15)

        resp = await asyncio.to_thread(_post)
        steps.append({"label": "malformed JSON with valid signature", "http": resp.status_code, "result": "rejected" if resp.status_code == 400 else "unexpected", "detail": {}})
    elif test_name == "replayed-old-event":
        await deliver(_rz_payment_payload("payment.captured", pay1, order, 150000, now), f"evt_LAB{suf}1", "payment.captured (current)")
        await deliver(_rz_payment_payload("payment.failed", pay1, order, 150000, now - 86400, "insufficient_funds"), f"evt_LAB{suf}2", "stale payment.failed for SAME payment (replayed old)")
    else:
        raise HTTPException(status_code=404, detail="Unknown test lab scenario.")

    return {"test": test_name, "steps": steps, "mode": "TEST", "order_id": order}


@router.get("/razorpay/events")
async def list_provider_events(request: Request):
    await get_current_user(request)
    events = await db.provider_events.find({"provider": "razorpay"}, {"_id": 0}).sort("received_at", -1).to_list(50)
    return {"events": events}

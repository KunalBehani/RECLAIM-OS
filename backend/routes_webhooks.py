import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from pymongo.errors import DuplicateKeyError

from audit import write_audit
from auth import get_current_user
from constants import normalize_status, now_iso, parse_dt
from database import db
from detection import log_exception, process_normalized_event, process_payment_attempt
from ingestion import _parse_amount
from integrations_store import get_integration
from providers.razorpay_adapter import RazorpayAdapter
from security_utils import verify_signature

router = APIRouter(tags=["webhooks"])

TIMESTAMP_TOLERANCE = timedelta(hours=24)
MAX_WEBHOOK_BODY = 1024 * 1024  # 1 MB


@router.post("/webhooks/payments")
async def receive_webhook(request: Request):
    raw = await request.body()
    signature = request.headers.get("X-Reclaim-Signature")
    if not verify_signature(raw, signature):
        await db.security_events.insert_one({
            "type": "INVALID_SIGNATURE",
            "path": "/api/webhooks/payments",
            "ip": request.client.host if request.client else None,
            "received_at": now_iso(),
        })
        raise HTTPException(status_code=401, detail="Invalid webhook signature. Event rejected and logged as a security event.")
    try:
        payload = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed JSON payload.")
    return await process_webhook_payload(payload, actor="webhook")


async def process_webhook_payload(payload: dict, actor="webhook") -> dict:
    """Shared ingestion path for the generic merchant webhook and the labeled
    simulator. Idempotent on event_id; stale or future-dated events rejected.
    Normalized records flow through the SAME engine as provider events."""
    event_id = payload.get("event_id")
    if not event_id:
        raise HTTPException(status_code=400, detail="Missing event_id.")

    try:
        await db.webhook_events.insert_one({
            "event_id": event_id,
            "received_at": now_iso(),
            "type": payload.get("type"),
            "simulated": bool(payload.get("simulated")),
            "status": "ACCEPTED",
            "actor": actor,
        })
    except DuplicateKeyError:
        await write_audit(
            actor=actor,
            event_type="DUPLICATE_WEBHOOK_BLOCKED",
            reason=f"Event {event_id} was already processed. BLOCKED AS DUPLICATE (idempotent, replay-safe).",
            related={"event_id": event_id},
        )
        return {"status": "BLOCKED_AS_DUPLICATE", "duplicate": True, "event_id": event_id}

    ts = parse_dt(payload.get("timestamp"))
    now = datetime.now(timezone.utc)
    if not ts:
        await db.webhook_events.update_one({"event_id": event_id}, {"$set": {"status": "REJECTED", "error": "missing_timestamp"}})
        raise HTTPException(status_code=422, detail="Missing or invalid event timestamp.")
    if ts > now + timedelta(minutes=5) or ts < now - TIMESTAMP_TOLERANCE:
        await db.webhook_events.update_one({"event_id": event_id}, {"$set": {"status": "REJECTED", "error": "stale_timestamp"}})
        raise HTTPException(status_code=422, detail="Event timestamp outside the acceptable window (stale or future-dated).")

    simulated = bool(payload.get("simulated"))
    data = payload.get("data") or {}
    amount = _parse_amount(data.get("amount"))
    status = normalize_status(data.get("status"))
    attempt = {
        "payment_id": data.get("payment_id") or f"pay_{uuid.uuid4().hex[:12]}",
        "order_id": data.get("order_id"),
        "invoice_id": data.get("invoice_id"),
        "customer_reference": data.get("customer_reference"),
        "email": data.get("email"),
        "amount": amount,
        "currency": (str(data.get("currency")).upper() if data.get("currency") else None),
        "status": status,
        "failure_code": data.get("failure_code"),
        "failure_reason": data.get("failure_reason"),
        "payment_method": data.get("payment_method"),
        "timestamp": ts.isoformat(),
        "source": "SIMULATOR" if simulated else "WEBHOOK",
        "source_event_id": event_id,
        "simulated": simulated,
        "ingestion_confidence": 1.0,
        "payment_id_generated": not bool(data.get("payment_id")),
        "timestamp_estimated": False,
        "raw_data_reference": f"webhook:{event_id}",
        "batch_id": None,
        "ingested_at": now_iso(),
    }

    if status is None or amount is None:
        exc = await log_exception(
            "WEBHOOK_DATA_EXCEPTION",
            attempt["payment_id"],
            {"missing": [f for f, v in (("status", status), ("amount", amount)) if v is None]},
            source=attempt["source"],
        )
        await db.webhook_events.update_one({"event_id": event_id}, {"$set": {"status": "DATA_EXCEPTION", "exception_id": exc["exception_id"]}})
        await write_audit(
            actor=actor, event_type="DATA_EXCEPTION",
            reason="Webhook event missing a recognized status or a valid amount. Sent to exception queue; NOT included in financial totals.",
            after_state={"exception_id": exc["exception_id"]}, related={"event_id": event_id},
        )
        return {"status": "DATA_EXCEPTION", "event_id": event_id, "exception_id": exc["exception_id"]}

    result = await process_payment_attempt(attempt, actor=actor)
    await db.webhook_events.update_one({"event_id": event_id}, {"$set": {"status": "PROCESSED", "result": result.get("result")}})
    return {"status": "processed", "event_id": event_id, "simulated": simulated, "result": result}


@router.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request):
    """Public Razorpay webhook endpoint.

    Security order: size limit -> configured-secret check -> RAW-body HMAC
    signature verification (constant time) -> JSON parse -> event-id
    idempotency -> normalize -> shared engine. No secret is ever logged.
    """
    raw = await request.body()
    if len(raw) > MAX_WEBHOOK_BODY:
        raise HTTPException(status_code=413, detail="Payload too large.")

    config = await get_integration("razorpay", "TEST")
    if not config or not config.get("webhook_secret"):
        raise HTTPException(status_code=503, detail="Razorpay integration is not configured. Save Test Mode credentials first.")

    signature = request.headers.get("X-Razorpay-Signature")
    provider_event_id = request.headers.get("x-razorpay-event-id")

    if not RazorpayAdapter.verify_signature(raw, signature, config["webhook_secret"]):
        await db.security_events.insert_one({
            "type": "INVALID_SIGNATURE",
            "path": "/api/webhooks/razorpay",
            "ip": request.client.host if request.client else None,
            "received_at": now_iso(),
        })
        await write_audit(
            actor="webhook:razorpay",
            event_type="WEBHOOK_SIGNATURE_REJECTED",
            reason="Razorpay webhook signature verification failed. Event rejected; no state changed; security event logged.",
            related={"provider_event_id": provider_event_id},
        )
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")

    try:
        payload = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed JSON payload.")

    if not provider_event_id:
        provider_event_id = f"noeid_{hashlib.sha256(raw).hexdigest()[:20]}"

    return await process_provider_event(payload, provider_event_id, config, actor="webhook:razorpay")


async def process_provider_event(payload: dict, provider_event_id: str, config: dict, actor="webhook:razorpay") -> dict:
    """Durable event store + idempotency + normalization + shared engine."""
    adapter = RazorpayAdapter(config)
    mode = config.get("mode", "TEST")
    now = now_iso()
    event_doc = {
        "internal_event_id": f"pevt_{uuid.uuid4().hex[:12]}",
        "provider": "razorpay",
        "provider_event_id": provider_event_id,
        "event_type": payload.get("event"),
        "provider_account_id": payload.get("account_id"),
        "received_at": now,
        "first_seen_at": now,
        "provider_created_at": datetime.fromtimestamp(int(payload["created_at"]), tz=timezone.utc).isoformat() if payload.get("created_at") else None,
        "signature_verified": True,
        "signature_failure_reason": None,
        "processing_status": "RECEIVED",
        "processing_attempts": 1,
        "processed_at": None,
        "duplicate_deliveries": 0,
        "last_duplicate_at": None,
        "raw_payload_hash": hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest(),
        "normalized_event_id": None,
        "normalized_order_id": None,
        "error_code": None,
        "error_message": None,
        "source": "LIVE" if mode == "LIVE" else "TEST_MODE",
        "mode": mode,
    }
    try:
        await db.provider_events.insert_one(event_doc)
    except DuplicateKeyError:
        await db.provider_events.update_one(
            {"provider": "razorpay", "provider_event_id": provider_event_id},
            {"$inc": {"duplicate_deliveries": 1}, "$set": {"last_duplicate_at": now_iso()}},
        )
        await write_audit(
            actor=actor,
            event_type="DUPLICATE_EVENT_DETECTED",
            reason=f"Razorpay event {provider_event_id} delivered again. Already processed — no duplicate transactions, cases, actions or revenue.",
            related={"provider_event_id": provider_event_id},
        )
        return {"status": "duplicate", "duplicate": True, "provider_event_id": provider_event_id}
    event_doc.pop("_id", None)

    await write_audit(
        actor=actor,
        event_type="WEBHOOK_SIGNATURE_VERIFIED",
        reason=f"Raw-body HMAC-SHA256 signature verified for Razorpay event ({payload.get('event')}), constant-time comparison, mode {mode}.",
        after_state={"provider_event_id": provider_event_id, "mode": mode},
        related={"provider_event_id": provider_event_id},
        provider_mode=mode,
        correlation_id=provider_event_id,
    )
    await write_audit(
        actor=actor,
        event_type="WEBHOOK_RECEIVED",
        provider_mode=mode,
        correlation_id=provider_event_id,
        reason=f"Razorpay webhook received and signature verified ({payload.get('event')}).",
        after_state={"provider_event_id": provider_event_id, "event_type": payload.get("event"), "mode": mode},
        related={"provider_event_id": provider_event_id},
    )

    normalized = adapter.normalize_event(payload, provider_event_id)
    if normalized.get("kind") == "unsupported":
        await db.provider_events.update_one(
            {"provider": "razorpay", "provider_event_id": provider_event_id},
            {"$set": {"processing_status": "IGNORED_UNSUPPORTED", "processed_at": now_iso()}},
        )
        return {"status": "ignored_unsupported", "event_type": normalized.get("event_type"), "provider_event_id": provider_event_id}

    normalized_id = normalized.get("order_id") if normalized["kind"] == "order" else normalized["attempt"].get("payment_id")
    order_ref = normalized.get("order_id") if normalized["kind"] == "order" else normalized["attempt"].get("order_id")
    await db.provider_events.update_one(
        {"provider": "razorpay", "provider_event_id": provider_event_id},
        {"$set": {"normalized_event_id": normalized_id, "normalized_order_id": order_ref}},
    )
    await write_audit(
        actor=actor,
        event_type="EVENT_NORMALIZED",
        reason=f"Provider event normalized into internal {normalized.get('kind')} model ({normalized.get('event_type')}).",
        after_state={"event_type": normalized.get("event_type"), "kind": normalized["kind"], "mode": mode,
                     "normalized_event_id": normalized_id, "order_id": order_ref},
        related={"provider_event_id": provider_event_id},
        provider_mode=mode,
        correlation_id=provider_event_id,
    )

    try:
        result = await process_normalized_event(normalized, actor=actor)
        await db.provider_events.update_one(
            {"provider": "razorpay", "provider_event_id": provider_event_id},
            {"$set": {"processing_status": "PROCESSED", "processed_at": now_iso(), "result": result.get("result")}},
        )
        await db.integrations.update_one(
            {"provider": "razorpay", "mode": mode},
            {"$set": {"last_successful_event_at": now_iso()}},
        )
        if mode == "LIVE":
            await write_audit(
                actor=actor,
                event_type="LIVE_EVENT_PROCESSED",
                reason=f"LIVE Razorpay event {payload.get('event')} processed (order {order_ref}). Ingestion, verification and reconciliation only — no LIVE action executes unless explicitly enabled by the owner.",
                after_state={"provider_event_id": provider_event_id, "result": result.get("result"), "mode": "LIVE"},
                related={"provider_event_id": provider_event_id},
            )
        return {"status": "processed", "provider_event_id": provider_event_id, "simulated": False, "mode": mode, "result": result}
    except Exception as exc:
        await db.provider_events.update_one(
            {"provider": "razorpay", "provider_event_id": provider_event_id},
            {"$set": {"processing_status": "FAILED", "error_code": "PROCESSING_ERROR", "error_message": str(exc)[:300]}},
        )
        await db.integrations.update_one(
            {"provider": "razorpay", "mode": mode},
            {"$set": {"last_error_at": now_iso(), "last_error": "Event processing failed (see event record)"}},
        )
        raise HTTPException(status_code=500, detail="Event accepted but processing failed; it is recorded and safe to retry.")


@router.post("/webhooks/razorpay/live")
async def razorpay_live_webhook(request: Request):
    """Production-mode Razorpay webhook — completely isolated from TEST mode:
    its own stored credentials document, its own webhook secret, and an explicit
    owner activation gate. Same security order as TEST: size limit -> configured
    secret + activation check -> RAW-body HMAC-SHA256 verification (constant
    time) -> JSON parse -> event-id idempotency -> normalize -> shared engine.
    No secret is ever logged."""
    raw = await request.body()
    if len(raw) > MAX_WEBHOOK_BODY:
        raise HTTPException(status_code=413, detail="Payload too large.")

    config = await get_integration("razorpay", "LIVE")
    if not config or not config.get("webhook_secret"):
        raise HTTPException(status_code=503, detail="Razorpay LIVE mode is not configured.")
    if not config.get("live_activated"):
        raise HTTPException(status_code=403, detail="LIVE mode is configured but not activated. Explicit owner activation is required before live events are accepted.")

    signature = request.headers.get("X-Razorpay-Signature")
    provider_event_id = request.headers.get("x-razorpay-event-id")

    if not RazorpayAdapter.verify_signature(raw, signature, config["webhook_secret"]):
        await db.security_events.insert_one({
            "type": "INVALID_SIGNATURE",
            "path": "/api/webhooks/razorpay/live",
            "ip": request.client.host if request.client else None,
            "received_at": now_iso(),
        })
        await write_audit(
            actor="webhook:razorpay-live",
            event_type="LIVE_WEBHOOK_SIGNATURE_REJECTED",
            reason="LIVE Razorpay webhook signature verification failed. Event rejected; no state changed; security event logged.",
            related={"provider_event_id": provider_event_id},
        )
        raise HTTPException(status_code=401, detail="Invalid webhook signature.")

    try:
        payload = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed JSON payload.")

    if not provider_event_id:
        provider_event_id = f"noeid_{hashlib.sha256(raw).hexdigest()[:20]}"

    return await process_provider_event(payload, provider_event_id, config, actor="webhook:razorpay-live")


@router.get("/webhooks/events")
async def list_webhook_events(request: Request):
    await get_current_user(request)
    events = await db.webhook_events.find({}, {"_id": 0}).sort("received_at", -1).to_list(50)
    provider_events = await db.provider_events.find({}, {"_id": 0}).sort("received_at", -1).to_list(50)
    security_events = await db.security_events.find({}, {"_id": 0}).sort("received_at", -1).to_list(20)
    return {"events": events, "provider_events": provider_events, "security_events": security_events}


@router.get("/webhooks/config")
async def webhook_config(request: Request):
    await get_current_user(request)
    return {
        "endpoint_path": "/api/webhooks/payments",
        "razorpay_endpoint_path": "/api/webhooks/razorpay",
        "signature_header": "X-Reclaim-Signature",
        "signature_scheme": "HMAC-SHA256 hex of the raw request body, prefixed with 'sha256='",
        "timestamp_tolerance": "24 hours",
        "idempotency": "event_id is unique; replays are blocked as duplicates",
        "secret_configured": bool(os.environ.get("WEBHOOK_SECRET")),
        "mode": "TEST MODE — configure Razorpay Test Mode in Integrations for provider events. Use the simulator or sign events with the server-side webhook secret.",
    }

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
from detection import log_exception, process_payment_attempt
from ingestion import _parse_amount
from security_utils import verify_signature

router = APIRouter(tags=["webhooks"])

TIMESTAMP_TOLERANCE = timedelta(hours=24)


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
    """Shared ingestion path for real webhooks and the labeled simulator.
    Idempotent on event_id; stale or future-dated events are rejected."""
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


@router.get("/webhooks/events")
async def list_webhook_events(request: Request):
    await get_current_user(request)
    events = await db.webhook_events.find({}, {"_id": 0}).sort("received_at", -1).to_list(50)
    security_events = await db.security_events.find({}, {"_id": 0}).sort("received_at", -1).to_list(20)
    return {"events": events, "security_events": security_events}


@router.get("/webhooks/config")
async def webhook_config(request: Request):
    await get_current_user(request)
    return {
        "endpoint_path": "/api/webhooks/payments",
        "signature_header": "X-Reclaim-Signature",
        "signature_scheme": "HMAC-SHA256 hex of the raw request body, prefixed with 'sha256='",
        "timestamp_tolerance": "24 hours",
        "idempotency": "event_id is unique; replays are blocked as duplicates",
        "secret_configured": bool(os.environ.get("WEBHOOK_SECRET")),
        "mode": "TEST MODE — no live payment provider is connected. Use the simulator or sign events with the server-side webhook secret.",
    }

"""Public, tokenized same-order recovery retry endpoints (customer-facing).

The token is an opaque, unguessable per-action secret issued when a REAL
recovery notification is sent. These endpoints expose only the public Razorpay
key_id (public by design) plus the order's own amount/currency — never any
secret. Completion is accepted only with a valid Razorpay checkout signature
(HMAC-SHA256 of order_id|payment_id with the server-side key_secret), which is
what lets attribution link the resulting payment back to the recovery action
honestly (STRONG).
"""
import hashlib
import hmac
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from audit import write_audit
from constants import now_iso, parse_dt
from database import db
from integrations_store import get_integration

router = APIRouter(prefix="/recovery", tags=["recovery"])


class CompleteBody(BaseModel):
    razorpay_payment_id: str
    razorpay_order_id: str
    razorpay_signature: str


async def _load(token: str):
    action = await db.recovery_actions.find_one({"recovery_token": token}, {"_id": 0})
    if not action:
        raise HTTPException(status_code=404, detail="Recovery link is invalid or expired.")
    expires_at = parse_dt(action.get("expires_at"))
    if expires_at and expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=404, detail="Recovery link is invalid or expired.")
    case = await db.recovery_cases.find_one({"case_id": action["case_id"]}, {"_id": 0})
    if not case:
        raise HTTPException(status_code=404, detail="Recovery case not found.")
    config = await get_integration("razorpay", "TEST")
    if not config:
        raise HTTPException(status_code=400, detail="Payments are not configured.")
    return action, case, config


@router.get("/pay/{token}")
async def get_retry_launch(token: str):
    action, case, config = await _load(token)
    settled = case.get("status") in ("VERIFIED_RECOVERED", "NATURALLY_RECOVERED")
    return {
        "order_id": case["order_key"],
        "amount_paise": int(round(float(case.get("amount_at_risk") or 0) * 100)),
        "currency": case.get("currency") or "INR",
        "key_id": (config.get("key_id") or "").strip(),
        "mode": config.get("mode", "TEST"),
        "merchant": "RECLAIM OS",
        "settled": settled,
        "linked_payment_id": action.get("linked_payment_id"),
    }


@router.post("/pay/{token}/complete")
async def complete_retry(token: str, body: CompleteBody):
    payment_id = body.razorpay_payment_id.strip()
    order_id = body.razorpay_order_id.strip()
    signature = body.razorpay_signature.strip()
    if not payment_id or not order_id or not signature:
        raise HTTPException(status_code=400, detail="razorpay_payment_id, razorpay_order_id and razorpay_signature are required.")

    action, case, config = await _load(token)
    if order_id != case["order_key"]:
        raise HTTPException(status_code=400, detail="Payment order does not match this recovery link.")

    # Verify the genuine Razorpay checkout signature (server-side secret, never exposed).
    key_secret = (config.get("key_secret") or "").strip()
    expected = hmac.new(key_secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=400, detail="Invalid Razorpay payment signature.")

    if action.get("linked_payment_id"):
        return {"linked": True, "duplicate": True, "payment_id": action["linked_payment_id"]}

    await db.recovery_actions.update_one(
        {"action_id": action["action_id"]},
        {"$set": {"linked_payment_id": payment_id, "linked_at": now_iso()}},
    )
    await write_audit(
        case_id=case["case_id"],
        actor="customer",
        event_type="RECOVERY_PAYMENT_LINKED",
        reason=(
            f"Customer completed a same-order payment ({payment_id}) through the recovery link issued by "
            f"action {action['action_id']}; Razorpay checkout signature verified server-side. "
            "Recovery attribution will be finalized by the provider webhook (source of truth)."
        ),
        after_state={"action_id": action["action_id"], "linked_payment_id": payment_id, "order_id": order_id},
    )
    return {"linked": True, "duplicate": False, "payment_id": payment_id}


def new_recovery_token() -> str:
    return f"rct_{uuid.uuid4().hex}{uuid.uuid4().hex[:8]}"

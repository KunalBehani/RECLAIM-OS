"""Public, tokenized same-order recovery retry endpoints (customer-facing).

The token is an opaque, unguessable per-action secret issued when a REAL
recovery notification is sent. These endpoints expose only the public Razorpay
key_id (public by design) plus the order's own amount/currency — never any
secret. Completion is accepted only with a valid Razorpay checkout signature
(HMAC-SHA256 of order_id|payment_id with the server-side key_secret), which is
what lets attribution link the resulting payment back to the recovery action
honestly (STRONG).

Provider-mode isolation guarantee
----------------------------------
The Razorpay integration configuration is selected exclusively from the
authoritative server-side ``case["provider_mode"]`` field that is written by
the detection pipeline at case-creation time and is never mutated afterwards.
It is impossible for:
  - a LIVE case to receive TEST credentials, or
  - a TEST case to receive LIVE credentials.
Client input (query parameters, request body) is never used to select the mode.
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


# Authoritative mapping from known case source/provider_mode values to the
# Razorpay integration mode that MUST be used.  Any value absent from this
# table causes a fail-closed 400 — the system never silently falls back to
# the wrong credential set.
_SOURCE_TO_INTEGRATION_MODE: dict[str, str] = {
    "RAZORPAY_TEST": "TEST",
    "RAZORPAY_LIVE": "LIVE",
    "WEBHOOK":       "TEST",   # generic merchant webhook — always TEST
    "SIMULATOR":     "TEST",
    "TEST":          "TEST",
    "TEST_LAB":      "TEST",
    "CSV_UPLOAD":    "TEST",
    "XLSX_UPLOAD":   "TEST",
    "FILE_IMPORT":   "TEST",
}


def _mode_for_case(case: dict) -> str:
    """Return the Razorpay integration mode (``"TEST"`` or ``"LIVE"``) that
    must be used for this case.

    The primary authoritative source is ``case["provider_mode"]``, written at
    case-creation time by the detection pipeline and never mutated afterwards.
    ``case["source"]`` is consulted as a secondary signal for older documents
    that pre-date the ``provider_mode`` field.

    Raises HTTP 400 for any unrecognised value so the system fails closed
    rather than silently selecting the wrong credentials.
    """
    # Primary: provider_mode written at case creation ("TEST" or "LIVE").
    pm = (case.get("provider_mode") or "").strip().upper()
    if pm == "LIVE":
        return "LIVE"
    if pm == "TEST":
        return "TEST"

    # Secondary: source field (backwards-compat for pre-provider_mode docs).
    source = (case.get("source") or "").strip()
    mode = _SOURCE_TO_INTEGRATION_MODE.get(source)
    if mode is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Cannot determine integration mode for case {case.get('case_id')!r}: "
                f"unrecognised provider_mode={pm!r} and source={source!r}. "
                "Refusing to proceed to prevent credential mismatch."
            ),
        )
    return mode


async def _load(token: str):
    """Load and validate a recovery action, its case, and the correct
    mode-specific Razorpay integration configuration.

    Fail-closed contract:
      - Token not found or expired          → 404
      - Case not found                      → 404
      - Unknown / invalid provider mode     → 400
      - Mode-specific config missing        → 400 (never fall back to other mode)
      - LIVE integration not yet activated  → 400
    """
    action = await db.recovery_actions.find_one({"recovery_token": token}, {"_id": 0})
    if not action:
        raise HTTPException(status_code=404, detail="Recovery link is invalid or expired.")
    expires_at = parse_dt(action.get("expires_at"))
    if expires_at and expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=404, detail="Recovery link is invalid or expired.")
    case = await db.recovery_cases.find_one({"case_id": action["case_id"]}, {"_id": 0})
    if not case:
        raise HTTPException(status_code=404, detail="Recovery case not found.")

    # Derive provider mode exclusively from authoritative server-side case data.
    # Client input is never consulted.
    integration_mode = _mode_for_case(case)

    config = await get_integration("razorpay", integration_mode)
    if not config:
        # Hard fail: never silently fall back from LIVE to TEST or vice versa.
        raise HTTPException(
            status_code=400,
            detail=(
                f"Razorpay {integration_mode} configuration is not available. "
                "Cannot process the recovery payment."
            ),
        )

    # LIVE integration requires an additional explicit owner-activation gate,
    # mirroring the same gate applied by the live webhook endpoint.
    if integration_mode == "LIVE" and not config.get("live_activated"):
        raise HTTPException(
            status_code=400,
            detail=(
                "Razorpay LIVE integration is configured but not yet activated "
                "by the owner. Cannot process the recovery payment."
            ),
        )

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

    # Webhook/link race: the provider webhook (source of truth) may close the
    # case MODERATE seconds before the browser posts this signature-verified
    # link. Direct evidence arriving late still earns STRONG.
    if (
        case.get("status") == "VERIFIED_RECOVERED"
        and case.get("attribution_strength") == "MODERATE"
        and (case.get("verification_evidence") or {}).get("success_payment_id") in (None, payment_id)
    ):
        await db.recovery_cases.update_one(
            {"case_id": case["case_id"]},
            {"$set": {"attribution_strength": "STRONG"}},
        )
        await write_audit(
            case_id=case["case_id"],
            actor="customer",
            event_type="ATTRIBUTION_DECISION",
            reason=(
                f"Recovery-link payment {payment_id} linked after the case was already closed by the provider webhook. "
                "Signature-verified same-order link is direct evidence: attribution upgraded MODERATE -> STRONG."
            ),
            before_state={"attribution_strength": "MODERATE"},
            after_state={"attribution_strength": "STRONG", "linked_payment_id": payment_id},
        )
    return {"linked": True, "duplicate": False, "payment_id": payment_id}


def new_recovery_token() -> str:
    return f"rct_{uuid.uuid4().hex}{uuid.uuid4().hex[:8]}"

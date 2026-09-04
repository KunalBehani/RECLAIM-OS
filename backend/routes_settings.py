from fastapi import APIRouter, HTTPException, Request

from audit import write_audit
from auth import get_current_user
from database import db, get_settings
from policy import ACTION_CATALOG

router = APIRouter(prefix="/settings", tags=["settings"])

EDITABLE_KEYS = {
    "emergency_stop": bool,
    "auto_execute": bool,
    "recovery_window_days": (int, float),
    "approval_threshold_amount": (int, float),
    "confidence_threshold": (int, float),
    "max_total_cost_per_case": (int, float),
    "live_actions_enabled": bool,
    "max_customer_actions_per_day": int,
}


@router.get("")
async def read_settings(request: Request):
    await get_current_user(request)
    settings = await get_settings()
    return {"settings": settings, "action_catalog": ACTION_CATALOG}


@router.put("")
async def update_settings(request: Request):
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only the owner role can modify policy settings and safety switches.")
    body = await request.json()
    current = await get_settings()
    updates = {}
    for key, value in body.items():
        if key in EDITABLE_KEYS and isinstance(value, EDITABLE_KEYS[key]):
            updates[key] = value
    if updates:
        await db.settings.update_one({"key": "policy"}, {"$set": updates}, upsert=True)
        await write_audit(
            actor=user["email"],
            event_type="SETTINGS_UPDATED",
            reason="Policy configuration updated.",
            before_state={k: current.get(k) for k in updates},
            after_state=updates,
        )
        # Safety-critical switches get their own explicit audit events.
        if "emergency_stop" in updates:
            await write_audit(
                actor=user["email"],
                event_type="EMERGENCY_STOP_ENABLED" if updates["emergency_stop"] else "EMERGENCY_STOP_DISABLED",
                reason=("Global emergency stop ENABLED — all autonomous actions halt; every execution path fails closed."
                        if updates["emergency_stop"] else
                        "Global emergency stop disabled by owner."),
            )
        if "live_actions_enabled" in updates:
            await write_audit(
                actor=user["email"],
                event_type="LIVE_ACTIONS_ENABLED" if updates["live_actions_enabled"] else "LIVE_ACTIONS_DISABLED",
                reason=("LIVE actions explicitly ENABLED by owner — live execution gates now permit policy-approved actions."
                        if updates["live_actions_enabled"] else
                        "LIVE actions disabled (safe default restored)."),
            )
    return {"settings": await get_settings()}

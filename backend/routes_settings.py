from fastapi import APIRouter, Request

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
}


@router.get("")
async def read_settings(request: Request):
    await get_current_user(request)
    settings = await get_settings()
    return {"settings": settings, "action_catalog": ACTION_CATALOG}


@router.put("")
async def update_settings(request: Request):
    user = await get_current_user(request)
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
    return {"settings": await get_settings()}

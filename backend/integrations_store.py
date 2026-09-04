from __future__ import annotations

"""Integration credential storage. Secrets live only server-side in MongoDB
and are NEVER returned by any API response — only masked metadata."""

from database import db


async def get_integration(provider: str = "razorpay", mode: str | None = None):
    """Mode-scoped credential retrieval. TEST and LIVE configurations are
    completely isolated documents; legacy docs without a mode field are
    treated as TEST. mode=None matches any mode (used for non-payment
    providers like resend that have no modes)."""
    if mode is None:
        return await db.integrations.find_one({"provider": provider}, {"_id": 0})
    doc = await db.integrations.find_one({"provider": provider, "mode": mode}, {"_id": 0})
    if doc is None and mode == "TEST":
        doc = await db.integrations.find_one({"provider": provider, "$or": [{"mode": {"$exists": False}}, {"mode": None}]}, {"_id": 0})
    return doc


def mask_key_id(key_id):
    if not key_id:
        return None
    prefix = key_id[:9] if len(key_id) >= 9 else key_id[:3]
    return f"{prefix}{'*' * 8}"


def public_config(doc):
    if not doc:
        return {
            "provider": "razorpay",
            "mode": "TEST",
            "status": "NOT_CONFIGURED",
            "key_id_masked": None,
            "webhook_configured": False,
            "created_at": None,
            "updated_at": None,
            "last_successful_event_at": None,
            "last_error_at": None,
            "last_error": None,
        }
    return {
        "provider": doc["provider"],
        "mode": doc.get("mode", "TEST"),
        "status": doc.get("status", "NOT_CONNECTED"),
        "key_id_masked": mask_key_id(doc.get("key_id")),
        "webhook_configured": bool(doc.get("webhook_secret")),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "last_successful_event_at": doc.get("last_successful_event_at"),
        "last_error_at": doc.get("last_error_at"),
        "last_error": doc.get("last_error"),
    }

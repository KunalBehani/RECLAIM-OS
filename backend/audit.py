import uuid

from constants import now_iso
from database import db


async def write_audit(
    *,
    case_id=None,
    actor="system",
    event_type,
    reason="",
    before_state=None,
    after_state=None,
    policy_rule_reference=None,
    model_version=None,
    related=None,
):
    doc = {
        "event_id": f"evt_{uuid.uuid4().hex[:12]}",
        "case_id": case_id,
        "timestamp": now_iso(),
        "actor": actor,
        "event_type": event_type,
        "reason": reason,
        "before_state": before_state,
        "after_state": after_state,
        "policy_rule_reference": policy_rule_reference,
        "model_version": model_version,
        "related": related or {},
    }
    await db.audit_events.insert_one(doc)
    doc.pop("_id", None)
    return doc

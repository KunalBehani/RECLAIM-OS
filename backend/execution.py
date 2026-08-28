import uuid

from audit import write_audit
from constants import now_iso
from database import db
from policy import ACTION_CATALOG


async def execute_action(
    case_id: str,
    action_type: str,
    idempotency_key: str,
    actor: str,
    expected_incremental_value: float = 0.0,
    estimated_cost: float | None = None,
    policy_result: dict | None = None,
    approval_status: str = "AUTO_APPROVED",
):
    """Adapter-based execution layer.

    Only the SIMULATED adapter is configured in this environment: no real
    financial action is performed, and every executed action is labeled
    SIMULATED. Recovery is only counted after independent verification
    against source-of-truth payment data. Execution is idempotent on
    `idempotency_key`.
    """
    existing = await db.recovery_actions.find_one({"idempotency_key": idempotency_key}, {"_id": 0})
    if existing:
        await write_audit(
            case_id=case_id,
            actor=actor,
            event_type="DUPLICATE_EXECUTION_BLOCKED",
            reason=f"Idempotency key '{idempotency_key}' already executed; returning the original action. No duplicate action performed.",
            after_state={"action_id": existing["action_id"], "idempotency_key": idempotency_key},
        )
        return {"action": existing, "duplicate": True}

    spec = ACTION_CATALOG[action_type]
    now = now_iso()
    doc = {
        "action_id": f"act_{uuid.uuid4().hex[:12]}",
        "case_id": case_id,
        "action_type": action_type,
        "label": spec["label"],
        "scheduled_time": now,
        "executed_time": now,
        "execution_mode": "SIMULATED",
        "simulated": True,
        "approval_status": approval_status,
        "policy_result": (policy_result or {}).get("decision"),
        "policy_reasons": (policy_result or {}).get("reasons", []),
        "expected_incremental_value": expected_incremental_value,
        "estimated_cost": spec["estimated_cost"] if estimated_cost is None else estimated_cost,
        "outcome": "PENDING",
        "idempotency_key": idempotency_key,
        "provider_reference": f"SIM-{uuid.uuid4().hex[:8].upper()}",
        "created_at": now,
    }
    await db.recovery_actions.insert_one(doc)
    doc.pop("_id", None)

    await write_audit(
        case_id=case_id,
        actor=actor,
        event_type="ACTION_EXECUTED",
        reason=(
            f"{spec['label']} executed via the SIMULATED adapter (ref {doc['provider_reference']}). "
            "No real financial action occurred. Outcome is PENDING until independently verified "
            "against source-of-truth payment data."
        ),
        after_state={"action_id": doc["action_id"], "action_type": action_type, "execution_mode": "SIMULATED"},
        policy_rule_reference=(policy_result or {}).get("rule_version"),
    )
    await db.recovery_cases.update_one(
        {"case_id": case_id},
        {"$set": {"status": "ACTION_EXECUTED", "action_status": "EXECUTED", "verification_status": "PENDING", "last_evaluated_at": now}},
    )
    return {"action": doc, "duplicate": False}

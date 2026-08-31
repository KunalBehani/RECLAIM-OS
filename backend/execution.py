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

    # Phase 1.5: genuine customer-facing execution for provider-sourced cases
    # when the notification channel is configured. Everything else keeps the
    # existing SIMULATED adapter behavior unchanged.
    if action_type == "SEND_RECOVERY_LINK":
        case = await db.recovery_cases.find_one({"case_id": case_id}, {"_id": 0})
        if case and case.get("source") in ("RAZORPAY_TEST", "RAZORPAY_LIVE"):
            recipient = await _customer_email(case)
            resend_cfg = await db.integrations.find_one({"provider": "resend"}, {"_id": 0})
            if recipient and resend_cfg and resend_cfg.get("enabled"):
                return await _execute_real_notification(
                    case, spec, idempotency_key, actor, expected_incremental_value,
                    estimated_cost, policy_result, approval_status, recipient, now,
                )

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


async def _customer_email(case: dict) -> str | None:
    """Customer contact from server-side payment records only (never caller input)."""
    att = await db.payment_attempts.find_one(
        {"order_id": case.get("order_key"), "email": {"$ne": None}},
        {"_id": 0, "email": 1},
        sort=[("timestamp", -1)],
    )
    return (att or {}).get("email")


async def _execute_real_notification(case, spec, idempotency_key, actor, expected_incremental_value,
                                     estimated_cost, policy_result, approval_status, recipient, now):
    """Genuine customer-facing recovery email. On provider send failure the action
    is recorded with executed_time=None (never executed → can never earn attribution)
    and the case does NOT advance to ACTION_EXECUTED."""
    from notifications.base import NotificationError, mask_email
    from notifications.resend_adapter import ResendNotificationAdapter
    from notifications.templates import build_recovery_email, recovery_pay_url
    from routes_recovery import new_recovery_token

    token = new_recovery_token()
    from datetime import datetime, timedelta, timezone
    expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    subject, html = build_recovery_email(
        amount_inr=float(case.get("amount_at_risk") or 0),
        order_id=case.get("order_key"),
        pay_url=recovery_pay_url(token),
    )
    doc = {
        "action_id": f"act_{uuid.uuid4().hex[:12]}",
        "case_id": case["case_id"],
        "action_type": "SEND_RECOVERY_LINK",
        "label": spec["label"],
        "scheduled_time": now,
        "executed_time": None,
        "execution_mode": "REAL",
        "simulated": False,
        "approval_status": approval_status,
        "policy_result": (policy_result or {}).get("decision"),
        "policy_reasons": (policy_result or {}).get("reasons", []),
        "expected_incremental_value": expected_incremental_value,
        "estimated_cost": spec["estimated_cost"] if estimated_cost is None else estimated_cost,
        "outcome": "PENDING",
        "idempotency_key": idempotency_key,
        "recovery_token": token,
        "expires_at": expires_at,
        "notification": {"channel": "email", "recipient": recipient, "status": "PENDING"},
        "created_at": now,
    }
    try:
        result = await ResendNotificationAdapter().send_recovery_email(recipient=recipient, subject=subject, html=html)
    except NotificationError as exc:
        doc["outcome"] = "DELIVERY_FAILED"
        doc["notification"]["status"] = "FAILED"
        await db.recovery_actions.insert_one(doc)
        doc.pop("_id", None)
        await write_audit(
            case_id=case["case_id"], actor=actor, event_type="NOTIFICATION_FAILED",
            reason=f"Genuine recovery email to {mask_email(recipient)} could not be delivered: {exc}. Action recorded as NOT executed; no attribution is possible.",
            after_state={"action_id": doc["action_id"], "execution_mode": "REAL", "delivery_status": "FAILED"},
        )
        return {"action": doc, "duplicate": False}

    doc["executed_time"] = now
    doc["provider_reference"] = result.provider_reference
    doc["notification"].update({"status": "SENT", "email_id": result.provider_reference, "sent_at": now})
    await db.recovery_actions.insert_one(doc)
    doc.pop("_id", None)
    await write_audit(
        case_id=case["case_id"], actor=actor, event_type="ACTION_EXECUTED",
        reason=(
            f"{spec['label']} executed via the REAL notification adapter — a genuine customer-facing email was "
            f"sent to {mask_email(recipient)} (provider ref {result.provider_reference}). Same-order retry link issued. "
            "Outcome is PENDING until independently verified against source-of-truth payment data."
        ),
        after_state={"action_id": doc["action_id"], "action_type": "SEND_RECOVERY_LINK",
                     "execution_mode": "REAL", "delivery_status": "SENT",
                     "recipient_masked": mask_email(recipient)},
        policy_rule_reference=(policy_result or {}).get("rule_version"),
    )
    await db.recovery_cases.update_one(
        {"case_id": case["case_id"]},
        {"$set": {"status": "ACTION_EXECUTED", "action_status": "EXECUTED", "verification_status": "PENDING", "last_evaluated_at": now}},
    )
    return {"action": doc, "duplicate": False}

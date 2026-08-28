import uuid
from datetime import datetime, timedelta, timezone

from audit import write_audit
from constants import CLOSED_CASE_STATUSES, OPEN_CASE_STATUSES, now_iso, parse_dt
from database import db, get_settings
from execution import execute_action
from intelligence import analyze_case
from metrics import case_title, why_at_risk
from policy import evaluate_policy


def _order_query(order_key: str) -> dict:
    return {"$or": [{"order_id": order_key}, {"invoice_id": order_key}]}


async def log_exception(reason: str, record_ref, detail, source="engine", batch_id=None) -> dict:
    doc = {
        "exception_id": f"exc_{uuid.uuid4().hex[:12]}",
        "reason": reason,
        "record_ref": record_ref,
        "detail": detail,
        "status": "OPEN",
        "source": source,
        "batch_id": batch_id,
        "created_at": now_iso(),
    }
    await db.exceptions.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def process_payment_attempt(attempt: dict, actor="system", allow_llm=True) -> dict:
    """Unified recovery engine entry point. Every normalized payment attempt,
    from any ingestion mode, flows through here."""
    order_key = attempt.get("order_id") or attempt.get("invoice_id")
    if not order_key or attempt.get("amount") is None or not attempt.get("status"):
        exc = await log_exception(
            "AMBIGUOUS_OR_INCOMPLETE_RECORD",
            attempt.get("payment_id"),
            {k: v for k, v in attempt.items() if v is not None},
            attempt.get("source") or "unknown",
            attempt.get("batch_id"),
        )
        await write_audit(
            actor=actor,
            event_type="DATA_EXCEPTION",
            reason="Record lacks order/invoice linkage, amount, or a recognized status. Sent to exception queue; excluded from all financial totals.",
            after_state={"exception_id": exc["exception_id"]},
        )
        return {"result": "exception", "exception_id": exc["exception_id"]}

    existing = await db.payment_attempts.find_one({"payment_id": attempt["payment_id"]}, {"_id": 1})
    if existing:
        await write_audit(
            actor=actor,
            event_type="DUPLICATE_ATTEMPT_BLOCKED",
            reason=f"payment_id '{attempt['payment_id']}' already recorded; duplicate blocked.",
            related={"order_key": order_key},
        )
        return {"result": "duplicate_attempt", "payment_id": attempt["payment_id"]}

    try:
        await db.payment_attempts.insert_one(attempt)
    except Exception:
        return {"result": "duplicate_attempt", "payment_id": attempt["payment_id"]}
    attempt.pop("_id", None)
    await write_audit(
        actor=actor,
        event_type="PAYMENT_ATTEMPT_RECORDED",
        reason=f"{attempt['status']} payment attempt {attempt['payment_id']} recorded from {attempt.get('source')}.",
        after_state={"payment_id": attempt["payment_id"], "status": attempt["status"], "amount": attempt.get("amount"), "currency": attempt.get("currency")},
        related={"order_key": order_key, "simulated": attempt.get("simulated", False)},
    )

    if attempt["status"] == "failed":
        related = await db.payment_attempts.find(_order_query(order_key), {"_id": 0}).sort("timestamp", 1).to_list(1000)
        successes = [r for r in related if r["status"] == "success"]
        if successes:
            case = await db.recovery_cases.find_one({"order_key": order_key, "status": {"$in": OPEN_CASE_STATUSES}}, {"_id": 0})
            if case:
                close = await close_case_on_success(case, successes[0], actor=actor)
                return {"result": close["result"], "case_id": case["case_id"]}
            await write_audit(
                actor=actor,
                event_type="NATURAL_RECOVERY_DETECTED",
                reason=f"Order {order_key} already has a successful payment on record. NOT revenue at risk; no case created.",
                related={"order_key": order_key},
            )
            return {"result": "naturally_recovered", "order_key": order_key}

        case = await db.recovery_cases.find_one({"order_key": order_key}, {"_id": 0})
        if case:
            await db.recovery_cases.update_one(
                {"case_id": case["case_id"]},
                {"$addToSet": {"payment_attempt_ids": attempt["payment_id"]}, "$set": {"last_evaluated_at": now_iso()}},
            )
            note = (
                f"Additional failed attempt {attempt['payment_id']} linked to existing case. "
                "One case per order: amount at risk is NOT double-counted."
            )
            if case["status"] not in OPEN_CASE_STATUSES:
                note = (
                    f"New failed attempt {attempt['payment_id']} attached to closed case (status {case['status']}). "
                    "No new case created; no double counting."
                )
            await write_audit(case_id=case["case_id"], actor=actor, event_type="CASE_UPDATED", reason=note)
            return {"result": "case_updated", "case_id": case["case_id"]}

        case_doc = {
            "case_id": f"case_{uuid.uuid4().hex[:12]}",
            "order_key": order_key,
            "order_id": attempt.get("order_id"),
            "invoice_id": attempt.get("invoice_id"),
            "customer_reference": attempt.get("customer_reference"),
            "payment_attempt_ids": [attempt["payment_id"]],
            "amount_at_risk": attempt.get("amount"),
            "currency": attempt.get("currency"),
            "status": "OPEN",
            "reason_created": (
                f"Payment attempt {attempt['payment_id']} failed "
                f"({attempt.get('failure_code') or 'no failure code'}) and no successful settlement exists for this order."
            ),
            "created_at": now_iso(),
            "last_evaluated_at": now_iso(),
            "risk_state": "UNRESOLVED",
            "natural_recovery_probability": None,
            "expected_natural_recovery_value": None,
            "recommended_action": None,
            "action_status": "NONE",
            "outcome": "PENDING",
            "verification_status": "UNVERIFIED",
            "confidence": None,
            "confidence_type": None,
            "policy_result": None,
            "action_evaluations": [],
            "diagnosis": None,
            "explanation": None,
            "evidence": [],
            "model_version": None,
            "features": None,
            "source": attempt.get("source"),
            "simulated": attempt.get("simulated", False),
            "recovered_amount": 0.0,
        }
        case_doc["title"] = case_title(case_doc)
        case_doc["why_at_risk"] = why_at_risk(case_doc)
        await db.recovery_cases.insert_one(case_doc)
        case_doc.pop("_id", None)
        await write_audit(
            case_id=case_doc["case_id"],
            actor=actor,
            event_type="CASE_CREATED",
            reason=case_doc["reason_created"],
            after_state={"amount_at_risk": case_doc["amount_at_risk"], "currency": case_doc["currency"], "risk_state": "UNRESOLVED"},
        )
        pipeline = await run_case_pipeline(case_doc["case_id"], actor=actor, allow_llm=allow_llm)
        return {"result": "case_created", "case_id": case_doc["case_id"], "pipeline": pipeline}

    if attempt["status"] == "success":
        case = await db.recovery_cases.find_one({"order_key": order_key, "status": {"$in": OPEN_CASE_STATUSES}}, {"_id": 0})
        if case:
            close = await close_case_on_success(case, attempt, actor=actor)
            return {"result": close["result"], "case_id": case["case_id"]}
        return {"result": "payment_recorded", "order_key": order_key}

    return {"result": "payment_recorded", "order_key": order_key}


async def close_case_on_success(case: dict, success_attempt: dict, actor="verification") -> dict:
    """Independent outcome verification against source-of-truth payment data."""
    executed = await db.recovery_actions.find(
        {"case_id": case["case_id"], "executed_time": {"$ne": None}}, {"_id": 0}
    ).to_list(100)
    success_dt = parse_dt(success_attempt.get("timestamp"))
    created_dt = parse_dt(case.get("created_at"))
    before = {"status": case["status"], "outcome": case.get("outcome"), "verification_status": case.get("verification_status")}
    recovered_amount = round(min(float(case.get("amount_at_risk") or 0), float(success_attempt.get("amount") or 0)), 2)
    evidence = {
        "success_payment_id": success_attempt.get("payment_id"),
        "source": success_attempt.get("source"),
        "success_timestamp": success_attempt.get("timestamp"),
        "simulated": success_attempt.get("simulated", False),
    }

    if success_dt and created_dt and success_dt < created_dt:
        updates = {
            "status": "INVALID",
            "outcome": "INVALID_CASE",
            "verification_status": "VERIFIED",
            "recovered_amount": 0.0,
            "attribution": "NONE",
        }
        reason = (
            f"Successful payment {success_attempt.get('payment_id')} predates case creation; "
            "the order was already recovered. Case INVALIDATED to prevent overclaiming revenue at risk."
        )
    else:
        attributed = None
        if success_dt:
            eligible = [a for a in executed if parse_dt(a.get("executed_time")) and parse_dt(a["executed_time"]) <= success_dt]
            if eligible:
                attributed = sorted(eligible, key=lambda a: a["executed_time"])[-1]["action_type"]
        if attributed:
            updates = {
                "status": "VERIFIED_RECOVERED",
                "outcome": "VERIFIED_RECOVERED",
                "verification_status": "VERIFIED",
                "recovered_amount": recovered_amount,
                "attribution": "SYSTEM_ACTION",
                "attributed_action": attributed,
            }
            reason = (
                f"Successful payment {success_attempt.get('payment_id')} verified AFTER system action {attributed} executed. "
                f"{recovered_amount} {case.get('currency') or ''} counted as VERIFIED recovered revenue.".strip()
            )
        else:
            updates = {
                "status": "NATURALLY_RECOVERED",
                "outcome": "NATURALLY_RECOVERED",
                "verification_status": "VERIFIED",
                "recovered_amount": 0.0,
                "natural_recovered_amount": recovered_amount,
                "attribution": "NONE",
            }
            reason = (
                f"Successful payment {success_attempt.get('payment_id')} verified with no prior system action. "
                "Natural recovery — NOT counted as system-recovered revenue."
            )

    updates.update({"verification_evidence": evidence, "closed_at": now_iso(), "action_status": "CLOSED"})
    await db.recovery_cases.update_one({"case_id": case["case_id"]}, {"$set": updates})
    await write_audit(
        case_id=case["case_id"],
        actor=actor,
        event_type="CASE_CLOSED",
        reason=reason,
        before_state=before,
        after_state={"status": updates["status"], "outcome": updates["outcome"], "recovered_amount": updates.get("recovered_amount")},
        related=evidence,
    )
    result_map = {"VERIFIED_RECOVERED": "verified_recovered", "INVALID": "invalid_case", "NATURALLY_RECOVERED": "closed_natural"}
    return {"result": result_map[updates["status"]], "status": updates["status"], "attributed_action": updates.get("attributed_action")}


async def verify_case(case_id: str, actor="verification") -> dict:
    """Reconcile a case against source-of-truth payment data. Only a verified
    successful settlement closes a case as recovered."""
    case = await db.recovery_cases.find_one({"case_id": case_id}, {"_id": 0})
    if not case:
        return {"error": "case_not_found"}
    if case["status"] in CLOSED_CASE_STATUSES:
        return {"result": "already_closed", "status": case["status"]}

    successes = await db.payment_attempts.find(
        {**_order_query(case["order_key"]), "status": "success"}, {"_id": 0}
    ).sort("timestamp", 1).to_list(10)
    if successes:
        return await close_case_on_success(case, successes[0], actor=actor)

    settings = await get_settings()
    created = parse_dt(case["created_at"])
    window = timedelta(days=float(settings.get("recovery_window_days", 14)))
    if created and datetime.now(timezone.utc) > created + window:
        await db.recovery_cases.update_one(
            {"case_id": case_id},
            {"$set": {"status": "NOT_RECOVERED", "outcome": "NOT_RECOVERED", "verification_status": "VERIFIED", "closed_at": now_iso(), "action_status": "CLOSED"}},
        )
        await write_audit(
            case_id=case_id,
            actor=actor,
            event_type="CASE_CLOSED",
            reason="Recovery window expired with no successful settlement in source-of-truth data. Marked NOT_RECOVERED.",
            before_state={"status": case["status"]},
            after_state={"status": "NOT_RECOVERED"},
        )
        return {"result": "not_recovered"}

    await db.recovery_cases.update_one({"case_id": case_id}, {"$set": {"verification_status": "PENDING"}})
    await write_audit(
        case_id=case_id,
        actor=actor,
        event_type="VERIFICATION_PENDING",
        reason="No successful settlement found yet. Outcome remains PENDING — 0 counted as verified recovery.",
    )
    return {"result": "pending"}


async def run_case_pipeline(case_id: str, actor="system", allow_llm=True) -> dict:
    case = await db.recovery_cases.find_one({"case_id": case_id}, {"_id": 0})
    if not case:
        return {"error": "case_not_found"}
    attempts = await db.payment_attempts.find(_order_query(case["order_key"]), {"_id": 0}).sort("timestamp", 1).to_list(1000)
    settings = await get_settings()
    analysis = await analyze_case(case, attempts, allow_llm=allow_llm)

    new_status = "EVALUATED" if case["status"] == "OPEN" else case["status"]
    await db.recovery_cases.update_one(
        {"case_id": case_id},
        {"$set": {
            "status": new_status,
            "last_evaluated_at": now_iso(),
            "diagnosis": analysis["diagnosis"],
            "natural_recovery_probability": analysis["natural_recovery_probability"],
            "expected_natural_recovery_value": analysis["expected_natural_recovery_value"],
            "action_evaluations": analysis["action_evaluations"],
            "recommended_action": analysis["recommended_action"],
            "selection_reason": analysis["selection_reason"],
            "confidence": analysis["confidence"],
            "confidence_type": analysis["confidence_type"],
            "explanation": analysis["explanation"],
            "evidence": analysis["evidence"],
            "model_version": analysis["model_version"],
            "features": analysis["features"],
        }},
    )
    await write_audit(
        case_id=case_id,
        actor=actor,
        event_type="AI_ANALYSIS_COMPLETED",
        reason=f"{analysis['diagnosis']} Natural recovery baseline: {analysis['natural_recovery_probability']}.",
        after_state={
            "natural_recovery_probability": analysis["natural_recovery_probability"],
            "expected_natural_recovery_value": analysis["expected_natural_recovery_value"],
            "confidence": analysis["confidence"],
            "confidence_type": analysis["confidence_type"],
        },
        model_version=analysis["model_version"],
    )

    rec = analysis["recommended_action"]
    alternatives = [
        {"action_type": e["action_type"], "expected_incremental_value": e["expected_incremental_value"], "p_recovery": e["p_recovery"]}
        for e in analysis["action_evaluations"]
    ]
    await write_audit(
        case_id=case_id,
        actor=actor,
        event_type="ACTION_SELECTED",
        reason=analysis["selection_reason"],
        after_state={"recommended_action": rec, "alternatives_considered": alternatives},
        model_version=analysis["model_version"],
    )

    case_now = await db.recovery_cases.find_one({"case_id": case_id}, {"_id": 0})
    actions = await db.recovery_actions.find({"case_id": case_id}, {"_id": 0}).to_list(100)
    policy_result = evaluate_policy(case_now, rec, actions, settings)
    await db.recovery_cases.update_one({"case_id": case_id}, {"$set": {"policy_result": policy_result}})
    await write_audit(
        case_id=case_id,
        actor="policy-engine",
        event_type="POLICY_DECISION",
        reason="; ".join(r["detail"] for r in policy_result["reasons"]) or "All policy checks passed.",
        after_state={"decision": policy_result["decision"], "action_type": rec},
        policy_rule_reference=policy_result["rule_version"],
    )

    outcome = {"recommended_action": rec, "policy_decision": policy_result["decision"], "executed": False}
    if rec in ("WAIT_NO_ACTION", "ESCALATE_HUMAN", "STOP_RECOVERY"):
        if rec == "ESCALATE_HUMAN":
            await db.recovery_cases.update_one({"case_id": case_id}, {"$set": {"status": "APPROVAL_PENDING", "action_status": "AWAITING_HUMAN"}})
            await write_audit(case_id=case_id, actor=actor, event_type="ESCALATED_TO_HUMAN", reason="Analysis recommends human review.")
        return outcome

    if policy_result["decision"] == "ALLOW" and settings.get("auto_execute"):
        exec_count = len([a for a in actions if a.get("action_type") == rec and a.get("executed_time")])
        eiv = next((e["expected_incremental_value"] for e in analysis["action_evaluations"] if e["action_type"] == rec), 0)
        res = await execute_action(
            case_id,
            rec,
            idempotency_key=f"{case_id}:{rec}:{exec_count + 1}",
            actor=actor,
            expected_incremental_value=eiv,
            policy_result=policy_result,
            approval_status="AUTO_APPROVED",
        )
        outcome["executed"] = not res["duplicate"]
        outcome["action_id"] = res["action"]["action_id"]
        if rec == "SCHEDULED_RECHECK":
            await verify_case(case_id, actor="scheduled-recheck")
    elif policy_result["decision"] == "ALLOW":
        outcome["note"] = "auto_execute disabled; action awaiting manual execution."
    elif policy_result["decision"] == "APPROVAL":
        await db.recovery_cases.update_one({"case_id": case_id}, {"$set": {"status": "APPROVAL_PENDING", "action_status": "AWAITING_APPROVAL"}})
        await write_audit(
            case_id=case_id,
            actor="policy-engine",
            event_type="APPROVAL_REQUIRED",
            reason="; ".join(r["detail"] for r in policy_result["reasons"]),
            after_state={"action_type": rec},
            policy_rule_reference=policy_result["rule_version"],
        )
    elif policy_result["decision"] == "BLOCK":
        await db.recovery_cases.update_one({"case_id": case_id}, {"$set": {"action_status": "BLOCKED"}})
    elif policy_result["decision"] == "STOP":
        await db.recovery_cases.update_one(
            {"case_id": case_id},
            {"$set": {"status": "STOPPED", "action_status": "STOPPED", "outcome": "STOPPED", "closed_at": now_iso()}},
        )
        await write_audit(
            case_id=case_id,
            actor="policy-engine",
            event_type="CASE_STOPPED",
            reason="; ".join(r["detail"] for r in policy_result["reasons"]),
            policy_rule_reference=policy_result["rule_version"],
        )
    return outcome

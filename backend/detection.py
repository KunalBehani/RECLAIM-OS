import uuid
from datetime import datetime, timedelta, timezone

from audit import write_audit
from constants import CLOSED_CASE_STATUSES, OPEN_CASE_STATUSES, now_iso, parse_dt
from database import db, get_settings
from execution import execute_action
from intelligence import analyze_case
from metrics import case_title, why_at_risk
from policy import evaluate_policy

# Payment-attempt state precedence: a more authoritative state can never be
# overwritten by an older/less authoritative one (out-of-order safety).
STATUS_PRECEDENCE = {"success": 3, "failed": 2, "pending": 1}

# Only genuine customer-facing interventions can earn recovery attribution.
# Monitoring actions (e.g. SCHEDULED_RECHECK) never earn attribution.
ATTRIBUTABLE_ACTIONS = {"SAFE_PAYMENT_RETRY", "SEND_RECOVERY_LINK", "CUSTOMER_REMINDER"}


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


async def upsert_payment_attempt(attempt: dict, actor="system") -> dict:
    """Idempotent, precedence-aware write into the payment ledger.
    - New payment_id -> created.
    - Same payment_id with a more authoritative state (or a genuinely newer
      state change) -> updated, lineage preserved.
    - Same/older state -> duplicate/stale, no financial effect.
    """
    existing = await db.payment_attempts.find_one({"payment_id": attempt["payment_id"]}, {"_id": 0})
    lineage = attempt.get("provider_event_ids") or []

    if not existing:
        try:
            await db.payment_attempts.insert_one(attempt)
        except Exception:
            return {"outcome": "duplicate"}
        attempt.pop("_id", None)
        await write_audit(
            actor=actor,
            event_type="PAYMENT_ATTEMPT_RECORDED",
            reason=f"{attempt['status']} payment attempt {attempt['payment_id']} recorded from {attempt.get('source')}.",
            after_state={"payment_id": attempt["payment_id"], "status": attempt["status"], "amount": attempt.get("amount"), "currency": attempt.get("currency")},
            related={"order_key": attempt.get("order_id") or attempt.get("invoice_id"), "simulated": attempt.get("simulated", False), "provider_event_ids": lineage},
        )
        return {"outcome": "created"}

    new_prec = STATUS_PRECEDENCE.get(attempt.get("status"), 0)
    old_prec = STATUS_PRECEDENCE.get(existing.get("status"), 0)
    new_ts = parse_dt(attempt.get("timestamp"))
    old_ts = parse_dt(existing.get("timestamp"))

    if new_prec > old_prec or (new_prec == old_prec and attempt.get("status") != existing.get("status") and new_ts and old_ts and new_ts > old_ts):
        update = {
            k: v
            for k, v in attempt.items()
            if k in ("status", "failure_code", "failure_reason", "payment_method", "timestamp", "captured_at") and v is not None
        }
        update["provider_event_ids"] = sorted(set((existing.get("provider_event_ids") or []) + lineage))
        await db.payment_attempts.update_one({"payment_id": attempt["payment_id"]}, {"$set": update})
        await write_audit(
            actor=actor,
            event_type="PAYMENT_UPDATED",
            reason=f"Payment {attempt['payment_id']} state advanced {existing.get('status')} → {attempt.get('status')} (authoritative update, out-of-order safe).",
            before_state={"status": existing.get("status")},
            after_state={"status": attempt.get("status")},
            related={"order_key": existing.get("order_id") or existing.get("invoice_id"), "provider_event_ids": lineage},
        )
        return {"outcome": "updated"}

    if lineage:
        await db.payment_attempts.update_one(
            {"payment_id": attempt["payment_id"]},
            {"$addToSet": {"provider_event_ids": {"$each": lineage}}},
        )
    stale = new_prec < old_prec or (new_prec == old_prec and attempt.get("status") != existing.get("status"))
    await write_audit(
        actor=actor,
        event_type="STALE_EVENT_IGNORED" if stale else "DUPLICATE_ATTEMPT_BLOCKED",
        reason=(
            f"Event for payment {attempt['payment_id']} carried an older/less authoritative state "
            f"({attempt.get('status')} after {existing.get('status')}); ignored without downgrading."
            if stale
            else f"payment_id '{attempt['payment_id']}' already recorded; duplicate blocked."
        ),
        related={"order_key": existing.get("order_id") or existing.get("invoice_id"), "provider_event_ids": lineage},
    )
    return {"outcome": "stale_ignored" if stale else "duplicate"}


async def process_payment_attempt(attempt: dict, actor="system", allow_llm=True) -> dict:
    """Unified recovery engine entry point. Every normalized payment attempt,
    from any ingestion mode (CSV, simulator, provider webhook), flows through
    the same shared pipeline: upsert -> order reevaluation."""
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

    upsert = await upsert_payment_attempt(attempt, actor)
    if upsert["outcome"] == "duplicate":
        return {"result": "duplicate_attempt", "payment_id": attempt["payment_id"]}
    if upsert["outcome"] == "stale_ignored":
        return {"result": "stale_event_ignored", "payment_id": attempt["payment_id"]}
    return await reevaluate_order(order_key, trigger=attempt, actor=actor, allow_llm=allow_llm)


async def process_normalized_event(event: dict, actor="system", allow_llm=True) -> dict:
    """Single shared pipeline consumed by provider webhooks AND the simulator."""
    if event.get("kind") == "order":
        return await process_normalized_order_event(event, actor)
    return await process_payment_attempt(event["attempt"], actor=actor, allow_llm=allow_llm)


async def process_normalized_order_event(event: dict, actor="system") -> dict:
    order_id = event.get("order_id")
    if not order_id:
        exc = await log_exception("ORDER_EVENT_MISSING_ID", event.get("provider_event_id"), {"event_type": event.get("event_type")}, event.get("source") or "provider")
        return {"result": "exception", "exception_id": exc["exception_id"]}

    existing = await db.orders.find_one({"order_id": order_id}, {"_id": 0})
    precedence = {"created": 1, "attempted": 2, "paid": 3}
    new_status = event.get("status") or "created"
    lineage = [event.get("provider_event_id")] if event.get("provider_event_id") else []

    if not existing:
        doc = {
            "order_id": order_id,
            "provider": event.get("provider"),
            "amount": event.get("amount"),
            "amount_paid": event.get("amount_paid"),
            "amount_due": event.get("amount_due"),
            "currency": event.get("currency"),
            "status": new_status,
            "receipt": event.get("receipt"),
            "provider_created_at": event.get("created_at"),
            "paid_at": now_iso() if new_status == "paid" else None,
            "source": event.get("source"),
            "source_mode": event.get("source_mode"),
            "provider_event_ids": lineage,
            "updated_at": now_iso(),
        }
        await db.orders.insert_one(doc)
        doc.pop("_id", None)
        await write_audit(
            actor=actor,
            event_type="ORDER_LINKED",
            reason=f"Order {order_id} recorded from {event.get('provider')} ({event.get('source_mode')}) with status {new_status}.",
            after_state={"order_id": order_id, "status": new_status, "amount": event.get("amount"), "currency": event.get("currency")},
            related={"provider_event_ids": lineage},
        )
    else:
        if precedence.get(new_status, 0) > precedence.get(existing.get("status"), 0):
            updates = {
                "status": new_status,
                "amount_paid": event.get("amount_paid", existing.get("amount_paid")),
                "amount_due": event.get("amount_due", existing.get("amount_due")),
                "updated_at": now_iso(),
            }
            if new_status == "paid":
                updates["paid_at"] = now_iso()
            await db.orders.update_one({"order_id": order_id}, {"$set": updates, "$addToSet": {"provider_event_ids": {"$each": lineage}}})
            await write_audit(
                actor=actor,
                event_type="ORDER_UPDATED",
                reason=f"Order {order_id} state advanced {existing.get('status')} → {new_status}.",
                before_state={"status": existing.get("status")},
                after_state={"status": new_status},
                related={"provider_event_ids": lineage},
            )
        else:
            await db.orders.update_one({"order_id": order_id}, {"$addToSet": {"provider_event_ids": {"$each": lineage}}})

    if new_status == "paid":
        order_doc = await db.orders.find_one({"order_id": order_id}, {"_id": 0})
        return await reevaluate_order(order_id, trigger=None, actor=actor, order_doc_override=order_doc)
    return {"result": "order_recorded", "order_id": order_id}


async def reevaluate_order(order_key: str, trigger, actor="system", allow_llm=True, order_doc_override=None) -> dict:
    """Order-centric, arrival-order-independent risk evaluation. Recomputes the
    obligation state from ALL related attempts + provider order state."""
    attempts = await db.payment_attempts.find(_order_query(order_key), {"_id": 0}).sort("timestamp", 1).to_list(1000)
    order_doc = order_doc_override
    if order_doc is None:
        order_doc = await db.orders.find_one({"order_id": order_key}, {"_id": 0})
    successes = [a for a in attempts if a["status"] == "success"]
    order_paid = bool(order_doc and order_doc.get("status") == "paid")

    open_case = await db.recovery_cases.find_one({"order_key": order_key, "status": {"$in": OPEN_CASE_STATUSES}}, {"_id": 0})
    any_case = open_case or await db.recovery_cases.find_one({"order_key": order_key}, {"_id": 0})

    if successes or order_paid:
        if open_case:
            return await close_case_on_success(open_case, successes[0] if successes else None, actor=actor, order_doc=order_doc)
        if trigger and trigger.get("status") == "failed" and not any_case:
            await write_audit(
                actor=actor,
                event_type="NATURAL_RECOVERY_DETECTED",
                reason=f"Order {order_key} already has a successful payment on record. NOT revenue at risk; no case created.",
                related={"order_key": order_key},
            )
            return {"result": "naturally_recovered", "order_key": order_key}
        return {"result": "payment_recorded", "order_key": order_key}

    failed = [a for a in attempts if a["status"] == "failed"]
    if not failed:
        return {"result": "payment_recorded", "order_key": order_key}

    if any_case:
        known = set(any_case.get("payment_attempt_ids") or [])
        new_ids = [a["payment_id"] for a in failed if a["payment_id"] not in known]
        if (trigger and trigger.get("status") == "failed") or new_ids:
            updates = {"last_evaluated_at": now_iso()}
            if new_ids:
                await db.recovery_cases.update_one(
                    {"case_id": any_case["case_id"]},
                    {"$addToSet": {"payment_attempt_ids": {"$each": new_ids}}, "$set": updates},
                )
            else:
                await db.recovery_cases.update_one({"case_id": any_case["case_id"]}, {"$set": updates})
            note = (
                f"Additional failed attempt(s) linked to existing case. One case per order: amount at risk is NOT double-counted."
                if any_case["status"] in OPEN_CASE_STATUSES
                else f"New failed attempt attached to closed case (status {any_case['status']}). No new case created; no double counting."
            )
            await write_audit(case_id=any_case["case_id"], actor=actor, event_type="CASE_UPDATED", reason=note)
            return {"result": "case_updated", "case_id": any_case["case_id"]}
        return {"result": "payment_recorded", "order_key": order_key}

    latest_fail = failed[-1]
    case_doc = {
        "case_id": f"case_{uuid.uuid4().hex[:12]}",
        "order_key": order_key,
        "order_id": latest_fail.get("order_id"),
        "invoice_id": latest_fail.get("invoice_id"),
        "customer_reference": latest_fail.get("customer_reference"),
        "payment_attempt_ids": [a["payment_id"] for a in failed],
        "amount_at_risk": latest_fail.get("amount"),
        "currency": latest_fail.get("currency"),
        "status": "OPEN",
        "reason_created": (
            f"Payment attempt {latest_fail['payment_id']} failed "
            f"({latest_fail.get('failure_code') or 'no failure code'}) and no successful settlement exists for this order."
        ),
        "risk_evidence": {
            "payment_failed": True,
            "failure_code": latest_fail.get("failure_code"),
            "order_id": order_key,
            "order_amount": latest_fail.get("amount"),
            "currency": latest_fail.get("currency"),
            "successful_payment_found": False,
            "order_paid": False,
            "observation_window_elapsed": False,
            "failed_attempt_count": len(failed),
        },
        "provider": latest_fail.get("provider"),
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
        "source": latest_fail.get("source"),
        "simulated": latest_fail.get("simulated", False),
        "recovered_amount": 0.0,
        "attribution_strength": None,
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
        after_state={"amount_at_risk": case_doc["amount_at_risk"], "currency": case_doc["currency"], "risk_state": "UNRESOLVED", "risk_evidence": case_doc["risk_evidence"]},
    )
    pipeline = await run_case_pipeline(case_doc["case_id"], actor=actor, allow_llm=allow_llm)
    return {"result": "case_created", "case_id": case_doc["case_id"], "pipeline": pipeline}


async def close_case_on_success(case: dict, success_attempt=None, actor="verification", order_doc=None) -> dict:
    """Independent outcome verification + deterministic attribution tiers.

    STRONG   — settlement directly linked to a RECLAIM action reference.
    MODERATE — settlement observed after a RECLAIM action executed, matching
               the obligation, without a direct reference link.
    NONE     — settlement with no prior system action: natural recovery,
               never counted as system-recovered revenue.
    UNCERTAIN— an action exists but was executed AFTER the settlement;
               attribution is impossible, treated as natural recovery.
    """
    executed = await db.recovery_actions.find(
        {"case_id": case["case_id"], "executed_time": {"$ne": None}}, {"_id": 0}
    ).to_list(100)

    if success_attempt:
        success_amount = float(success_attempt.get("amount") or 0)
        success_dt = parse_dt(success_attempt.get("timestamp"))
        success_ref = success_attempt.get("payment_id")
        evidence = {
            "success_payment_id": success_ref,
            "source": success_attempt.get("source"),
            "success_timestamp": success_attempt.get("timestamp"),
            "simulated": success_attempt.get("simulated", False),
        }
    else:
        success_amount = float((order_doc or {}).get("amount_paid") or (order_doc or {}).get("amount") or 0)
        success_dt = parse_dt((order_doc or {}).get("paid_at")) or parse_dt((order_doc or {}).get("updated_at"))
        success_ref = (order_doc or {}).get("order_id")
        evidence = {
            "success_payment_id": None,
            "order_id": success_ref,
            "source": (order_doc or {}).get("source"),
            "success_timestamp": (order_doc or {}).get("paid_at") or (order_doc or {}).get("updated_at"),
            "simulated": False,
            "evidence_type": "provider_order_paid",
        }

    created_dt = parse_dt(case.get("created_at"))
    # Out-of-order guard: a settlement is only invalid evidence if it predates
    # the last recorded FAILED attempt (the order was already settled when the
    # failure was recorded). Comparing against case-creation time would be
    # wrong — case creation can lag the event by processing time.
    latest_fail = await db.payment_attempts.find(
        {**_order_query(case["order_key"]), "status": "failed"}, {"_id": 0, "timestamp": 1}
    ).sort("timestamp", -1).limit(1).to_list(1)
    reference_dt = parse_dt(latest_fail[0]["timestamp"]) if latest_fail else created_dt
    before = {"status": case["status"], "outcome": case.get("outcome"), "verification_status": case.get("verification_status")}
    amount_at_risk = float(case.get("amount_at_risk") or 0)
    recovered_amount = round(min(amount_at_risk, success_amount), 2)

    if success_dt and reference_dt and success_dt < reference_dt:
        updates = {
            "status": "INVALID",
            "outcome": "INVALID_CASE",
            "verification_status": "VERIFIED",
            "recovered_amount": 0.0,
            "attribution": "NONE",
            "attribution_strength": "NONE",
        }
        reason = (
            f"Successful settlement {success_ref} predates the last recorded failed attempt; "
            "the order was already settled when the failure was recorded. Case INVALIDATED to prevent overclaiming revenue at risk."
        )
    else:
        executed_before = [
            a for a in executed
            if a.get("action_type") in ATTRIBUTABLE_ACTIONS
            and parse_dt(a.get("executed_time")) and success_dt and parse_dt(a["executed_time"]) <= success_dt
        ]
        if executed_before:
            strong = any(a.get("linked_payment_id") and a.get("linked_payment_id") == success_ref for a in executed_before)
            attributed_action = sorted(executed_before, key=lambda a: a["executed_time"])[-1]["action_type"]
            strength = "STRONG" if strong else "MODERATE"
            partial = 0 < success_amount < amount_at_risk
            updates = {
                "status": "VERIFIED_RECOVERED",
                "outcome": "PARTIALLY_RECOVERED" if partial else "VERIFIED_RECOVERED",
                "verification_status": "VERIFIED",
                "recovered_amount": recovered_amount,
                "attribution": "SYSTEM_ACTION",
                "attributed_action": attributed_action,
                "attribution_strength": strength,
            }
            reason = (
                f"Successful settlement {success_ref} ({success_amount}) verified AFTER system action {attributed_action} executed. "
                f"Attribution: {strength}. {recovered_amount} {case.get('currency') or ''} counted as VERIFIED recovered revenue"
                + (f" (partial — {amount_at_risk - recovered_amount:.2f} remains outstanding)." if partial else ".")
            )
        else:
            executed_after = [
                a for a in executed
                if a.get("action_type") in ATTRIBUTABLE_ACTIONS
                and parse_dt(a.get("executed_time")) and success_dt and parse_dt(a["executed_time"]) > success_dt
            ]
            strength = "UNCERTAIN" if executed_after else "NONE"
            updates = {
                "status": "NATURALLY_RECOVERED",
                "outcome": "NATURALLY_RECOVERED",
                "verification_status": "VERIFIED",
                "recovered_amount": 0.0,
                "natural_recovered_amount": recovered_amount,
                "attribution": "NONE",
                "attribution_strength": strength,
            }
            reason = (
                f"Successful settlement {success_ref} verified with no prior system action. "
                "Natural recovery — NOT counted as system-recovered revenue."
                if not executed_after
                else f"Successful settlement {success_ref} occurred BEFORE the system action executed; attribution is impossible (UNCERTAIN). Counted as natural recovery, NOT system-recovered revenue."
            )

    updates.update({"verification_evidence": evidence, "closed_at": now_iso(), "action_status": "CLOSED"})
    await db.recovery_cases.update_one({"case_id": case["case_id"]}, {"$set": updates})
    await write_audit(
        case_id=case["case_id"],
        actor=actor,
        event_type="CASE_CLOSED",
        reason=reason,
        before_state=before,
        after_state={"status": updates["status"], "outcome": updates["outcome"], "recovered_amount": updates.get("recovered_amount"), "attribution_strength": updates.get("attribution_strength")},
        related=evidence,
    )
    result_map = {"VERIFIED_RECOVERED": "verified_recovered", "INVALID": "invalid_case", "NATURALLY_RECOVERED": "closed_natural"}
    return {"result": result_map[updates["status"]], "status": updates["status"], "attributed_action": updates.get("attributed_action"), "attribution_strength": updates.get("attribution_strength")}


async def verify_case(case_id: str, actor="verification") -> dict:
    """Reconcile a case against source-of-truth payment data. Only a verified
    successful settlement closes a case as recovered. Never claims recovery."""
    case = await db.recovery_cases.find_one({"case_id": case_id}, {"_id": 0})
    if not case:
        return {"error": "case_not_found"}
    if case["status"] in CLOSED_CASE_STATUSES:
        return {"result": "already_closed", "status": case["status"]}

    successes = await db.payment_attempts.find(
        {**_order_query(case["order_key"]), "status": "success"}, {"_id": 0}
    ).sort("timestamp", 1).to_list(10)
    order_doc = await db.orders.find_one({"order_id": case["order_key"]}, {"_id": 0})
    if successes or (order_doc and order_doc.get("status") == "paid"):
        return await close_case_on_success(case, successes[0] if successes else None, actor=actor, order_doc=order_doc)

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
        # LLM-latency race guard: re-check that the order has not settled while
        # analysis was running, before executing any action.
        settled_attempt = await db.payment_attempts.find_one({**_order_query(case["order_key"]), "status": "success"}, {"_id": 1})
        settled_order = await db.orders.find_one({"order_id": case["order_key"], "status": "paid"}, {"_id": 1})
        if settled_attempt or settled_order:
            outcome["note"] = "Order settled while analysis was running; action NOT executed. Reconciling outcome instead."
            await verify_case(case_id, actor="pre-execution-guard")
            return outcome
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

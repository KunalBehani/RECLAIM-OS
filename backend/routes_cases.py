from fastapi import APIRouter, HTTPException, Request

from audit import write_audit
from auth import get_current_user
from constants import CLOSED_CASE_STATUSES, OPEN_CASE_STATUSES, mask_reference, now_iso
from database import db, get_settings
from detection import run_case_pipeline, verify_case
from execution import execute_action
from metrics import FUNNEL_STAGES, compute_funnel, enrich_case
from policy import ACTION_CATALOG, evaluate_policy

router = APIRouter(tags=["cases"])

STAGE_ORDER = [
    "Event Ingestion",
    "Validation",
    "Risk Detection",
    "Analysis",
    "Action Selection",
    "Policy Decision",
    "Human Review",
    "Execution",
    "Verification",
    "Closure",
]

EVENT_STAGE = {
    "PAYMENT_ATTEMPT_RECORDED": "Event Ingestion",
    "DUPLICATE_WEBHOOK_BLOCKED": "Validation",
    "DUPLICATE_ATTEMPT_BLOCKED": "Validation",
    "DATA_EXCEPTION": "Validation",
    "NATURAL_RECOVERY_DETECTED": "Risk Detection",
    "CASE_CREATED": "Risk Detection",
    "CASE_UPDATED": "Risk Detection",
    "AI_ANALYSIS_COMPLETED": "Analysis",
    "ACTION_SELECTED": "Action Selection",
    "POLICY_DECISION": "Policy Decision",
    "APPROVAL_REQUIRED": "Human Review",
    "ESCALATED_TO_HUMAN": "Human Review",
    "HUMAN_APPROVED": "Human Review",
    "HUMAN_REJECTED": "Human Review",
    "CASE_MARKED_INVALID": "Human Review",
    "ACTION_EXECUTED": "Execution",
    "DUPLICATE_EXECUTION_BLOCKED": "Execution",
    "VERIFICATION_PENDING": "Verification",
    "CASE_CLOSED": "Closure",
    "CASE_STOPPED": "Closure",
    "SETTINGS_UPDATED": "System",
    "WEBHOOK_RECEIVED": "Event Ingestion",
    "WEBHOOK_SIGNATURE_REJECTED": "Validation",
    "DUPLICATE_EVENT_DETECTED": "Validation",
    "EVENT_NORMALIZED": "Validation",
    "PAYMENT_UPDATED": "Event Ingestion",
    "STALE_EVENT_IGNORED": "Validation",
    "ORDER_LINKED": "Event Ingestion",
    "ORDER_UPDATED": "Event Ingestion",
    "RECONCILIATION_STARTED": "Verification",
    "RECONCILIATION_COMPLETED": "Verification",
    "RECONCILIATION_FAILED": "Verification",
    "INTEGRATION_CONFIGURED": "System",
    "INTEGRATION_TEST_SUCCEEDED": "System",
    "INTEGRATION_TEST_FAILED": "System",
    "INTEGRATION_DISCONNECTED": "System",
}


def _mask_case(case: dict) -> dict:
    case = dict(case)
    if case.get("customer_reference"):
        case["customer_reference"] = mask_reference(case["customer_reference"])
    return case


def _sort_cases(cases: list, sort: str) -> list:
    if sort == "oldest":
        return sorted(cases, key=lambda c: c.get("created_at") or "")
    if sort in ("amount_desc", "amount_asc"):
        # Never blend currencies: group by currency, sort within each group.
        reverse = sort == "amount_desc"
        groups = {}
        for case in cases:
            groups.setdefault(case.get("currency") or "UNKNOWN", []).append(case)
        ordered = []
        for currency in sorted(groups):
            ordered.extend(sorted(groups[currency], key=lambda c: float(c.get("amount_at_risk") or 0), reverse=reverse))
        return ordered
    return sorted(cases, key=lambda c: c.get("created_at") or "", reverse=True)


async def _audit_for_case(case: dict) -> list:
    """Complete audit lineage for a case: case-scoped events plus pre-case
    ingestion lineage (WEBHOOK_RECEIVED / EVENT_NORMALIZED) joined via the
    case's provider event ids. Used by both the detail and replay endpoints
    so the two can never drift."""
    audit = await db.audit_events.find({"case_id": case["case_id"]}, {"_id": 0}).sort("timestamp", 1).to_list(500)
    provider_event_ids = [
        e["provider_event_id"]
        for e in await db.provider_events.find(
            {"normalized_order_id": case["order_key"]}, {"_id": 0, "provider_event_id": 1}
        ).to_list(200)
    ]
    if provider_event_ids:
        lineage_audit = await db.audit_events.find(
            {"related.provider_event_id": {"$in": provider_event_ids}, "case_id": None}, {"_id": 0}
        ).sort("timestamp", 1).to_list(100)
        existing_ids = {e["event_id"] for e in audit}
        audit = sorted(audit + [e for e in lineage_audit if e["event_id"] not in existing_ids], key=lambda e: e["timestamp"])
    return audit


@router.get("/cases")
async def list_cases(request: Request, status: str | None = None, outcome: str | None = None,
                     policy: str | None = None, q: str | None = None, stage: str | None = None,
                     source: str | None = None, attributed_action: str | None = None,
                     sort: str = "newest", limit: int = 500):
    await get_current_user(request)
    query = {}
    if status:
        query["status"] = status
    if outcome:
        query["outcome"] = outcome
    if policy:
        query["policy_result.decision"] = policy
    if attributed_action:
        query["attributed_action"] = attributed_action
    if q:
        query["$or"] = [
            {"case_id": {"$regex": q, "$options": "i"}},
            {"order_key": {"$regex": q, "$options": "i"}},
            {"title": {"$regex": q, "$options": "i"}},
            {"payment_attempt_ids": {"$regex": q, "$options": "i"}},
        ]
    if stage:
        if stage in FUNNEL_STAGES:
            all_cases = await db.recovery_cases.find({}, {"_id": 0}).to_list(10000)
            all_actions = await db.recovery_actions.find({}, {"_id": 0}).to_list(10000)
            audit_executed = await db.audit_events.distinct("case_id", {"event_type": "ACTION_EXECUTED", "case_id": {"$ne": None}})
            funnel = compute_funnel(all_cases, all_actions, set(audit_executed))
            query["case_id"] = {"$in": list(funnel["sets"][stage])}
        elif stage == "at_risk":
            query["status"] = {"$in": OPEN_CASE_STATUSES}
        elif stage in ("stopped", "invalid"):
            query["status"] = stage.upper()
        elif stage == "blocked":
            query["status"] = {"$in": OPEN_CASE_STATUSES}
            query["policy_result.decision"] = "BLOCK"

    cases = await db.recovery_cases.find(query, {"_id": 0}).to_list(5000)
    enriched = [_mask_case(enrich_case(c)) for c in cases]
    if source:
        enriched = [c for c in enriched if c["source_category"] == source]
    enriched = _sort_cases(enriched, sort)
    return {"cases": enriched[: min(limit, 5000)]}


@router.get("/cases/{case_id}")
async def get_case(case_id: str, request: Request):
    await get_current_user(request)
    case = await db.recovery_cases.find_one({"case_id": case_id}, {"_id": 0})
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    attempts = await db.payment_attempts.find(
        {"$or": [{"order_id": case["order_key"]}, {"invoice_id": case["order_key"]}]}, {"_id": 0}
    ).sort("timestamp", 1).to_list(1000)
    actions = await db.recovery_actions.find({"case_id": case_id}, {"_id": 0}).sort("created_at", 1).to_list(100)
    audit = await _audit_for_case(case)
    for a in attempts:
        if a.get("customer_reference"):
            a["customer_reference"] = mask_reference(a["customer_reference"])
    provider_events = await db.provider_events.find(
        {"normalized_order_id": case["order_key"]}, {"_id": 0}
    ).sort("received_at", 1).to_list(200)
    return {
        "case": _mask_case(enrich_case(case)),
        "attempts": attempts,
        "actions": actions,
        "audit_trail": audit,
        "provider_events": provider_events,
    }


@router.get("/cases/{case_id}/replay")
async def decision_replay(case_id: str, request: Request):
    await get_current_user(request)
    case = await db.recovery_cases.find_one({"case_id": case_id}, {"_id": 0})
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    audit = await _audit_for_case(case)
    steps = []
    for event in audit:
        steps.append({
            "event_id": event["event_id"],
            "stage": EVENT_STAGE.get(event["event_type"], "System"),
            "event_type": event["event_type"],
            "timestamp": event["timestamp"],
            "actor": event["actor"],
            "reason": event.get("reason"),
            "before_state": event.get("before_state"),
            "after_state": event.get("after_state"),
            "policy_rule_reference": event.get("policy_rule_reference"),
            "model_version": event.get("model_version"),
        })
    return {"case_id": case_id, "stage_order": STAGE_ORDER, "steps": steps}


@router.post("/cases/{case_id}/evaluate")
async def reevaluate_case(case_id: str, request: Request):
    user = await get_current_user(request)
    case = await db.recovery_cases.find_one({"case_id": case_id}, {"_id": 0})
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    if case["status"] in CLOSED_CASE_STATUSES:
        raise HTTPException(status_code=400, detail=f"Case is closed ({case['status']}); cannot re-evaluate.")
    result = await run_case_pipeline(case_id, actor=user["email"])
    return result


@router.post("/cases/{case_id}/verify")
async def verify_case_endpoint(case_id: str, request: Request):
    user = await get_current_user(request)
    result = await verify_case(case_id, actor=user["email"])
    if result.get("error"):
        raise HTTPException(status_code=404, detail="Case not found")
    return result


@router.post("/cases/{case_id}/execute")
async def manual_execute(case_id: str, request: Request):
    user = await get_current_user(request)
    body = await request.json()
    action_type = body.get("action_type")
    if action_type not in ACTION_CATALOG:
        raise HTTPException(status_code=400, detail="Action not in the approved catalog.")
    case = await db.recovery_cases.find_one({"case_id": case_id}, {"_id": 0})
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    settings = await get_settings()
    actions = await db.recovery_actions.find({"case_id": case_id}, {"_id": 0}).to_list(100)
    policy_result = evaluate_policy(case, action_type, actions, settings)
    await db.recovery_cases.update_one({"case_id": case_id}, {"$set": {"policy_result": policy_result}})
    await write_audit(
        case_id=case_id, actor="policy-engine", event_type="POLICY_DECISION",
        reason="; ".join(r["detail"] for r in policy_result["reasons"]) or "All policy checks passed.",
        after_state={"decision": policy_result["decision"], "action_type": action_type},
        policy_rule_reference=policy_result["rule_version"],
    )
    if policy_result["decision"] in ("BLOCK", "STOP"):
        return {"executed": False, "policy_result": policy_result}
    if policy_result["decision"] == "APPROVAL":
        await db.recovery_cases.update_one({"case_id": case_id}, {"$set": {"status": "APPROVAL_PENDING", "action_status": "AWAITING_APPROVAL"}})
        await write_audit(case_id=case_id, actor="policy-engine", event_type="APPROVAL_REQUIRED",
                          reason="; ".join(r["detail"] for r in policy_result["reasons"]),
                          after_state={"action_type": action_type}, policy_rule_reference=policy_result["rule_version"])
        return {"executed": False, "policy_result": policy_result, "note": "Action requires human approval; routed to review queue."}
    # Same pre-execution settle guard as the autopilot: never fire an action
    # for an order that has already settled.
    settled_attempt = await db.payment_attempts.find_one(
        {"$or": [{"order_id": case["order_key"]}, {"invoice_id": case["order_key"]}], "status": "success"}, {"_id": 1}
    )
    settled_order = await db.orders.find_one({"order_id": case["order_key"], "status": "paid"}, {"_id": 1})
    if settled_attempt or settled_order:
        await verify_case(case_id, actor=f"pre-execution-guard:{user['email']}")
        return {"executed": False, "policy_result": policy_result,
                "note": "Order already settled; action NOT executed. Case reconciled instead."}
    exec_count = len([a for a in actions if a.get("action_type") == action_type and a.get("executed_time")])
    res = await execute_action(
        case_id, action_type,
        idempotency_key=f"{case_id}:{action_type}:{exec_count + 1}",
        actor=user["email"], policy_result=policy_result, approval_status="MANUAL_TRIGGER",
    )
    return {"executed": not res["duplicate"], "duplicate": res["duplicate"], "action": res["action"], "policy_result": policy_result}


@router.post("/cases/{case_id}/review")
async def review_case(case_id: str, request: Request):
    user = await get_current_user(request)
    body = await request.json()
    decision = body.get("decision")
    note = body.get("note") or ""
    case = await db.recovery_cases.find_one({"case_id": case_id}, {"_id": 0})
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    if case["status"] in CLOSED_CASE_STATUSES:
        raise HTTPException(status_code=400, detail=f"Case is already closed ({case['status']}).")
    actor = user["email"]

    if decision in ("approve", "alternate"):
        action_type = case.get("recommended_action") if decision == "approve" else body.get("action_type")
        if action_type not in ACTION_CATALOG or action_type in ("WAIT_NO_ACTION",):
            raise HTTPException(status_code=400, detail="No executable action selected.")
        settings = await get_settings()
        actions = await db.recovery_actions.find({"case_id": case_id}, {"_id": 0}).to_list(100)
        policy_result = evaluate_policy(case, action_type, actions, settings)
        await db.recovery_cases.update_one({"case_id": case_id}, {"$set": {"policy_result": policy_result}})
        if policy_result["decision"] in ("BLOCK", "STOP"):
            await write_audit(case_id=case_id, actor=actor, event_type="HUMAN_APPROVED",
                              reason=f"Human approved {action_type}, but the policy engine overrode: {policy_result['decision']}. Policy cannot be bypassed. {note}",
                              after_state={"decision": policy_result["decision"], "action_type": action_type},
                              policy_rule_reference=policy_result["rule_version"])
            return {"executed": False, "policy_result": policy_result, "note": "Policy engine blocked the action even after human approval."}
        await write_audit(case_id=case_id, actor=actor, event_type="HUMAN_APPROVED",
                          reason=f"Human approved {action_type}. {note}",
                          after_state={"action_type": action_type})
        exec_count = len([a for a in actions if a.get("action_type") == action_type and a.get("executed_time")])
        res = await execute_action(case_id, action_type,
                                   idempotency_key=f"{case_id}:{action_type}:{exec_count + 1}",
                                   actor=actor, policy_result=policy_result, approval_status="HUMAN_APPROVED")
        return {"executed": not res["duplicate"], "duplicate": res["duplicate"], "action": res["action"], "policy_result": policy_result}

    if decision == "reject":
        await db.recovery_cases.update_one({"case_id": case_id},
                                           {"$set": {"status": "EVALUATED", "action_status": "REJECTED", "last_evaluated_at": now_iso()}})
        await write_audit(case_id=case_id, actor=actor, event_type="HUMAN_REJECTED",
                          reason=f"Recommended action rejected by reviewer. {note}",
                          before_state={"recommended_action": case.get("recommended_action")})
        return {"executed": False, "status": "EVALUATED", "action_status": "REJECTED"}

    if decision == "invalid":
        await db.recovery_cases.update_one({"case_id": case_id},
                                           {"$set": {"status": "INVALID", "outcome": "INVALID_CASE", "action_status": "CLOSED",
                                                     "verification_status": "VERIFIED", "recovered_amount": 0.0, "closed_at": now_iso()}})
        await write_audit(case_id=case_id, actor=actor, event_type="CASE_MARKED_INVALID",
                          reason=f"Reviewer marked case invalid; removed from revenue-at-risk totals. {note}",
                          before_state={"status": case["status"], "amount_at_risk": case.get("amount_at_risk")},
                          after_state={"status": "INVALID"})
        return {"executed": False, "status": "INVALID"}

    if decision == "stop":
        await db.recovery_cases.update_one({"case_id": case_id},
                                           {"$set": {"status": "STOPPED", "outcome": "STOPPED", "action_status": "STOPPED", "closed_at": now_iso()}})
        await write_audit(case_id=case_id, actor=actor, event_type="CASE_STOPPED",
                          reason=f"Recovery stopped by human reviewer. {note}",
                          before_state={"status": case["status"]}, after_state={"status": "STOPPED"})
        return {"executed": False, "status": "STOPPED"}

    raise HTTPException(status_code=400, detail="decision must be approve, alternate, reject, invalid or stop")


@router.get("/review/queue")
async def review_queue(request: Request):
    await get_current_user(request)
    pending = await db.recovery_cases.find({"status": "APPROVAL_PENDING"}, {"_id": 0}).sort("created_at", -1).to_list(100)
    exceptions = await db.exceptions.find({"status": "OPEN"}, {"_id": 0}).sort("created_at", -1).to_list(100)
    exceptions_total = await db.exceptions.count_documents({"status": "OPEN"})
    return {
        "approval_pending": [_mask_case(enrich_case(c)) for c in pending],
        "exceptions": exceptions,
        "exceptions_truncated": exceptions_total > len(exceptions),
        "counts": {"approval_pending": len(pending), "exceptions": exceptions_total},
    }


@router.post("/exceptions/{exception_id}/resolve")
async def resolve_exception(exception_id: str, request: Request):
    user = await get_current_user(request)
    result = await db.exceptions.update_one({"exception_id": exception_id}, {"$set": {"status": "RESOLVED", "resolved_by": user["email"], "resolved_at": now_iso()}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Exception not found")
    return {"status": "RESOLVED", "exception_id": exception_id}

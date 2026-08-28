from fastapi import APIRouter, Request

from auth import get_current_user
from constants import now_iso
from database import db
from metrics import (
    FUNNEL_META,
    FUNNEL_STAGES,
    case_title,
    compute_charts,
    compute_funnel,
    compute_kpis,
    compute_policy_activity,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary")
async def dashboard_summary(request: Request, days: int = 30):
    """Every number is computed live from case/action/attempt/audit records.
    Nothing is hardcoded or separately maintained."""
    await get_current_user(request)
    days = min(max(int(days), 1), 365)
    cases = await db.recovery_cases.find({}, {"_id": 0}).to_list(10000)
    actions = await db.recovery_actions.find({}, {"_id": 0}).to_list(10000)
    attempts = await db.payment_attempts.find({}, {"_id": 0}).to_list(20000)
    exceptions_open = await db.exceptions.count_documents({"status": "OPEN"})
    policy_events = await db.audit_events.find(
        {"event_type": {"$in": ["POLICY_DECISION", "APPROVAL_REQUIRED", "HUMAN_APPROVED", "HUMAN_REJECTED", "CASE_STOPPED"]}},
        {"_id": 0},
    ).to_list(20000)
    # Execution evidence lives in the immutable audit trail; action records may
    # have been purged by test cleanup, so the audit log is authoritative.
    audit_executed = await db.audit_events.distinct("case_id", {"event_type": "ACTION_EXECUTED", "case_id": {"$ne": None}})

    funnel = compute_funnel(cases, actions, set(audit_executed))
    return {
        "generated_at": now_iso(),
        "days": days,
        "kpis": compute_kpis(cases, actions, exceptions_open),
        "funnel": {
            "order": FUNNEL_STAGES,
            "stages": funnel["stages"],
            "side": funnel["side"],
            "meta": FUNNEL_META,
            "mode": "cumulative",
            "note": "Cumulative 'reached this stage' counts derived from actual case records. Stopped, invalid and currently-blocked cases are reported separately, never forced through the funnel.",
        },
        "charts": compute_charts(cases, attempts, actions, days),
        "policy_activity": compute_policy_activity(policy_events, cases),
    }


@router.get("/cost-ledger")
async def cost_ledger(request: Request):
    """Full lineage for the Verified Net Recovery figure: every executed action
    with its recorded cost, the case it belonged to, and that case's outcome."""
    await get_current_user(request)
    actions = await db.recovery_actions.find({"executed_time": {"$ne": None}}, {"_id": 0}).sort("executed_time", -1).to_list(1000)
    case_ids = list({a["case_id"] for a in actions})
    cases = await db.recovery_cases.find({"case_id": {"$in": case_ids}}, {"_id": 0}).to_list(10000)
    by_id = {c["case_id"]: c for c in cases}

    entries, totals = [], {}
    for action in actions:
        case = by_id.get(action["case_id"], {})
        cost = float(action.get("estimated_cost") or 0)
        ccy = case.get("currency") or "UNKNOWN"
        totals[ccy] = round(totals.get(ccy, 0.0) + cost, 2)
        entries.append({
            "action_id": action["action_id"],
            "case_id": action["case_id"],
            "case_title": case_title(case),
            "case_status": case.get("status"),
            "action_type": action["action_type"],
            "label": action.get("label"),
            "estimated_cost": cost,
            "currency": case.get("currency"),
            "executed_time": action.get("executed_time"),
            "execution_mode": action.get("execution_mode"),
            "simulated": action.get("simulated", False),
            "provider_reference": action.get("provider_reference"),
            "approval_status": action.get("approval_status"),
        })
    return {"entries": entries, "totals": totals, "count": len(entries)}

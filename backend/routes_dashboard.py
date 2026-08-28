from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request

from auth import get_current_user
from constants import OPEN_CASE_STATUSES, parse_dt
from database import db

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _add(money_map: dict, currency, amount: float):
    key = currency or "UNKNOWN"
    money_map[key] = round(money_map.get(key, 0.0) + amount, 2)


def _sub(a: dict, b: dict) -> dict:
    keys = set(a) | set(b)
    return {k: round(a.get(k, 0.0) - b.get(k, 0.0), 2) for k in keys}


@router.get("/summary")
async def dashboard_summary(request: Request):
    await get_current_user(request)
    cases = await db.recovery_cases.find({}, {"_id": 0}).to_list(10000)
    actions = await db.recovery_actions.find({"executed_time": {"$ne": None}}, {"_id": 0}).to_list(10000)
    attempts = await db.payment_attempts.find({}, {"_id": 0}).to_list(20000)
    exceptions_open = await db.exceptions.count_documents({"status": "OPEN"})

    case_by_id = {c["case_id"]: c for c in cases}
    at_risk, expected, gross, costs, natural = {}, {}, {}, {}, {}
    active_cases = 0
    closed_count = 0
    recovered_count = 0

    for case in cases:
        ccy = case.get("currency")
        if case["status"] in OPEN_CASE_STATUSES:
            active_cases += 1
            _add(at_risk, ccy, float(case.get("amount_at_risk") or 0))
            p = None
            for e in case.get("action_evaluations") or []:
                if e.get("action_type") == case.get("recommended_action"):
                    p = e.get("p_recovery")
                    break
            if p is None:
                p = case.get("natural_recovery_probability")
            if p:
                _add(expected, ccy, round(float(case.get("amount_at_risk") or 0) * float(p), 2))
        else:
            closed_count += 1
            if case["status"] == "VERIFIED_RECOVERED":
                recovered_count += 1
                _add(gross, ccy, float(case.get("recovered_amount") or 0))
            elif case["status"] == "NATURALLY_RECOVERED":
                _add(natural, ccy, float(case.get("natural_recovered_amount") or 0))

    for action in actions:
        case = case_by_id.get(action["case_id"])
        _add(costs, (case or {}).get("currency"), float(action.get("estimated_cost") or 0))

    net = _sub(gross, costs)
    recovery_rate = round(recovered_count / closed_count * 100, 1) if closed_count else 0.0

    success_ts_by_order = defaultdict(list)
    for a in attempts:
        if a.get("status") == "success":
            key = a.get("order_id") or a.get("invoice_id")
            if key:
                dt = parse_dt(a.get("timestamp"))
                if dt:
                    success_ts_by_order[key].append(dt)
    unnecessary_actions = 0
    for action in actions:
        case = case_by_id.get(action["case_id"])
        if not case or case["status"] != "NATURALLY_RECOVERED":
            continue
        exec_dt = parse_dt(action.get("executed_time"))
        successes = success_ts_by_order.get(case["order_key"], [])
        if exec_dt and any(s < exec_dt for s in successes):
            unnecessary_actions += 1

    funnel = {
        "detected": len(cases),
        "eligible": len([c for c in cases if c.get("natural_recovery_probability") is not None]),
        "evaluated": len([c for c in cases if c.get("recommended_action")]),
        "approved": len([c for c in cases if (c.get("policy_result") or {}).get("decision") in ("ALLOW", "APPROVAL") or c.get("status") in ("ACTION_EXECUTED", "VERIFYING", "VERIFIED_RECOVERED")]),
        "executed": len({a["case_id"] for a in actions}),
        "verifying": len([c for c in cases if c.get("verification_status") == "PENDING" and c["status"] in OPEN_CASE_STATUSES]),
        "recovered": recovered_count,
    }

    currencies = sorted(set(at_risk) | set(gross) | set(expected))
    days = 30
    today = datetime.now(timezone.utc).date()
    series = {ccy: [] for ccy in currencies}
    for i in range(days - 1, -1, -1):
        day = today - timedelta(days=i)
        for ccy in currencies:
            risk_sum = 0.0
            recovered_sum = 0.0
            for case in cases:
                if (case.get("currency") or "UNKNOWN") != ccy:
                    continue
                created = parse_dt(case.get("created_at"))
                if created and created.date() == day:
                    risk_sum += float(case.get("amount_at_risk") or 0)
                if case["status"] == "VERIFIED_RECOVERED":
                    closed = parse_dt(case.get("closed_at"))
                    if closed and closed.date() == day:
                        recovered_sum += float(case.get("recovered_amount") or 0)
            series[ccy].append({"date": day.isoformat(), "at_risk": round(risk_sum, 2), "verified_recovered": round(recovered_sum, 2)})

    recovery_by_action = defaultdict(dict)
    for case in cases:
        if case["status"] == "VERIFIED_RECOVERED" and case.get("attributed_action"):
            bucket = recovery_by_action[case["attributed_action"]]
            _add(bucket, case.get("currency"), float(case.get("recovered_amount") or 0))

    cases_by_status = defaultdict(int)
    for case in cases:
        cases_by_status[case["status"]] += 1

    failure_reasons = defaultdict(int)
    for a in attempts:
        if a.get("status") == "failed":
            failure_reasons[a.get("failure_code") or "unknown"] += 1

    policy_decisions = defaultdict(int)
    policy_block_rules = defaultdict(int)
    for case in cases:
        pr = case.get("policy_result") or {}
        if pr.get("decision"):
            policy_decisions[pr["decision"]] += 1
            if pr["decision"] in ("BLOCK", "STOP"):
                for reason in pr.get("reasons", []):
                    policy_block_rules[reason.get("rule", "UNKNOWN")] += 1

    return {
        "kpis": {
            "revenue_at_risk": at_risk,
            "expected_recoverable": expected,
            "verified_gross_recovery": gross,
            "action_costs": costs,
            "verified_net_recovery": net,
            "natural_recovered_not_counted": natural,
            "active_cases": active_cases,
            "recovery_rate_pct": recovery_rate,
            "exceptions_open": exceptions_open,
            "actions_taken": len(actions),
            "unnecessary_actions": unnecessary_actions,
            "cases_stopped": cases_by_status.get("STOPPED", 0),
            "cases_escalated": cases_by_status.get("APPROVAL_PENDING", 0),
            "cases_unresolved": active_cases,
            "outcomes_unknown": len([c for c in cases if c.get("verification_status") in ("PENDING", "UNVERIFIED") and c["status"] in OPEN_CASE_STATUSES]),
        },
        "funnel": funnel,
        "charts": {
            "currencies": currencies,
            "timeseries": series,
            "recovery_by_action": [{"action": k, "amounts": v} for k, v in recovery_by_action.items()],
            "cases_by_status": [{"status": k, "count": v} for k, v in cases_by_status.items()],
            "failure_reasons": [{"code": k, "count": v} for k, v in sorted(failure_reasons.items(), key=lambda kv: -kv[1])[:8]],
            "policy_decisions": [{"decision": k, "count": v} for k, v in policy_decisions.items()],
            "policy_block_rules": [{"rule": k, "count": v} for k, v in sorted(policy_block_rules.items(), key=lambda kv: -kv[1])],
        },
    }

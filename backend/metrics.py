"""Dashboard metrics — pure functions over actual case/action/attempt records.

One source of truth: every dashboard number is derived here from the
underlying records. No separately maintained counters, no hardcoded totals.
"""
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from constants import OPEN_CASE_STATUSES, parse_dt

FUNNEL_STAGES = ["detected", "eligible", "evaluated", "policy_decided", "ready", "executed", "verifying", "recovered"]

FUNNEL_META = {
    "detected": {"label": "Detected", "description": "Recovery cases created from genuinely unresolved failed payments."},
    "eligible": {"label": "Eligible", "description": "Cases that passed validation — invalid cases are excluded."},
    "evaluated": {"label": "Evaluated", "description": "Cases with a completed analysis and natural-recovery baseline."},
    "policy_decided": {"label": "Policy Decided", "description": "Cases where the deterministic policy engine ruled on the recommended action."},
    "ready": {"label": "Ready for Execution", "description": "Cases whose action was auto-allowed by policy or approved by a human."},
    "executed": {"label": "Executed", "description": "Cases with at least one executed recovery action (always labeled SIMULATED in this environment)."},
    "verifying": {"label": "Verifying", "description": "Executed cases that entered independent outcome verification."},
    "recovered": {"label": "Verified Recovered", "description": "Cases closed only after a successful settlement was observed in source-of-truth payment data."},
}

KNOWN_FINAL_STATUSES = ("VERIFIED_RECOVERED", "NATURALLY_RECOVERED", "NOT_RECOVERED")

FAILURE_LABELS = {
    "insufficient_funds": "Insufficient funds",
    "do_not_honor": "Do not honor",
    "try_again_later": "Temporary issuer decline",
    "issuer_unavailable": "Issuer unavailable",
    "processing_error": "Processing error",
    "temporarily_unavailable": "Temporarily unavailable",
    "card_declined_permanent": "Card declined (permanent)",
    "stolen_card": "Stolen card",
    "lost_card": "Lost card",
    "fraud": "Fraud block",
    "fraudulent": "Fraud block",
    "invalid_card": "Invalid card",
    "expired_card": "Expired card",
    "card_not_supported": "Card not supported",
    "authentication_failed": "Authentication failed",
    "withdrawal_count_limit_exceeded": "Withdrawal limit exceeded",
    "approval_exceeded": "Approval limit exceeded",
    "unknown": "Unknown reason",
}

SOURCE_CATEGORIES = {
    "CSV_UPLOAD": "IMPORTED",
    "XLSX_UPLOAD": "IMPORTED",
    "WEBHOOK": "LIVE",
    "SIMULATOR": "SIMULATED",
    "TEST": "SIMULATED",
    "RAZORPAY_TEST": "TEST_MODE",
    "RAZORPAY_LIVE": "LIVE",
}


def _add(money_map, currency, amount):
    key = currency or "UNKNOWN"
    money_map[key] = round(money_map.get(key, 0.0) + amount, 2)


def humanize_failure(code):
    if not code:
        return "Unknown reason"
    return FAILURE_LABELS.get(code, str(code).replace("_", " ").capitalize())


def source_category(doc):
    """Consistent source taxonomy: LIVE / TEST MODE / IMPORTED / SIMULATED.
    Simulated always wins — simulated data must never be mistaken for live data."""
    if doc.get("simulated"):
        return "SIMULATED"
    return SOURCE_CATEGORIES.get(doc.get("source"), "IMPORTED")


def case_title(case):
    """Best truthful human-readable title from available data. Never invents
    product, customer, or business context that is not in the source records."""
    if case.get("title"):
        return case["title"]
    if case.get("order_id"):
        return f"Failed Payment for Order {case['order_id']}"
    if case.get("invoice_id"):
        return f"Overdue Invoice {case['invoice_id']}"
    refs = case.get("payment_attempt_ids") or []
    return f"Unresolved Payment — {refs[0]}" if refs else "Unresolved Payment"


def why_at_risk(case):
    if case.get("why_at_risk"):
        return case["why_at_risk"]
    reason = case.get("reason_created") or ""
    match = re.search(r"failed \(([^)]+)\)", reason)
    if match and match.group(1) != "no failure code":
        return f"Payment failed ({humanize_failure(match.group(1))}); no successful replacement payment found"
    return "Payment failed; no successful replacement payment found"


def enrich_case(case):
    enriched = dict(case)
    enriched["title"] = case_title(enriched)
    enriched["source_category"] = source_category(enriched)
    enriched["why_at_risk"] = why_at_risk(enriched)
    return enriched


def compute_funnel(cases, actions, audit_executed_ids=None):
    """Strict lifecycle funnel. Every stage is a cumulative 'reached this stage'
    set, so counts are monotonically non-increasing by construction. Invalid,
    stopped and currently-blocked cases are reported as side stats, never
    forced through the funnel.

    audit_executed_ids: case_ids with ACTION_EXECUTED events in the immutable
    audit trail. The audit log is authoritative execution evidence — it
    survives even if action records are purged."""
    by_id = {c["case_id"]: c for c in cases}
    executed_ids = {a["case_id"] for a in actions if a.get("executed_time") and a.get("case_id") in by_id}
    executed_ids |= {cid for cid in (audit_executed_ids or set()) if cid in by_id}
    approved_via_action = {
        a["case_id"]
        for a in actions
        if a.get("case_id") in by_id
        and (a.get("approval_status") in ("AUTO_APPROVED", "HUMAN_APPROVED", "MANUAL_TRIGGER") or a.get("policy_result") == "ALLOW")
    }

    sets = {"detected": set(by_id)}
    sets["eligible"] = {cid for cid, c in by_id.items() if c["status"] != "INVALID"}
    sets["evaluated"] = {cid for cid in sets["eligible"] if by_id[cid].get("natural_recovery_probability") is not None}
    sets["policy_decided"] = {cid for cid in sets["evaluated"] if by_id[cid].get("policy_result")}
    sets["ready"] = {
        cid
        for cid in sets["policy_decided"]
        if (by_id[cid].get("policy_result") or {}).get("decision") == "ALLOW" or cid in approved_via_action or cid in executed_ids
    }
    sets["executed"] = executed_ids
    sets["verifying"] = {
        cid
        for cid in executed_ids
        if by_id[cid].get("verification_status") == "PENDING" or by_id[cid]["status"] in KNOWN_FINAL_STATUSES
    }
    sets["recovered"] = {cid for cid, c in by_id.items() if c["status"] == "VERIFIED_RECOVERED"}

    for i in range(len(FUNNEL_STAGES) - 1, 0, -1):
        sets[FUNNEL_STAGES[i - 1]] |= sets[FUNNEL_STAGES[i]]

    side = {
        "stopped": len([c for c in cases if c["status"] == "STOPPED"]),
        "invalid": len([c for c in cases if c["status"] == "INVALID"]),
        "blocked": len([
            c for c in cases
            if c["status"] in OPEN_CASE_STATUSES and (c.get("policy_result") or {}).get("decision") == "BLOCK"
        ]),
    }
    return {"stages": {s: len(sets[s]) for s in FUNNEL_STAGES}, "sets": sets, "side": side}


def compute_kpis(cases, actions, exceptions_open):
    case_ccy = {c["case_id"]: c.get("currency") for c in cases}
    at_risk_cases = [c for c in cases if c["status"] in OPEN_CASE_STATUSES]

    at_risk, expected, gross, costs, natural = {}, {}, {}, {}, {}
    for case in at_risk_cases:
        _add(at_risk, case.get("currency"), float(case.get("amount_at_risk") or 0))
        p = None
        for e in case.get("action_evaluations") or []:
            if e.get("action_type") == case.get("recommended_action"):
                p = e.get("p_recovery")
                break
        if p is None:
            p = case.get("natural_recovery_probability")
        if p is not None:
            _add(expected, case.get("currency"), round(float(case.get("amount_at_risk") or 0) * float(p), 2))

    verified_cases = [c for c in cases if c["status"] == "VERIFIED_RECOVERED"]
    for case in verified_cases:
        _add(gross, case.get("currency"), float(case.get("recovered_amount") or 0))
    for case in cases:
        if case["status"] == "NATURALLY_RECOVERED":
            _add(natural, case.get("currency"), float(case.get("natural_recovered_amount") or 0))

    executed_actions = [a for a in actions if a.get("executed_time")]
    for action in executed_actions:
        _add(costs, case_ccy.get(action["case_id"]), float(action.get("estimated_cost") or 0))

    net = {k: round(gross.get(k, 0.0) - costs.get(k, 0.0), 2) for k in set(gross) | set(costs)}

    active_breakdown = Counter(c["status"] for c in at_risk_cases)
    known_final = [c for c in cases if c["status"] in KNOWN_FINAL_STATUSES]
    rate = round(len(verified_cases) / len(known_final) * 100, 1) if known_final else None

    return {
        "revenue_at_risk": at_risk,
        "revenue_at_risk_cases": len(at_risk_cases),
        "expected_recoverable_estimate": expected,
        "verified_gross_recovery": gross,
        "verified_recovered_count": len(verified_cases),
        "action_costs": costs,
        "executed_action_count": len(executed_actions),
        "verified_net_recovery": net,
        "natural_recovered_not_counted": natural,
        "active_cases": len(at_risk_cases),
        "active_breakdown": dict(active_breakdown),
        "recovery_rate_pct": rate,
        "recovery_rate_numerator": len(verified_cases),
        "recovery_rate_denominator": len(known_final),
        "exceptions_open": exceptions_open,
    }


def compute_charts(cases, attempts, actions, days):
    currencies = sorted({(c.get("currency") or "UNKNOWN") for c in cases})
    today = datetime.now(timezone.utc).date()
    series = {ccy: [] for ccy in currencies}
    for i in range(days - 1, -1, -1):
        day = today - timedelta(days=i)
        for ccy in currencies:
            risk_sum = 0.0
            rec_sum = 0.0
            for case in cases:
                if (case.get("currency") or "UNKNOWN") != ccy:
                    continue
                created = parse_dt(case.get("created_at"))
                if created and created.date() == day:
                    risk_sum += float(case.get("amount_at_risk") or 0)
                if case["status"] == "VERIFIED_RECOVERED":
                    closed = parse_dt(case.get("closed_at"))
                    if closed and closed.date() == day:
                        rec_sum += float(case.get("recovered_amount") or 0)
            series[ccy].append({"date": day.isoformat(), "at_risk": round(risk_sum, 2), "verified_recovered": round(rec_sum, 2)})

    recovery_by_action = {}
    for case in cases:
        if case["status"] == "VERIFIED_RECOVERED" and case.get("attributed_action"):
            bucket = recovery_by_action.setdefault(case["attributed_action"], {})
            _add(bucket, case.get("currency"), float(case.get("recovered_amount") or 0))

    cases_by_status = Counter(c["status"] for c in cases)
    failure_counts = Counter((a.get("failure_code") or "unknown") for a in attempts if a.get("status") == "failed")
    source_counts = Counter(source_category(c) for c in cases)

    return {
        "currencies": currencies,
        "timeseries": series,
        "recovery_by_action": [{"action": k, "amounts": v} for k, v in recovery_by_action.items()],
        "cases_by_status": [{"status": k, "count": v} for k, v in cases_by_status.items()],
        "failure_reasons": [{"code": k, "label": humanize_failure(k), "count": v} for k, v in failure_counts.most_common(8)],
        "sources": [{"source": k, "count": v} for k, v in source_counts.items()],
    }


def compute_policy_activity(events, cases):
    """Real policy activity from the audit trail (every decision is recorded),
    plus the rule breakdown from current case-level policy results."""
    decisions = Counter()
    human_approvals = 0
    human_rejections = 0
    approvals_required = 0
    for event in events:
        if event["event_type"] == "POLICY_DECISION":
            decision = (event.get("after_state") or {}).get("decision")
            if decision:
                decisions[decision] += 1
        elif event["event_type"] == "APPROVAL_REQUIRED":
            approvals_required += 1
        elif event["event_type"] == "HUMAN_APPROVED":
            human_approvals += 1
        elif event["event_type"] == "HUMAN_REJECTED":
            human_rejections += 1

    rules = Counter()
    for case in cases:
        policy = case.get("policy_result") or {}
        if policy.get("decision") in ("BLOCK", "STOP"):
            for reason in policy.get("reasons", []):
                rules[reason.get("rule", "UNKNOWN")] += 1

    return {
        "decisions": [{"decision": k, "count": v} for k, v in decisions.items()],
        "total_decisions": sum(decisions.values()),
        "approvals_required": approvals_required,
        "human_approvals": human_approvals,
        "human_rejections": human_rejections,
        "block_rules": [{"rule": k, "count": v} for k, v in rules.most_common()],
    }

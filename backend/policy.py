from datetime import datetime, timedelta, timezone

from constants import CLOSED_CASE_STATUSES, RULE_VERSION, parse_dt

ACTION_CATALOG = {
    "WAIT_NO_ACTION": {
        "label": "Wait / No Action",
        "description": "Do nothing and let natural recovery play out. Re-evaluated on new data.",
        "estimated_cost": 0.0,
        "max_executions": 0,
        "cooldown_hours": 0,
        "requires_approval": False,
        "approval_above_threshold": False,
        "money_touching": False,
        "terminal": False,
    },
    "SCHEDULED_RECHECK": {
        "label": "Scheduled Recheck",
        "description": "Re-verify the order settlement state from source-of-truth data.",
        "estimated_cost": 0.0,
        "max_executions": 5,
        "cooldown_hours": 6,
        "requires_approval": False,
        "approval_above_threshold": False,
        "money_touching": False,
        "terminal": False,
    },
    "SAFE_PAYMENT_RETRY": {
        "label": "Safe Payment Retry",
        "description": "Retry the charge through the authorized payment provider. Never charges without authorization.",
        "estimated_cost": 25.0,
        "max_executions": 3,
        "cooldown_hours": 24,
        "requires_approval": False,
        "approval_above_threshold": True,
        "money_touching": True,
        "terminal": False,
    },
    "SEND_RECOVERY_LINK": {
        "label": "Send Payment Recovery Link",
        "description": "Send the customer a fresh payment link via a configured authorized integration.",
        "estimated_cost": 12.0,
        "max_executions": 2,
        "cooldown_hours": 48,
        "requires_approval": False,
        "approval_above_threshold": True,
        "money_touching": True,
        "terminal": False,
    },
    "CUSTOMER_REMINDER": {
        "label": "Approved Customer Reminder",
        "description": "Send an approved dunning reminder message to the customer.",
        "estimated_cost": 4.0,
        "max_executions": 2,
        "cooldown_hours": 24,
        "requires_approval": False,
        "approval_above_threshold": False,
        "money_touching": False,
        "terminal": False,
    },
    "ESCALATE_HUMAN": {
        "label": "Escalate to Human Review",
        "description": "Route the case to the human review queue.",
        "estimated_cost": 0.0,
        "max_executions": 1,
        "cooldown_hours": 0,
        "requires_approval": False,
        "approval_above_threshold": False,
        "money_touching": False,
        "terminal": False,
    },
    "STOP_RECOVERY": {
        "label": "Stop Recovery",
        "description": "Permanently halt all recovery activity for this case.",
        "estimated_cost": 0.0,
        "max_executions": 1,
        "cooldown_hours": 0,
        "requires_approval": False,
        "approval_above_threshold": False,
        "money_touching": False,
        "terminal": True,
    },
}

CONTROL_ACTIONS = {"WAIT_NO_ACTION", "STOP_RECOVERY", "ESCALATE_HUMAN"}


def compute_eiv(amount: float, p_action: float, p_natural: float, cost: float) -> float:
    p_action = max(0.0, min(1.0, p_action))
    p_natural = max(0.0, min(1.0, p_natural))
    return round(amount * (p_action - p_natural) - cost, 2)


def evaluate_policy(case: dict, action_type: str, actions: list, settings: dict, now=None) -> dict:
    """Deterministic policy gate between AI recommendation and execution.

    Pure function: same inputs always produce the same decision.
    Returns {"decision": ALLOW|BLOCK|APPROVAL|STOP, "reasons": [...], "rule_version": ...}
    """
    now = now or datetime.now(timezone.utc)
    if action_type not in ACTION_CATALOG:
        return {
            "decision": "BLOCK",
            "reasons": [{"rule": "ACTION_NOT_IN_CATALOG", "detail": f"'{action_type}' is not in the approved action catalog"}],
            "rule_version": RULE_VERSION,
        }

    spec = ACTION_CATALOG[action_type]
    stops, blocks, approvals = [], [], []

    if settings.get("emergency_stop") and action_type != "STOP_RECOVERY":
        stops.append({"rule": "EMERGENCY_STOP", "detail": "Global emergency stop is enabled; all autonomous actions are halted"})

    if case.get("status") in CLOSED_CASE_STATUSES and action_type != "STOP_RECOVERY":
        blocks.append({"rule": "CASE_ALREADY_CLOSED", "detail": f"Case is {case.get('status')}; no further recovery actions permitted"})

    if action_type not in CONTROL_ACTIONS:
        created = parse_dt(case.get("created_at")) or now
        window_days = float(settings.get("recovery_window_days", 14))
        if now > created + timedelta(days=window_days):
            stops.append({"rule": "RECOVERY_WINDOW_EXPIRED", "detail": f"Recovery window of {window_days}d has expired"})

        executed_same = [a for a in actions if a.get("action_type") == action_type and a.get("executed_time")]
        if spec["max_executions"] and len(executed_same) >= spec["max_executions"]:
            blocks.append({
                "rule": "MAX_EXECUTIONS_REACHED",
                "detail": f"{action_type} executed {len(executed_same)} time(s); catalog limit is {spec['max_executions']}",
            })

        if spec["cooldown_hours"] and executed_same:
            last_dt = max(filter(None, (parse_dt(a.get("executed_time")) for a in executed_same)), default=None)
            if last_dt and now < last_dt + timedelta(hours=spec["cooldown_hours"]):
                remaining = (last_dt + timedelta(hours=spec["cooldown_hours"]) - now).total_seconds() / 3600
                blocks.append({"rule": "COOLDOWN_ACTIVE", "detail": f"Cooldown active; {remaining:.1f}h remaining of {spec['cooldown_hours']}h"})

        pending_same = [a for a in actions if a.get("action_type") == action_type and not a.get("executed_time") and a.get("outcome") == "PENDING"]
        if pending_same:
            blocks.append({"rule": "DUPLICATE_PENDING_ACTION", "detail": "An identical action is already pending for this case"})

        spent = sum(float(a.get("estimated_cost") or 0) for a in actions if a.get("executed_time"))
        max_cost = float(settings.get("max_total_cost_per_case", 500))
        if spent + spec["estimated_cost"] > max_cost:
            stops.append({"rule": "MAX_INTERVENTION_COST_REACHED", "detail": f"Intervention cost {spent:.2f} + {spec['estimated_cost']:.2f} exceeds cap {max_cost:.2f}"})

        if spec.get("approval_above_threshold") and float(case.get("amount_at_risk") or 0) >= float(settings.get("approval_threshold_amount", 50000)):
            approvals.append({
                "rule": "AMOUNT_ABOVE_APPROVAL_THRESHOLD",
                "detail": f"Amount at risk {case.get('amount_at_risk')} meets/exceeds approval threshold {settings.get('approval_threshold_amount')}",
            })

        conf = case.get("confidence")
        conf_threshold = float(settings.get("confidence_threshold", 0.55))
        if spec.get("money_touching"):
            if conf is None:
                approvals.append({
                    "rule": "LOW_CONFIDENCE_REVIEW",
                    "detail": "No calibrated model confidence available (heuristic assessment only); money-moving actions require human approval",
                })
            elif conf < conf_threshold:
                approvals.append({"rule": "LOW_CONFIDENCE_REVIEW", "detail": f"Model confidence {conf} is below threshold {conf_threshold}"})

        dnc = settings.get("do_not_contact_customers") or []
        if action_type in ("CUSTOMER_REMINDER", "SEND_RECOVERY_LINK") and case.get("customer_reference") in dnc:
            blocks.append({"rule": "DO_NOT_CONTACT", "detail": "Customer is in the do-not-contact list"})

    decision = "STOP" if stops else ("BLOCK" if blocks else ("APPROVAL" if approvals else "ALLOW"))
    return {"decision": decision, "reasons": stops + blocks + approvals, "rule_version": RULE_VERSION}

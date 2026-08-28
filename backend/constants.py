from datetime import datetime, timezone

SUCCESS_STATUSES = {"succeeded", "success", "captured", "paid", "completed"}
FAILED_STATUSES = {"failed", "declined", "error", "rejected", "cancelled", "canceled"}
PENDING_STATUSES = {"pending", "processing", "initiated", "created", "authorized", "requires_action"}

OPEN_CASE_STATUSES = [
    "OPEN",
    "EVALUATED",
    "APPROVAL_PENDING",
    "ACTION_SCHEDULED",
    "ACTION_EXECUTED",
    "VERIFYING",
]
CLOSED_CASE_STATUSES = [
    "VERIFIED_RECOVERED",
    "NATURALLY_RECOVERED",
    "NOT_RECOVERED",
    "STOPPED",
    "INVALID",
]

MODEL_VERSION_LLM = "claude-sonnet-4-6"
MODEL_VERSION_HEURISTIC = "heuristic-fallback-v1"
RULE_VERSION = "policy-v1.0"

DEFAULT_SETTINGS = {
    "key": "policy",
    "emergency_stop": False,
    "auto_execute": True,
    "recovery_window_days": 14,
    "approval_threshold_amount": 50000,
    "confidence_threshold": 0.55,
    "max_total_cost_per_case": 500,
    "do_not_contact_customers": [],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_dt(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def normalize_status(raw):
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in SUCCESS_STATUSES:
        return "success"
    if s in FAILED_STATUSES:
        return "failed"
    if s in PENDING_STATUSES:
        return "pending"
    return None


def mask_reference(ref):
    if not ref:
        return None
    ref = str(ref)
    if "@" in ref:
        name, domain = ref.split("@", 1)
        return f"{name[:2]}***@{domain}"
    if len(ref) <= 4:
        return "****"
    return f"{ref[:3]}***{ref[-2:]}"

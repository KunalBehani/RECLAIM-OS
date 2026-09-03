"""Explicit recovery-case state machine (Phase 2B).

No arbitrary status writes: every case status change must be a valid transition
through assert_transition(), and every transition is audited at the mutation
point. Statuses use the canonical vocabulary already persisted in production;
the Phase-2B conceptual states map onto them as:
  NEW -> OPEN, ANALYZING/POLICY_REVIEW -> OPEN..EVALUATED (in-flight pipeline),
  ACTION_SELECTED/ACTION_PENDING -> EVALUATED, ACTION_EXECUTED -> ACTION_EXECUTED,
  AWAITING_VERIFICATION -> ACTION_EXECUTED with verification_status PENDING,
  VERIFIED_NOT_RECOVERED -> NOT_RECOVERED, SUPPRESSED -> STOPPED,
  EXPIRED/FAILED -> NOT_RECOVERED, CLOSED -> any terminal state.
"""
TERMINAL_STATUSES = {"VERIFIED_RECOVERED", "NATURALLY_RECOVERED", "NOT_RECOVERED", "INVALID", "STOPPED"}

VALID_TRANSITIONS = {
    # OPEN may jump straight to APPROVAL_PENDING (policy routes high-value cases
    # to human review before the EVALUATED write commits) or ACTION_EXECUTED
    # (manual/auto execution on a freshly created case).
    "OPEN": {"EVALUATED", "ACTION_EXECUTED", "APPROVAL_PENDING", "INVALID", "STOPPED", "VERIFIED_RECOVERED", "NATURALLY_RECOVERED", "NOT_RECOVERED"},
    "EVALUATED": {"ACTION_EXECUTED", "APPROVAL_PENDING", "STOPPED", "INVALID",
                  "VERIFIED_RECOVERED", "NATURALLY_RECOVERED", "NOT_RECOVERED"},
    "ACTION_EXECUTED": {"VERIFIED_RECOVERED", "NATURALLY_RECOVERED", "NOT_RECOVERED", "STOPPED", "APPROVAL_PENDING"},
    "APPROVAL_PENDING": {"ACTION_EXECUTED", "EVALUATED", "STOPPED", "VERIFIED_RECOVERED", "NATURALLY_RECOVERED", "NOT_RECOVERED"},
    "VERIFYING": {"VERIFIED_RECOVERED", "NATURALLY_RECOVERED", "NOT_RECOVERED", "STOPPED"},
    "VERIFIED_RECOVERED": set(),
    "NATURALLY_RECOVERED": set(),
    "NOT_RECOVERED": set(),
    "INVALID": set(),
    "STOPPED": set(),
}


def assert_transition(from_status: str, to_status: str) -> None:
    """Raise on any illegal case status transition. Same-status writes are no-ops."""
    if not to_status or from_status == to_status:
        return
    if to_status not in VALID_TRANSITIONS.get(from_status, set()):
        raise ValueError(f"Illegal case state transition: {from_status} -> {to_status}")

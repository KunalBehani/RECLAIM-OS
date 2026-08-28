import asyncio
import json
import os
import uuid
from datetime import datetime, timezone

from constants import MODEL_VERSION_HEURISTIC, MODEL_VERSION_LLM, parse_dt
from policy import ACTION_CATALOG, compute_eiv

SOFT_DECLINE_CODES = {
    "insufficient_funds",
    "do_not_honor",
    "try_again_later",
    "issuer_unavailable",
    "processing_error",
    "temporarily_unavailable",
    "withdrawal_count_limit_exceeded",
    "approval_exceeded",
}
HARD_DECLINE_CODES = {
    "card_declined_permanent",
    "stolen_card",
    "lost_card",
    "fraud",
    "fraudulent",
    "invalid_card",
    "expired_card",
    "card_not_supported",
    "authentication_failed",
    "permanent_decline",
}

RANKABLE_ACTIONS = ["SCHEDULED_RECHECK", "SAFE_PAYMENT_RETRY", "SEND_RECOVERY_LINK", "CUSTOMER_REMINDER"]


def _clamp(p, lo=0.0, hi=1.0):
    return max(lo, min(hi, p))


def build_features(case: dict, attempts: list) -> dict:
    failed = [a for a in attempts if a.get("status") == "failed"]
    last = failed[-1] if failed else {}
    last_fail_dt = parse_dt(last.get("timestamp")) if last.get("timestamp") else None
    hours = 0.0
    if last_fail_dt:
        hours = max(0.0, (datetime.now(timezone.utc) - last_fail_dt).total_seconds() / 3600.0)
    return {
        "failure_code": (last.get("failure_code") or "").lower(),
        "failure_reason": last.get("failure_reason") or "",
        "payment_method": last.get("payment_method") or "unknown",
        "hours_since_failure": round(hours, 1),
        "attempt_count": len(attempts),
        "failed_attempt_count": len(failed),
        "previous_successes": len([a for a in attempts if a.get("status") == "success"]),
        "amount": case.get("amount_at_risk"),
        "currency": case.get("currency") or "UNKNOWN",
    }


def heuristic_natural_probability(features: dict):
    code = features.get("failure_code") or ""
    p = 0.30
    reasons = ["base natural-recovery rate 0.30 (do-nothing baseline)"]
    if code in SOFT_DECLINE_CODES:
        p += 0.15
        reasons.append(f"failure code '{code}' is a soft/retriable decline (+0.15)")
    elif code in HARD_DECLINE_CODES:
        p -= 0.20
        reasons.append(f"failure code '{code}' is a hard decline (-0.20)")
    if features.get("previous_successes", 0) > 0:
        p += 0.10
        reasons.append("prior successful payment exists for this order/customer (+0.10)")
    if features.get("failed_attempt_count", 1) >= 3:
        p -= 0.08
        reasons.append("3+ failed attempts on this order (-0.08)")
    days = features.get("hours_since_failure", 0) / 24.0
    decay = min(0.20, 0.02 * max(0.0, days - 1))
    if decay > 0:
        p -= decay
        reasons.append(f"elapsed-time decay: {days:.1f} days since last failure (-{decay:.2f})")
    if not code:
        p -= 0.05
        reasons.append("no failure code available; lower data confidence (-0.05)")
    return round(_clamp(p, 0.02, 0.90), 3), reasons


def heuristic_action_probabilities(features: dict, p_natural: float) -> dict:
    code = features.get("failure_code") or ""
    soft = code in SOFT_DECLINE_CODES
    hard = code in HARD_DECLINE_CODES
    return {
        "SCHEDULED_RECHECK": round(_clamp(p_natural + 0.02, 0, 0.97), 3),
        "SAFE_PAYMENT_RETRY": round(_clamp(p_natural + (0.18 if soft else 0.02), 0, 0.97), 3),
        "SEND_RECOVERY_LINK": round(_clamp(p_natural + (0.10 if hard else 0.20), 0, 0.97), 3),
        "CUSTOMER_REMINDER": round(_clamp(p_natural + 0.09, 0, 0.97), 3),
    }


def _extract_json(text: str):
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    try:
        return json.loads(cleaned)
    except Exception:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except Exception:
                return None
    return None


async def _llm_case_analysis(features: dict, p_natural_hint: float):
    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        return None
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
    except Exception:
        return None

    system = (
        "You are the analysis engine of RECLAIM OS, a policy-bounded revenue recovery platform. "
        "You estimate recovery probabilities for failed payments. Respond with ONLY valid JSON. No markdown, no prose."
    )
    prompt = f"""Analyze this failed-payment recovery case and return JSON only.

CASE CONTEXT:
{json.dumps(features, indent=2)}

Deterministic baseline estimate of natural recovery probability (no intervention): {p_natural_hint}

Return exactly this JSON schema:
{{
  "diagnosis": "one sentence diagnosing why the payment failed and whether it is recoverable",
  "natural_recovery_probability": 0.0,
  "action_recovery_probabilities": {{"SCHEDULED_RECHECK": 0.0, "SAFE_PAYMENT_RETRY": 0.0, "SEND_RECOVERY_LINK": 0.0, "CUSTOMER_REMINDER": 0.0}},
  "confidence": 0.0,
  "explanation": "2-3 sentence human-readable rationale for the recommendation",
  "evidence": ["signal 1", "signal 2", "signal 3"]
}}

Rules:
- Every probability is the chance the FULL amount is recovered if that action is taken (0.0 to 1.0). Be conservative and realistic.
- SCHEDULED_RECHECK only re-verifies state, so it should barely exceed the natural recovery probability.
- SAFE_PAYMENT_RETRY works mainly for soft declines (insufficient funds, issuer unavailable), not hard declines (stolen card, fraud).
- SEND_RECOVERY_LINK lets the customer pay a different way, so it can help even for hard declines.
- Do not invent actions outside this list."""

    try:
        chat = LlmChat(
            api_key=api_key,
            session_id=f"reclaim-analysis-{uuid.uuid4().hex[:12]}",
            system_message=system,
        ).with_model("anthropic", MODEL_VERSION_LLM)

        async def _call():
            return await chat.send_message(UserMessage(text=prompt))

        raw = await asyncio.wait_for(_call(), timeout=45)
        return _validate_llm_output(_extract_json(raw))
    except Exception:
        return None


def _validate_llm_output(data):
    if not isinstance(data, dict):
        return None
    try:
        probs = data.get("action_recovery_probabilities") or {}
        return {
            "diagnosis": str(data.get("diagnosis") or "")[:500],
            "natural_recovery_probability": _clamp(float(data.get("natural_recovery_probability", 0.3))),
            "action_recovery_probabilities": {
                a: _clamp(float(probs[a])) for a in RANKABLE_ACTIONS if isinstance(probs.get(a), (int, float))
            },
            "confidence": _clamp(float(data.get("confidence", 0.5)), 0.05, 0.99),
            "explanation": str(data.get("explanation") or "")[:1000],
            "evidence": [str(e)[:200] for e in (data.get("evidence") or [])][:6],
        }
    except Exception:
        return None


async def analyze_case(case: dict, attempts: list, allow_llm: bool = True) -> dict:
    """Intelligence layer: diagnosis + natural recovery baseline + action ranking.

    Financial arithmetic (expected incremental value) is always deterministic.
    Probabilities come from Claude Sonnet when available, else the deterministic
    heuristic fallback, and the model version is recorded either way.
    """
    features = build_features(case, attempts)
    p_nat_heuristic, reasons = heuristic_natural_probability(features)
    llm = await _llm_case_analysis(features, p_nat_heuristic) if allow_llm else None

    if llm:
        p_nat = round(_clamp(0.5 * p_nat_heuristic + 0.5 * llm["natural_recovery_probability"], 0.01, 0.95), 3)
        heur_probs = heuristic_action_probabilities(features, p_nat)
        probs = {
            a: round(llm["action_recovery_probabilities"].get(a, heur_probs[a]), 3) for a in RANKABLE_ACTIONS
        }
        diagnosis = llm["diagnosis"]
        explanation = llm["explanation"]
        evidence = llm["evidence"] or reasons[:4]
        confidence = round(llm["confidence"], 2)
        model_version = MODEL_VERSION_LLM
    else:
        p_nat = p_nat_heuristic
        probs = heuristic_action_probabilities(features, p_nat)
        code = features.get("failure_code") or "unknown"
        if code in SOFT_DECLINE_CODES:
            diagnosis = f"Soft decline ({code}); the payment is likely recoverable with a retry or fresh payment link."
        elif code in HARD_DECLINE_CODES:
            diagnosis = f"Hard decline ({code}); a direct retry is unlikely to succeed, a new payment method is needed."
        else:
            diagnosis = f"Failure classified as '{code}'; recoverability estimated from attempt history and elapsed time."
        explanation = (
            "Deterministic fallback analysis (LLM unavailable). Estimates are derived from the failure-code class, "
            "attempt history, prior successes and elapsed time since the last failure."
        )
        evidence = reasons[:4]
        confidence = 0.5
        model_version = MODEL_VERSION_HEURISTIC

    amount = float(case.get("amount_at_risk") or 0)
    evaluations = []
    for action in RANKABLE_ACTIONS:
        spec = ACTION_CATALOG[action]
        p = probs[action]
        eiv = compute_eiv(amount, p, p_nat, spec["estimated_cost"])
        evaluations.append({
            "action_type": action,
            "label": spec["label"],
            "p_recovery": p,
            "uplift": round(p - p_nat, 3),
            "estimated_cost": spec["estimated_cost"],
            "expected_incremental_value": eiv,
            "confidence": confidence,
        })
    evaluations.append({
        "action_type": "WAIT_NO_ACTION",
        "label": ACTION_CATALOG["WAIT_NO_ACTION"]["label"],
        "p_recovery": p_nat,
        "uplift": 0.0,
        "estimated_cost": 0.0,
        "expected_incremental_value": 0.0,
        "confidence": confidence,
    })

    eligible = [e for e in evaluations if e["action_type"] != "WAIT_NO_ACTION" and e["expected_incremental_value"] > 0]
    best = max(eligible, key=lambda e: e["expected_incremental_value"]) if eligible else None
    recommended = best["action_type"] if best else "WAIT_NO_ACTION"
    if best:
        selection_reason = (
            f"{best['label']} has the highest expected incremental value "
            f"({best['expected_incremental_value']}) = recovery probability uplift {best['uplift']} x amount {amount} "
            f"- cost {best['estimated_cost']}, versus the do-nothing baseline."
        )
    else:
        selection_reason = "No permitted action beats the do-nothing baseline on expected incremental value; waiting is optimal."

    return {
        "diagnosis": diagnosis,
        "natural_recovery_probability": p_nat,
        "natural_recovery_reasons": reasons,
        "expected_natural_recovery_value": round(amount * p_nat, 2),
        "action_evaluations": evaluations,
        "recommended_action": recommended,
        "selection_reason": selection_reason,
        "confidence": confidence,
        "explanation": explanation,
        "evidence": evidence,
        "model_version": model_version,
        "features": features,
    }

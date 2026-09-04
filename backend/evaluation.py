"""ML Evaluation Lab & Model Calibration Engine for RECLAIM OS.

Pure analytical functions for evaluating AI predictions against actual historical
recovery outcomes. Provides dataset snapshotting, ground truth labeling, sample size
gating, classification metrics, confusion matrix, precision-recall & ROC curves,
Brier score, Expected Calibration Error (ECE), reliability diagrams, model comparison,
and Platt scaling.
"""
import math
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from constants import OPEN_CASE_STATUSES, now_iso

# Sample size thresholds
MIN_DESCRIPTIVE_SAMPLE = 10     # Below this: INSUFFICIENT_DATA
MIN_STATISTICAL_SAMPLE = 30     # 10 to 30: DESCRIPTIVE ONLY — LOW SAMPLE SIZE
MIN_CALIBRATION_SAMPLE = 50     # Below this: calibration fitting & curves marked unavailable
MIN_WELL_CALIBRATED_SAMPLE = 100 # Minimum sample size to qualify for WELL_CALIBRATED

# Attributable actions allow-list
ATTRIBUTABLE_ACTIONS = {"SAFE_PAYMENT_RETRY", "SEND_RECOVERY_LINK", "CUSTOMER_REMINDER"}


def _safe_float(val, default=0.0) -> float:
    try:
        if val is None:
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _logit(p: float, eps: float = 1e-6) -> float:
    p_clamped = max(eps, min(1.0 - eps, p))
    return math.log(p_clamped / (1.0 - p_clamped))


def _sigmoid(x: float) -> float:
    if x > 35:
        return 1.0
    if x < -35:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def assign_ground_truth_label(case: Dict[str, Any], actions: List[Dict[str, Any]] = None) -> Tuple[Optional[int], str, str]:
    """Assigns an authoritative ground truth binary label to a case.

    Returns:
        (label, label_category, explanation)
        label is 1 (Positive), 0 (Negative), or None (Excluded/Unknown).
    """
    status = case.get("status")
    attribution = case.get("attribution_strength")
    recovered_amount = _safe_float(case.get("recovered_amount"))
    incremental_amount = _safe_float(case.get("incremental_recovered_amount"))
    is_simulated = bool(case.get("simulated"))
    data_stage = case.get("data_stage") or ("SIMULATED" if is_simulated else "TEST")

    # 1. POSITIVE: Verified recovery with genuine attribution
    if status == "VERIFIED_RECOVERED":
        if attribution in ("STRONG", "MODERATE"):
            return 1, "POSITIVE_VERIFIED", f"Verified recovery ({attribution} attribution, ₹{recovered_amount:.2f} recovered)."
        elif is_simulated or data_stage in ("SIMULATED", "LAB", "IMPORTED"):
            # Simulated or imported test scenario where action was verified
            return 1, "POSITIVE_VERIFIED", f"Verified recovery in {data_stage} environment (₹{recovered_amount:.2f} recovered)."
        else:
            return None, "EXCLUDED_UNCERTAIN", "Verified settlement observed, but attribution is uncertain or unproven."

    # 2. NEGATIVE: Unrecovered cases where observation window elapsed or confirmed not recovered
    if status == "NOT_RECOVERED":
        return 0, "NEGATIVE_UNRECOVERED", "Case closed as NOT_RECOVERED (observation window elapsed without settlement)."

    # 3. EXCLUDED: Natural recoveries (tracked separately, excluded from action prediction evaluation)
    if status == "NATURALLY_RECOVERED":
        return None, "EXCLUDED_NATURAL", "Customer settled independently without system attribution (natural recovery)."

    # 4. EXCLUDED: Invalid, Stopped, or Open cases
    if status == "INVALID":
        return None, "EXCLUDED_INVALID", "Marked invalid (e.g. pre-existing settlement or duplicate)."
    if status == "STOPPED":
        return None, "EXCLUDED_STOPPED", "Recovery was stopped by policy or human operator."

    if status in OPEN_CASE_STATUSES:
        return None, "EXCLUDED_OPEN", "Case is currently active and within its observation window."

    return None, "EXCLUDED_UNKNOWN", f"Unrecognized terminal status: {status}"


def extract_evaluation_record(case: Dict[str, Any], actions: List[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Transforms a raw MongoDB case document into an immutable evaluation record."""
    label, label_category, explanation = assign_ground_truth_label(case, actions)
    
    # Extract prediction
    # Recovery likelihood is derived from 1 - P(natural recovery) or the recommended action's P(recovery)
    p_recovery = None
    action_evals = case.get("action_evaluations") or []
    rec_action = case.get("recommended_action")
    if rec_action and action_evals:
        for ev in action_evals:
            if ev.get("action_type") == rec_action:
                p_recovery = ev.get("p_recovery")
                break

    if p_recovery is None and case.get("confidence") is not None:
        p_recovery = _safe_float(case.get("confidence"))

    if p_recovery is None and case.get("natural_recovery_probability") is not None:
        # Default action uplift estimate if specific action probability is missing
        p_nat = _safe_float(case.get("natural_recovery_probability"), 0.3)
        p_recovery = min(0.95, max(0.05, 1.0 - p_nat * 0.7))

    if p_recovery is None:
        p_recovery = 0.5  # Neutral uncalibrated prior if no prediction data exists

    p_recovery = round(max(0.0, min(1.0, float(p_recovery))), 4)

    # Determine data_stage
    data_stage = case.get("data_stage")
    if not data_stage:
        if case.get("is_lab") or str(case.get("order_key") or "").startswith("order_LAB"):
            data_stage = "LAB"
        elif case.get("simulated") or case.get("source") in ("SIMULATOR", "TEST"):
            data_stage = "SIMULATED"
        elif case.get("source") in ("CSV_UPLOAD", "XLSX_UPLOAD", "FILE_IMPORT"):
            data_stage = "IMPORTED"
        elif case.get("source") == "RAZORPAY_LIVE" or case.get("provider_mode") == "LIVE":
            data_stage = "LIVE"
        else:
            data_stage = "TEST"

    model_ver = case.get("model_version") or "heuristic-fallback-v1"

    # Action executed details
    action_executed = None
    if actions:
        for a in actions:
            if a.get("executed_time"):
                action_executed = a.get("action_type")
                break

    return {
        "case_id": case.get("case_id"),
        "order_key": case.get("order_key"),
        "data_stage": data_stage,
        "is_lab": bool(case.get("is_lab") or data_stage == "LAB"),
        "provider": case.get("provider", "razorpay"),
        "provider_mode": case.get("provider_mode", "TEST"),
        "model_version": model_ver,
        "confidence_type": case.get("confidence_type", "heuristic"),
        "prediction_timestamp": case.get("last_evaluated_at") or case.get("created_at"),
        "predicted_recovery_likelihood": p_recovery,
        "natural_recovery_probability": case.get("natural_recovery_probability"),
        "ground_truth_label": label,
        "label_category": label_category,
        "label_explanation": explanation,
        "actual_status": case.get("status"),
        "actual_outcome": case.get("outcome"),
        "actual_recovered": _safe_float(case.get("recovered_amount")),
        "actual_incremental_recovery": _safe_float(case.get("incremental_recovered_amount")),
        "natural_recovery": _safe_float(case.get("natural_recovered_amount")),
        "action_selected": rec_action,
        "action_executed": action_executed,
        "attribution_strength": case.get("attribution_strength"),
        "amount": _safe_float(case.get("amount_at_risk")),
        "currency": case.get("currency", "INR"),
        "failure_reason": case.get("failure_reason"),
        "failure_code": case.get("failure_code"),
        "payment_method": case.get("payment_method"),
        "policy_decision": (case.get("policy_result") or {}).get("decision"),
        "policy_version": (case.get("policy_result") or {}).get("rule_version", "policy-v1.0"),
        "outcome_timestamp": case.get("closed_at"),
        "created_at": case.get("created_at"),
    }


def calculate_confusion_matrix(records: List[Dict[str, Any]], threshold: float = 0.50) -> Dict[str, Any]:
    """Calculates TP, FP, TN, FN and derived classification metrics at a given threshold."""
    labeled = [r for r in records if r.get("ground_truth_label") in (0, 1)]
    tp, fp, tn, fn = 0, 0, 0, 0
    
    for r in labeled:
        pred_pos = (r["predicted_recovery_likelihood"] >= threshold)
        actual_pos = (r["ground_truth_label"] == 1)
        
        if pred_pos and actual_pos:
            tp += 1
        elif pred_pos and not actual_pos:
            fp += 1
        elif not pred_pos and not actual_pos:
            tn += 1
        else:
            fn += 1

    total = tp + fp + tn + fn
    accuracy = round((tp + tn) / total, 4) if total > 0 else None
    precision = round(tp / (tp + fp), 4) if (tp + fp) > 0 else None
    recall = round(tp / (tp + fn), 4) if (tp + fn) > 0 else None
    f1 = round(2 * precision * recall / (precision + recall), 4) if (precision is not None and recall is not None and (precision + recall) > 0) else None
    specificity = round(tn / (tn + fp), 4) if (tn + fp) > 0 else None
    npv = round(tn / (tn + fn), 4) if (tn + fn) > 0 else None
    fpr = round(fp / (fp + tn), 4) if (fp + tn) > 0 else None

    return {
        "threshold": threshold,
        "total_labeled": total,
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "accuracy": accuracy,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "specificity": specificity,
        "negative_predictive_value": npv,
        "false_positive_rate": fpr,
    }


def calculate_brier_score(records: List[Dict[str, Any]]) -> Optional[float]:
    """Calculates the Brier Score: mean((predicted_prob - actual)^2). Lower is better."""
    labeled = [r for r in records if r.get("ground_truth_label") in (0, 1)]
    if not labeled:
        return None
    sq_errors = [(r["predicted_recovery_likelihood"] - r["ground_truth_label"]) ** 2 for r in labeled]
    return round(sum(sq_errors) / len(sq_errors), 4)


def calculate_calibration_buckets(records: List[Dict[str, Any]], num_bins: int = 10) -> Dict[str, Any]:
    """Calculates 10 reliability diagram buckets and Expected Calibration Error (ECE)."""
    labeled = [r for r in records if r.get("ground_truth_label") in (0, 1)]
    total = len(labeled)
    
    bins = []
    step = 1.0 / num_bins
    total_weighted_gap = 0.0

    for i in range(num_bins):
        low = round(i * step, 2)
        high = round((i + 1) * step, 2)
        
        # Upper bound inclusive on the highest bin
        if i == num_bins - 1:
            in_bin = [r for r in labeled if low <= r["predicted_recovery_likelihood"] <= high]
        else:
            in_bin = [r for r in labeled if low <= r["predicted_recovery_likelihood"] < high]

        count = len(in_bin)
        if count > 0:
            mean_pred = round(sum(r["predicted_recovery_likelihood"] for r in in_bin) / count, 4)
            obs_rate = round(sum(r["ground_truth_label"] for r in in_bin) / count, 4)
            gap = round(abs(mean_pred - obs_rate), 4)
            total_weighted_gap += (count / total) * gap
        else:
            mean_pred = round((low + high) / 2.0, 4)
            obs_rate = None
            gap = 0.0

        bins.append({
            "bin_index": i,
            "bin_lower": low,
            "bin_upper": high,
            "bin_label": f"{low:.1f} - {high:.1f}",
            "count": count,
            "mean_predicted": mean_pred,
            "observed_rate": obs_rate,
            "calibration_gap": gap,
            "ideal": round((low + high) / 2.0, 4),
        })

    ece = round(total_weighted_gap, 4) if total > 0 else None

    # Determine calibration classification status
    if total < MIN_DESCRIPTIVE_SAMPLE:
        status = "INSUFFICIENT_DATA"
        status_reason = f"Sample size ({total}) is below minimum threshold ({MIN_DESCRIPTIVE_SAMPLE}) for calibration assessment."
    elif total < MIN_STATISTICAL_SAMPLE:
        status = "INSUFFICIENT_DATA"
        status_reason = f"Sample size ({total}) is too small for statistically valid calibration claims (min {MIN_STATISTICAL_SAMPLE})."
    elif ece is not None and ece <= 0.05 and total >= MIN_WELL_CALIBRATED_SAMPLE:
        status = "WELL_CALIBRATED"
        status_reason = f"Model is well calibrated (ECE {ece:.4f} <= 0.05 across {total} observations)."
    elif ece is not None and ece <= 0.15:
        status = "PARTIALLY_CALIBRATED"
        status_reason = f"Model is partially calibrated (ECE {ece:.4f} between 0.05 and 0.15 across {total} observations)."
    else:
        status = "POORLY_CALIBRATED"
        status_reason = f"Model shows poor calibration (ECE {ece if ece is not None else 0:.4f} > 0.15)."

    return {
        "bins": bins,
        "expected_calibration_error": ece,
        "calibration_status": status,
        "calibration_status_reason": status_reason,
        "total_labeled_observations": total,
    }


def calculate_threshold_sweep(records: List[Dict[str, Any]], step: float = 0.05) -> Dict[str, Any]:
    """Sweeps thresholds from 0.00 to 1.00 and calculates trade-offs."""
    points = []
    best_f1 = -1.0
    optimal_threshold = 0.50

    curr = 0.0
    while curr <= 1.0001:
        th = round(curr, 2)
        cm = calculate_confusion_matrix(records, threshold=th)
        f1_val = cm["f1"] or 0.0
        if f1_val > best_f1:
            best_f1 = f1_val
            optimal_threshold = th

        points.append({
            "threshold": th,
            "precision": cm["precision"],
            "recall": cm["recall"],
            "f1": cm["f1"],
            "accuracy": cm["accuracy"],
            "specificity": cm["specificity"],
            "tp": cm["tp"],
            "fp": cm["fp"],
            "tn": cm["tn"],
            "fn": cm["fn"],
            "expected_actions": cm["tp"] + cm["fp"],
            "recoveries": cm["tp"],
        })
        curr += step

    return {
        "sweep_points": points,
        "optimal_f1_threshold": optimal_threshold,
        "max_f1_score": round(best_f1, 4) if best_f1 >= 0 else None,
    }


def calculate_curves(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculates ROC and Precision-Recall curve coordinates and AUCs."""
    labeled = [r for r in records if r.get("ground_truth_label") in (0, 1)]
    pos_count = sum(1 for r in labeled if r["ground_truth_label"] == 1)
    neg_count = sum(1 for r in labeled if r["ground_truth_label"] == 0)

    if pos_count == 0 or neg_count == 0:
        return {
            "roc_available": False,
            "pr_available": False,
            "roc_auc": None,
            "pr_auc": None,
            "roc_curve": [],
            "pr_curve": [],
            "reason": "ROC/PR analysis unavailable — requires both positive and negative outcomes.",
        }

    # Sort thresholds from 1.0 down to 0.0 for ROC/PR
    thresholds = [round(i * 0.02, 2) for i in range(51)]
    roc_points = []
    pr_points = []

    for th in thresholds:
        cm = calculate_confusion_matrix(labeled, threshold=th)
        tpr = cm["recall"] if cm["recall"] is not None else 0.0
        fpr = cm["false_positive_rate"] if cm["false_positive_rate"] is not None else 0.0
        prec = cm["precision"] if cm["precision"] is not None else 1.0

        roc_points.append({"threshold": th, "fpr": fpr, "tpr": tpr})
        pr_points.append({"threshold": th, "recall": tpr, "precision": prec})

    # Sort by FPR for ROC AUC integration
    roc_sorted = sorted(roc_points, key=lambda p: (p["fpr"], p["tpr"]))
    roc_auc = 0.0
    for i in range(len(roc_sorted) - 1):
        x1, y1 = roc_sorted[i]["fpr"], roc_sorted[i]["tpr"]
        x2, y2 = roc_sorted[i + 1]["fpr"], roc_sorted[i + 1]["tpr"]
        roc_auc += (x2 - x1) * (y1 + y2) / 2.0
    roc_auc = round(max(0.0, min(1.0, roc_auc)), 4)

    # Sort by Recall for PR AUC integration
    pr_sorted = sorted(pr_points, key=lambda p: (p["recall"], p["precision"]))
    pr_auc = 0.0
    for i in range(len(pr_sorted) - 1):
        x1, y1 = pr_sorted[i]["recall"], pr_sorted[i]["precision"]
        x2, y2 = pr_sorted[i + 1]["recall"], pr_sorted[i + 1]["precision"]
        pr_auc += (x2 - x1) * (y1 + y2) / 2.0
    pr_auc = round(max(0.0, min(1.0, pr_auc)), 4)

    return {
        "roc_available": True,
        "pr_available": True,
        "roc_auc": roc_auc,
        "pr_auc": pr_auc,
        "roc_curve": roc_sorted,
        "pr_curve": pr_sorted,
    }


def calculate_action_performance(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Evaluates historical recovery performance segmented by selected action."""
    action_groups = defaultdict(list)
    for r in records:
        act = r.get("action_selected") or "NO_ACTION"
        action_groups[act].append(r)

    results = []
    for act, recs in action_groups.items():
        total = len(recs)
        verified_count = sum(1 for r in recs if r.get("actual_status") == "VERIFIED_RECOVERED")
        natural_count = sum(1 for r in recs if r.get("actual_status") == "NATURALLY_RECOVERED")
        assisted_count = sum(1 for r in recs if r.get("actual_status") == "VERIFIED_RECOVERED" and r.get("attribution_strength") in ("STRONG", "MODERATE"))
        uncertain_count = sum(1 for r in recs if r.get("actual_status") == "VERIFIED_RECOVERED" and r.get("attribution_strength") == "UNCERTAIN")
        
        gross_rev = round(sum(r.get("actual_recovered", 0.0) for r in recs), 2)
        inc_rev = round(sum(r.get("actual_incremental_recovery", 0.0) for r in recs), 2)
        avg_pred = round(sum(r.get("predicted_recovery_likelihood", 0.0) for r in recs) / total, 4) if total > 0 else 0.0
        rec_rate = round(verified_count / total, 4) if total > 0 else 0.0

        results.append({
            "action_type": act,
            "total_cases": total,
            "verified_recovered_count": verified_count,
            "action_assisted_count": assisted_count,
            "natural_recovered_count": natural_count,
            "uncertain_attribution_count": uncertain_count,
            "recovery_rate": rec_rate,
            "mean_predicted_likelihood": avg_pred,
            "gross_recovered_amount": gross_rev,
            "incremental_recovered_amount": inc_rev,
        })

    return sorted(results, key=lambda a: a["total_cases"], reverse=True)


def calculate_natural_recovery_baseline(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculates the baseline natural recovery rate across eligible cases."""
    total = len(records)
    natural_recs = sum(1 for r in records if r.get("actual_status") == "NATURALLY_RECOVERED")
    action_assisted = sum(1 for r in records if r.get("actual_status") == "VERIFIED_RECOVERED" and r.get("attribution_strength") in ("STRONG", "MODERATE"))
    unrecovered = sum(1 for r in records if r.get("actual_status") == "NOT_RECOVERED")

    baseline_rate = round(natural_recs / total, 4) if total > 0 else 0.0

    return {
        "total_eligible_cases": total,
        "natural_recoveries": natural_recs,
        "action_assisted_recoveries": action_assisted,
        "unrecovered_cases": unrecovered,
        "natural_recovery_baseline_rate": baseline_rate,
        "disclaimer": "ASSOCIATIONAL — NOT CAUSAL: Natural recovery occurred without system action intervention.",
    }


def fit_platt_scaling(records: List[Dict[str, Any]], train_ratio: float = 0.7) -> Dict[str, Any]:
    """Fits Platt scaling (logistic calibration) on a train split if sample size is sufficient."""
    labeled = [r for r in records if r.get("ground_truth_label") in (0, 1)]
    n = len(labeled)
    if n < MIN_CALIBRATION_SAMPLE:
        return {
            "fitted": False,
            "reason": f"Calibration model not fitted — insufficient sample size ({n} < {MIN_CALIBRATION_SAMPLE}).",
            "model_version": None,
        }

    # Deterministic split: first 70% train, last 30% eval
    split_idx = int(n * train_ratio)
    train_data = labeled[:split_idx]
    eval_data = labeled[split_idx:]

    # Logistic regression on logit(p_raw): P_cal = sigmoid(a * logit(p_raw) + b)
    # Simple Newton-Raphson optimizer for 2 parameters (a, b)
    a, b = 1.0, 0.0
    learning_rate = 0.05
    for _ in range(100):
        grad_a, grad_b = 0.0, 0.0
        for r in train_data:
            x = _logit(r["predicted_recovery_likelihood"])
            y = r["ground_truth_label"]
            p_hat = _sigmoid(a * x + b)
            err = p_hat - y
            grad_a += err * x
            grad_b += err
        a -= learning_rate * (grad_a / len(train_data))
        b -= learning_rate * (grad_b / len(train_data))

    version = f"platt-v1-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    
    # Calculate uncalibrated vs calibrated Brier score on evaluation holdout
    eval_raw_brier = calculate_brier_score(eval_data)
    eval_cal_records = [
        {**r, "predicted_recovery_likelihood": round(_sigmoid(a * _logit(r["predicted_recovery_likelihood"]) + b), 4)}
        for r in eval_data
    ]
    eval_cal_brier = calculate_brier_score(eval_cal_records)
    eval_cal_ece = calculate_calibration_buckets(eval_cal_records)["expected_calibration_error"]

    return {
        "fitted": True,
        "model_version": version,
        "method": "platt_scaling",
        "parameters": {"a": round(a, 4), "b": round(b, 4)},
        "train_samples": len(train_data),
        "eval_samples": len(eval_data),
        "eval_raw_brier": eval_raw_brier,
        "eval_calibrated_brier": eval_cal_brier,
        "eval_calibrated_ece": eval_cal_ece,
        "fitted_at": now_iso(),
    }


def evaluate_cohort(
    records: List[Dict[str, Any]],
    cohort_name: str = "GENUINE_TEST",
    threshold: float = 0.50,
    model_version: Optional[str] = None,
) -> Dict[str, Any]:
    """Generates a comprehensive evaluation run report over a list of records."""
    # Filter by model version if requested
    if model_version:
        filtered = [r for r in records if r.get("model_version") == model_version]
    else:
        filtered = records

    total_cases = len(filtered)
    labeled = [r for r in filtered if r.get("ground_truth_label") in (0, 1)]
    pos_count = sum(1 for r in labeled if r["ground_truth_label"] == 1)
    neg_count = sum(1 for r in labeled if r["ground_truth_label"] == 0)
    natural_count = sum(1 for r in filtered if r.get("label_category") == "EXCLUDED_NATURAL")
    excluded_count = sum(1 for r in filtered if r.get("ground_truth_label") is None and r.get("label_category") != "EXCLUDED_NATURAL")

    # Sample size classification
    if len(labeled) < MIN_DESCRIPTIVE_SAMPLE:
        sample_size_status = "INSUFFICIENT_DATA"
        sample_size_message = (
            f"INSUFFICIENT SAMPLE SIZE ({len(labeled)} labeled observations). "
            f"At least {MIN_DESCRIPTIVE_SAMPLE} verified observations are required to calculate descriptive metrics."
        )
    elif len(labeled) < MIN_STATISTICAL_SAMPLE:
        sample_size_status = "DESCRIPTIVE_ONLY"
        sample_size_message = (
            f"DESCRIPTIVE ONLY — LOW SAMPLE SIZE ({len(labeled)} labeled observations). "
            f"Results describe current historical records but lack statistical power (min {MIN_STATISTICAL_SAMPLE})."
        )
    else:
        sample_size_status = "ADEQUATE"
        sample_size_message = f"Sample size ({len(labeled)} observations) is adequate for statistical evaluation."

    cm = calculate_confusion_matrix(filtered, threshold=threshold)
    brier = calculate_brier_score(filtered)
    calibration = calculate_calibration_buckets(filtered)
    sweep = calculate_threshold_sweep(filtered)
    curves = calculate_curves(filtered)
    actions_perf = calculate_action_performance(filtered)
    natural_baseline = calculate_natural_recovery_baseline(filtered)
    platt_fit = fit_platt_scaling(filtered)

    # Model versions in cohort
    model_counts = Counter_models = {}
    for r in filtered:
        mv = r.get("model_version") or "unknown"
        model_counts[mv] = model_counts.get(mv, 0) + 1

    return {
        "evaluation_id": f"eval_{uuid.uuid4().hex[:12]}",
        "cohort": cohort_name,
        "model_version_evaluated": model_version or "ALL_MODELS",
        "model_versions_present": model_counts,
        "evaluated_at": now_iso(),
        "threshold": threshold,
        "sample_size": {
            "total_cases": total_cases,
            "labeled_observations": len(labeled),
            "positive_outcomes": pos_count,
            "negative_outcomes": neg_count,
            "natural_recoveries_excluded": natural_count,
            "other_excluded": excluded_count,
            "status": sample_size_status,
            "message": sample_size_message,
        },
        "classification_metrics": {
            "accuracy": cm["accuracy"],
            "precision": cm["precision"],
            "recall": cm["recall"],
            "f1": cm["f1"],
            "specificity": cm["specificity"],
            "negative_predictive_value": cm["negative_predictive_value"],
            "false_positive_rate": cm["false_positive_rate"],
        },
        "confusion_matrix": {
            "tp": cm["tp"],
            "fp": cm["fp"],
            "tn": cm["tn"],
            "fn": cm["fn"],
        },
        "calibration": {
            "brier_score": brier,
            "expected_calibration_error": calibration["expected_calibration_error"],
            "calibration_status": calibration["calibration_status"],
            "calibration_status_reason": calibration["calibration_status_reason"],
            "reliability_diagram": calibration["bins"],
        },
        "threshold_analysis": sweep,
        "curves": curves,
        "action_performance": actions_perf,
        "natural_recovery_baseline": natural_baseline,
        "calibration_model": platt_fit,
        "is_lab": cohort_name == "LAB",
    }

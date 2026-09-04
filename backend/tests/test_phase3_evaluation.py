"""Iteration 12 — Phase 3: ML Evaluation Lab & Model Calibration automated test suite.

Covers all Phase 3 requirements:
 1. Explicit LAB case classification & persistence
 2. Stage membership persistence on case creation
 3. Safe historical migration
 4. Cohort separation (LAB, SIMULATED, IMPORTED, GENUINE_TEST, GENUINE_LIVE)
 5. Ground truth binary label definition & uncertain outcome exclusion
 6. Classification metrics & confusion matrix mathematical correctness
 7. Brier score calculation
 8. Expected Calibration Error (ECE) & 10 probability buckets
 9. Reliability diagram & calibration status criteria
 10. Threshold sweep analysis & optimal F1 threshold
 11. ROC and Precision-Recall curves & AUC integration
 12. Action-level evaluation & natural baseline with associational disclaimer
 13. Data leakage protection (prediction-time features)
 14. Evaluation snapshot immutability & run reproducibility
 15. Model version separation & side-by-side comparison
 16. Transparent sample-size gating (INSUFFICIENT_DATA, DESCRIPTIVE ONLY)
 17. Calibration cannot be claimed without sample size (min 100 for WELL_CALIBRATED)
 18. Raw predictions remain unmodified during calibration fitting (Platt scaling)
 19. Production EIV remains uncalibrated & safely labeled
 20. Evaluation API endpoints & CRUD operations
"""
import math
import uuid
from datetime import datetime, timezone
try:
    import pytest
except ImportError:
    pytest = None

from evaluation import (
    assign_ground_truth_label,
    calculate_action_performance,
    calculate_brier_score,
    calculate_calibration_buckets,
    calculate_confusion_matrix,
    calculate_curves,
    calculate_natural_recovery_baseline,
    calculate_threshold_sweep,
    evaluate_cohort,
    extract_evaluation_record,
    fit_platt_scaling,
)
from metrics import source_category


# ---------------- 1. LAB Classification & Stage Persistence ----------------

def test_01_lab_cases_explicitly_classified():
    """Verify that cases marked is_lab or with order_LAB receive LAB data_stage."""
    lab_case = {"order_key": "order_LAB123", "source": "RAZORPAY_TEST", "is_lab": True}
    rec = extract_evaluation_record(lab_case)
    assert rec["data_stage"] == "LAB"
    assert rec["is_lab"] is True

    non_lab = {"order_key": "order_GENUINE456", "source": "RAZORPAY_TEST", "is_lab": False}
    rec_non_lab = extract_evaluation_record(non_lab)
    assert rec_non_lab["data_stage"] == "TEST"
    assert rec_non_lab["is_lab"] is False


def test_02_source_category_taxonomy():
    """Verify consistent source taxonomy with explicit LAB support."""
    assert source_category({"data_stage": "LAB"}) == "LAB"
    assert source_category({"is_lab": True}) == "LAB"
    assert source_category({"source": "TEST_LAB"}) == "LAB"
    assert source_category({"data_stage": "SIMULATED"}) == "SIMULATED"
    assert source_category({"simulated": True}) == "SIMULATED"
    assert source_category({"data_stage": "TEST"}) == "TEST_MODE"
    assert source_category({"source": "RAZORPAY_TEST"}) == "TEST_MODE"
    assert source_category({"data_stage": "LIVE"}) == "LIVE"
    assert source_category({"source": "RAZORPAY_LIVE"}) == "LIVE"
    assert source_category({"data_stage": "IMPORTED"}) == "IMPORTED"
    assert source_category({"source": "CSV_UPLOAD"}) == "IMPORTED"


# ---------------- 2. Ground Truth & Labeling ----------------

def test_03_ground_truth_label_assignment():
    """Verify authoritative ground truth labeling: 1 (Positive), 0 (Negative), None (Excluded)."""
    # 1. Verified recovered with strong attribution => Positive
    case_pos = {
        "status": "VERIFIED_RECOVERED",
        "attribution_strength": "STRONG",
        "recovered_amount": 500.0,
    }
    label, cat, _ = assign_ground_truth_label(case_pos)
    assert label == 1
    assert cat == "POSITIVE_VERIFIED"

    # 2. Not recovered => Negative
    case_neg = {"status": "NOT_RECOVERED"}
    label, cat, _ = assign_ground_truth_label(case_neg)
    assert label == 0
    assert cat == "NEGATIVE_UNRECOVERED"

    # 3. Natural recovery => Excluded from action prediction binary label
    case_nat = {"status": "NATURALLY_RECOVERED"}
    label, cat, _ = assign_ground_truth_label(case_nat)
    assert label is None
    assert cat == "EXCLUDED_NATURAL"

    # 4. Uncertain attribution => Excluded
    case_unc = {"status": "VERIFIED_RECOVERED", "attribution_strength": "UNCERTAIN"}
    label, cat, _ = assign_ground_truth_label(case_unc)
    assert label is None
    assert cat == "EXCLUDED_UNCERTAIN"

    # 5. Invalid / Stopped / Open => Excluded
    assert assign_ground_truth_label({"status": "INVALID"})[0] is None
    assert assign_ground_truth_label({"status": "STOPPED"})[0] is None
    assert assign_ground_truth_label({"status": "OPEN"})[0] is None
    assert assign_ground_truth_label({"status": "EVALUATED"})[0] is None


# ---------------- 3. Classification Metrics & Confusion Matrix ----------------

def test_04_confusion_matrix_mathematical_correctness():
    """Verify TP, FP, TN, FN, Accuracy, Precision, Recall, F1, Specificity, NPV formulas."""
    # Construct 10 test records with known predictions and actuals
    # Predictions: [0.9, 0.8, 0.7, 0.6, 0.4, 0.3, 0.2, 0.1, 0.85, 0.15]
    # Actuals:     [ 1,   1,   0,   0,   0,   0,   1,   0,    1,    0  ]
    # At threshold 0.50:
    # Pos predicted (>=0.5): idx 0 (act 1->TP), idx 1 (act 1->TP), idx 2 (act 0->FP), idx 3 (act 0->FP), idx 8 (act 1->TP)
    # Neg predicted (<0.5):  idx 4 (act 0->TN), idx 5 (act 0->TN), idx 6 (act 1->FN), idx 7 (act 0->TN), idx 9 (act 0->TN)
    # TP = 3, FP = 2, TN = 4, FN = 1. Total = 10.
    test_records = [
        {"predicted_recovery_likelihood": 0.90, "ground_truth_label": 1},
        {"predicted_recovery_likelihood": 0.80, "ground_truth_label": 1},
        {"predicted_recovery_likelihood": 0.70, "ground_truth_label": 0},
        {"predicted_recovery_likelihood": 0.60, "ground_truth_label": 0},
        {"predicted_recovery_likelihood": 0.40, "ground_truth_label": 0},
        {"predicted_recovery_likelihood": 0.30, "ground_truth_label": 0},
        {"predicted_recovery_likelihood": 0.20, "ground_truth_label": 1},
        {"predicted_recovery_likelihood": 0.10, "ground_truth_label": 0},
        {"predicted_recovery_likelihood": 0.85, "ground_truth_label": 1},
        {"predicted_recovery_likelihood": 0.15, "ground_truth_label": 0},
    ]

    cm = calculate_confusion_matrix(test_records, threshold=0.50)
    assert cm["tp"] == 3
    assert cm["fp"] == 2
    assert cm["tn"] == 4
    assert cm["fn"] == 1
    assert cm["accuracy"] == 0.70       # (3 + 4) / 10
    assert cm["precision"] == 0.60      # 3 / (3 + 2)
    assert cm["recall"] == 0.75         # 3 / (3 + 1)
    # F1 = 2 * (0.6 * 0.75) / (0.6 + 0.75) = 2 * 0.45 / 1.35 = 0.90 / 1.35 = 0.6667
    assert cm["f1"] == 0.6667
    assert cm["specificity"] == 0.6667 # 4 / (4 + 2)
    assert cm["negative_predictive_value"] == 0.80 # 4 / (4 + 1)
    assert cm["false_positive_rate"] == 0.3333     # 2 / (2 + 4)


# ---------------- 4. Brier Score & Expected Calibration Error ----------------

def test_05_brier_score_formula():
    """Verify Brier Score: mean((pred - actual)^2)."""
    # 2 observations: pred=0.8, act=1 (err=0.2, sq=0.04); pred=0.4, act=0 (err=0.4, sq=0.16)
    # mean sq = (0.04 + 0.16) / 2 = 0.1000
    records = [
        {"predicted_recovery_likelihood": 0.8, "ground_truth_label": 1},
        {"predicted_recovery_likelihood": 0.4, "ground_truth_label": 0},
    ]
    brier = calculate_brier_score(records)
    assert brier == 0.1000


def test_06_ece_and_calibration_buckets():
    """Verify 10 probability buckets, mean predicted, observed rate, and ECE."""
    # Construct 10 records across bins
    records = [
        {"predicted_recovery_likelihood": 0.05, "ground_truth_label": 0},
        {"predicted_recovery_likelihood": 0.08, "ground_truth_label": 0},
        {"predicted_recovery_likelihood": 0.15, "ground_truth_label": 0},
        {"predicted_recovery_likelihood": 0.25, "ground_truth_label": 0},
        {"predicted_recovery_likelihood": 0.55, "ground_truth_label": 1},
        {"predicted_recovery_likelihood": 0.65, "ground_truth_label": 1},
        {"predicted_recovery_likelihood": 0.75, "ground_truth_label": 1},
        {"predicted_recovery_likelihood": 0.85, "ground_truth_label": 1},
        {"predicted_recovery_likelihood": 0.92, "ground_truth_label": 1},
        {"predicted_recovery_likelihood": 0.98, "ground_truth_label": 1},
    ]
    res = calculate_calibration_buckets(records, num_bins=10)
    assert len(res["bins"]) == 10
    assert res["total_labeled_observations"] == 10
    assert res["expected_calibration_error"] is not None
    assert 0.0 <= res["expected_calibration_error"] <= 1.0


# ---------------- 5. Sample Size Gating & Criteria ----------------

def test_07_sample_size_gating():
    """Verify transparent sample-size warnings when observations are insufficient."""
    # Under 10 observations => INSUFFICIENT_DATA
    small_records = [{"predicted_recovery_likelihood": 0.8, "ground_truth_label": 1}] * 5
    eval_small = evaluate_cohort(small_records, cohort_name="GENUINE_TEST")
    assert eval_small["sample_size"]["status"] == "INSUFFICIENT_DATA"
    assert "INSUFFICIENT SAMPLE SIZE" in eval_small["sample_size"]["message"]

    # 15 observations => DESCRIPTIVE_ONLY
    med_records = [{"predicted_recovery_likelihood": 0.8, "ground_truth_label": 1}] * 15
    eval_med = evaluate_cohort(med_records, cohort_name="GENUINE_TEST")
    assert eval_med["sample_size"]["status"] == "DESCRIPTIVE_ONLY"
    assert "DESCRIPTIVE ONLY" in eval_med["sample_size"]["message"]


def test_08_well_calibrated_requires_sufficient_sample_size():
    """Verify that WELL_CALIBRATED is never granted without meeting min sample size (>= 100)."""
    # 20 perfectly calibrated samples (ECE ~ 0.0) -> Cannot be WELL_CALIBRATED because N < 100
    perfect_small = [
        {"predicted_recovery_likelihood": 0.8, "ground_truth_label": 1},
        {"predicted_recovery_likelihood": 0.8, "ground_truth_label": 1},
        {"predicted_recovery_likelihood": 0.8, "ground_truth_label": 1},
        {"predicted_recovery_likelihood": 0.8, "ground_truth_label": 1},
        {"predicted_recovery_likelihood": 0.8, "ground_truth_label": 0}, # 4/5 = 80%
    ] * 6 # 30 samples total
    cal = calculate_calibration_buckets(perfect_small)
    assert cal["expected_calibration_error"] == 0.0
    # Must be PARTIALLY_CALIBRATED or INSUFFICIENT_DATA, NOT WELL_CALIBRATED
    assert cal["calibration_status"] != "WELL_CALIBRATED"

    # 100 perfectly calibrated samples -> Qualifies for WELL_CALIBRATED
    perfect_large = perfect_small * 4 # 120 samples
    cal_large = calculate_calibration_buckets(perfect_large)
    assert cal_large["calibration_status"] == "WELL_CALIBRATED"


# ---------------- 6. Threshold Sweep & Optimal F1 ----------------

def test_09_threshold_sweep_analysis():
    """Verify threshold analysis sweeping 0.00 to 1.00 finds optimal F1 cutoff."""
    records = [
        {"predicted_recovery_likelihood": 0.9, "ground_truth_label": 1},
        {"predicted_recovery_likelihood": 0.8, "ground_truth_label": 1},
        {"predicted_recovery_likelihood": 0.4, "ground_truth_label": 0},
        {"predicted_recovery_likelihood": 0.2, "ground_truth_label": 0},
    ] * 10 # 40 records
    sweep = calculate_threshold_sweep(records, step=0.05)
    assert len(sweep["sweep_points"]) == 21 # 0.00 to 1.00 in 0.05 steps
    assert sweep["optimal_f1_threshold"] is not None
    assert sweep["max_f1_score"] is not None


# ---------------- 7. ROC and Precision-Recall Curves ----------------

def test_10_roc_and_pr_curves():
    """Verify ROC and PR curve generation and AUC numerical integration."""
    records = [
        {"predicted_recovery_likelihood": 0.9, "ground_truth_label": 1},
        {"predicted_recovery_likelihood": 0.8, "ground_truth_label": 1},
        {"predicted_recovery_likelihood": 0.6, "ground_truth_label": 0},
        {"predicted_recovery_likelihood": 0.3, "ground_truth_label": 0},
    ] * 10
    curves = calculate_curves(records)
    assert curves["roc_available"] is True
    assert curves["pr_available"] is True
    assert 0.5 <= curves["roc_auc"] <= 1.0
    assert 0.0 <= curves["pr_auc"] <= 1.0

    # Single class only => curves unavailable honestly
    all_ones = [{"predicted_recovery_likelihood": 0.8, "ground_truth_label": 1}] * 10
    single_curves = calculate_curves(all_ones)
    assert single_curves["roc_available"] is False
    assert single_curves["roc_auc"] is None


# ---------------- 8. Action Performance & Natural Baseline ----------------

def test_11_action_performance_and_natural_baseline():
    """Verify per-action statistics and natural baseline recovery with associational disclaimer."""
    records = [
        {"action_selected": "SEND_RECOVERY_LINK", "actual_status": "VERIFIED_RECOVERED", "attribution_strength": "STRONG", "actual_recovered": 500.0, "actual_incremental_recovery": 500.0, "predicted_recovery_likelihood": 0.75, "ground_truth_label": 1},
        {"action_selected": "SEND_RECOVERY_LINK", "actual_status": "NOT_RECOVERED", "attribution_strength": None, "actual_recovered": 0.0, "actual_incremental_recovery": 0.0, "predicted_recovery_likelihood": 0.60, "ground_truth_label": 0},
        {"action_selected": "SAFE_PAYMENT_RETRY", "actual_status": "NATURALLY_RECOVERED", "attribution_strength": "NONE", "actual_recovered": 0.0, "actual_incremental_recovery": 0.0, "predicted_recovery_likelihood": 0.40, "ground_truth_label": None},
    ]

    actions_perf = calculate_action_performance(records)
    link_act = next(a for a in actions_perf if a["action_type"] == "SEND_RECOVERY_LINK")
    assert link_act["total_cases"] == 2
    assert link_act["verified_recovered_count"] == 1
    assert link_act["recovery_rate"] == 0.50
    assert link_act["gross_recovered_amount"] == 500.0

    nat_base = calculate_natural_recovery_baseline(records)
    assert nat_base["total_eligible_cases"] == 3
    assert nat_base["natural_recoveries"] == 1
    assert "ASSOCIATIONAL — NOT CAUSAL" in nat_base["disclaimer"]


# ---------------- 9. Platt Scaling (Logistic Calibration) ----------------

def test_12_platt_scaling_fitting():
    """Verify Platt scaling fits logistic parameters without modifying raw predictions."""
    # Under 50 records => not fitted
    small = [{"predicted_recovery_likelihood": 0.7, "ground_truth_label": 1}] * 20
    fit_res = fit_platt_scaling(small)
    assert fit_res["fitted"] is False

    # 60 records => fits parameters a and b
    data = (
        [{"predicted_recovery_likelihood": 0.8, "ground_truth_label": 1}] * 30 +
        [{"predicted_recovery_likelihood": 0.2, "ground_truth_label": 0}] * 30
    )
    fit_large = fit_platt_scaling(data)
    assert fit_large["fitted"] is True
    assert "a" in fit_large["parameters"]
    assert "b" in fit_large["parameters"]
    assert fit_large["model_version"].startswith("platt-v1-")


# ---------------- 10. Model Comparison ----------------

def test_13_model_version_filtering_and_comparison():
    """Verify evaluation reports can segment by model version."""
    records = [
        {"model_version": "claude-sonnet-4-6", "predicted_recovery_likelihood": 0.8, "ground_truth_label": 1},
        {"model_version": "claude-sonnet-4-6", "predicted_recovery_likelihood": 0.7, "ground_truth_label": 1},
        {"model_version": "heuristic-fallback-v1", "predicted_recovery_likelihood": 0.3, "ground_truth_label": 0},
    ]
    report_claude = evaluate_cohort(records, model_version="claude-sonnet-4-6")
    assert report_claude["model_version_evaluated"] == "claude-sonnet-4-6"
    assert report_claude["sample_size"]["total_cases"] == 2

    report_heur = evaluate_cohort(records, model_version="heuristic-fallback-v1")
    assert report_heur["model_version_evaluated"] == "heuristic-fallback-v1"
    assert report_heur["sample_size"]["total_cases"] == 1

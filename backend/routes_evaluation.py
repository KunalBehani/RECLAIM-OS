"""FastAPI router for Phase 3 ML Evaluation Lab.

Provides API endpoints for evaluating model recovery likelihoods, generating frozen
dataset evaluation runs, inspecting calibration & reliability diagrams, comparing
models, and analyzing threshold trade-offs without modifying production policy.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from auth import get_current_user
from constants import now_iso
from database import db
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

router = APIRouter(prefix="/evaluation", tags=["evaluation"])


class CreateEvaluationRunRequest(BaseModel):
    cohort: str = Field(default="GENUINE_TEST", description="Cohort: GENUINE_TEST, GENUINE_LIVE, IMPORTED, SIMULATED, LAB, ALL_REAL, ALL")
    threshold: float = Field(default=0.50, ge=0.0, le=1.0)
    model_version: Optional[str] = Field(default=None)
    title: Optional[str] = Field(default=None)
    notes: Optional[str] = Field(default=None)


class CompareModelsRequest(BaseModel):
    cohort: str = Field(default="GENUINE_TEST")
    model_a: str = Field(default="claude-sonnet-4-6")
    model_b: str = Field(default="heuristic-fallback-v1")
    threshold: float = Field(default=0.50)


async def _fetch_cohort_cases(cohort: str) -> List[Dict[str, Any]]:
    """Fetches raw case documents filtered strictly by cohort."""
    query = {}
    if cohort == "GENUINE_TEST":
        query = {"$or": [
            {"data_stage": "TEST", "is_lab": False, "simulated": False},
            {"source": "RAZORPAY_TEST", "is_lab": {"$ne": True}, "simulated": {"$ne": True}},
        ]}
    elif cohort == "GENUINE_LIVE":
        query = {"$or": [
            {"data_stage": "LIVE", "is_lab": False, "simulated": False},
            {"source": "RAZORPAY_LIVE", "is_lab": {"$ne": True}, "simulated": {"$ne": True}},
        ]}
    elif cohort == "LAB":
        query = {"$or": [
            {"data_stage": "LAB"},
            {"is_lab": True},
            {"order_key": {"$regex": "^order_LAB"}},
            {"source": "TEST_LAB"},
        ]}
    elif cohort == "SIMULATED":
        query = {"$or": [
            {"data_stage": "SIMULATED"},
            {"simulated": True},
            {"source": {"$in": ["SIMULATOR", "TEST"]}},
        ]}
    elif cohort == "IMPORTED":
        query = {"$or": [
            {"data_stage": "IMPORTED"},
            {"source": {"$in": ["CSV_UPLOAD", "XLSX_UPLOAD", "FILE_IMPORT"]}},
        ]}
    elif cohort == "ALL_REAL":
        query = {"$and": [
            {"is_lab": {"$ne": True}},
            {"simulated": {"$ne": True}},
            {"data_stage": {"$in": ["TEST", "LIVE"]}},
        ]}
    elif cohort == "ALL":
        query = {}
    else:
        query = {"data_stage": cohort}

    cases = await db.recovery_cases.find(query, {"_id": 0}).sort("created_at", -1).to_list(10000)
    return cases


@router.get("/cohorts")
async def get_evaluation_cohorts(request: Request):
    """Returns available evaluation cohorts and case counts."""
    await get_current_user(request)
    
    total = await db.recovery_cases.count_documents({})
    genuine_test = await db.recovery_cases.count_documents({
        "$or": [
            {"data_stage": "TEST", "is_lab": False, "simulated": False},
            {"source": "RAZORPAY_TEST", "is_lab": {"$ne": True}, "simulated": {"$ne": True}},
        ]
    })
    genuine_live = await db.recovery_cases.count_documents({
        "$or": [
            {"data_stage": "LIVE", "is_lab": False, "simulated": False},
            {"source": "RAZORPAY_LIVE", "is_lab": {"$ne": True}, "simulated": {"$ne": True}},
        ]
    })
    lab_cases = await db.recovery_cases.count_documents({
        "$or": [
            {"data_stage": "LAB"},
            {"is_lab": True},
            {"order_key": {"$regex": "^order_LAB"}},
            {"source": "TEST_LAB"},
        ]
    })
    simulated_cases = await db.recovery_cases.count_documents({
        "$or": [
            {"data_stage": "SIMULATED"},
            {"simulated": True},
            {"source": {"$in": ["SIMULATOR", "TEST"]}},
        ]
    })
    imported_cases = await db.recovery_cases.count_documents({
        "$or": [
            {"data_stage": "IMPORTED"},
            {"source": {"$in": ["CSV_UPLOAD", "XLSX_UPLOAD", "FILE_IMPORT"]}},
        ]
    })

    # Find distinct model versions
    model_versions = await db.recovery_cases.distinct("model_version")
    models = [m for m in model_versions if m] or ["claude-sonnet-4-6", "heuristic-fallback-v1"]

    return {
        "cohorts": [
            {"id": "GENUINE_TEST", "label": "Genuine Razorpay TEST", "count": genuine_test, "description": "Actual test payment failures and settlements with real webhooks."},
            {"id": "GENUINE_LIVE", "label": "Genuine Razorpay LIVE", "count": genuine_live, "description": "Real-money production payments (currently read-only)."},
            {"id": "IMPORTED", "label": "Imported Historical CSV/XLSX", "count": imported_cases, "description": "Merchant batch files parsed through the recovery engine."},
            {"id": "SIMULATED", "label": "Simulated Payment Scenarios", "count": simulated_cases, "description": "Synthetic UI and test runner events."},
            {"id": "LAB", "label": "Developer Test Lab", "count": lab_cases, "description": "Synthetic webhook payloads generated by developer test lab (LAB DATA — NOT REAL-WORLD PERFORMANCE)."},
        ],
        "total_cases_in_db": total,
        "available_model_versions": models,
        "default_cohort": "GENUINE_TEST",
    }


@router.get("/summary")
async def get_evaluation_summary(
    request: Request,
    cohort: str = Query(default="GENUINE_TEST"),
    threshold: float = Query(default=0.50, ge=0.0, le=1.0),
    model_version: Optional[str] = Query(default=None),
):
    """Calculates on-the-fly evaluation report for the selected cohort and threshold."""
    await get_current_user(request)
    cases = await _fetch_cohort_cases(cohort)
    actions = await db.recovery_actions.find({}, {"_id": 0}).to_list(10000)
    
    actions_by_case = defaultdict(list)
    for a in actions:
        actions_by_case[a.get("case_id")].append(a)

    records = [extract_evaluation_record(c, actions_by_case.get(c.get("case_id"))) for c in cases]
    report = evaluate_cohort(records, cohort_name=cohort, threshold=threshold, model_version=model_version)
    return report


@router.post("/runs")
async def create_evaluation_run(body: CreateEvaluationRunRequest, request: Request):
    """Creates an immutable frozen Evaluation Run snapshot."""
    user = await get_current_user(request)
    cases = await _fetch_cohort_cases(body.cohort)
    actions = await db.recovery_actions.find({}, {"_id": 0}).to_list(10000)
    
    actions_by_case = defaultdict(list)
    for a in actions:
        actions_by_case[a.get("case_id")].append(a)

    records = [extract_evaluation_record(c, actions_by_case.get(c.get("case_id"))) for c in cases]
    report = evaluate_cohort(records, cohort_name=body.cohort, threshold=body.threshold, model_version=body.model_version)

    run_doc = {
        "run_id": f"evalrun_{uuid.uuid4().hex[:12]}",
        "title": body.title or f"Evaluation Run: {body.cohort} ({datetime.now(timezone.utc).strftime('%d %b %Y %H:%M')})",
        "notes": body.notes,
        "created_by": user.get("email", "system"),
        "created_at": now_iso(),
        "status": "COMPLETED",
        "cohort": body.cohort,
        "threshold": body.threshold,
        "model_version": body.model_version,
        "report": report,
        "snapshot_case_count": len(records),
        "snapshot_records_sample": records[:100],  # sample preview
    }

    await db.evaluation_runs.insert_one(run_doc)
    run_doc.pop("_id", None)
    return run_doc


@router.get("/runs")
async def list_evaluation_runs(request: Request):
    """Lists past evaluation runs."""
    await get_current_user(request)
    runs = await db.evaluation_runs.find({}, {"snapshot_records_sample": 0, "_id": 0}).sort("created_at", -1).to_list(100)
    return {"runs": runs}


@router.get("/runs/{run_id}")
async def get_evaluation_run(run_id: str, request: Request):
    """Retrieves full evaluation report for a specific frozen run."""
    await get_current_user(request)
    run = await db.evaluation_runs.find_one({"run_id": run_id}, {"_id": 0})
    if not run:
        raise HTTPException(status_code=404, detail="Evaluation run not found.")
    return run


@router.delete("/runs/{run_id}")
async def delete_evaluation_run(run_id: str, request: Request):
    """Deletes an evaluation run."""
    await get_current_user(request)
    res = await db.evaluation_runs.delete_one({"run_id": run_id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Evaluation run not found.")
    return {"deleted": True, "run_id": run_id}


@router.post("/compare")
async def compare_models(body: CompareModelsRequest, request: Request):
    """Compares performance of two models side-by-side on the same cohort."""
    await get_current_user(request)
    cases = await _fetch_cohort_cases(body.cohort)
    actions = await db.recovery_actions.find({}, {"_id": 0}).to_list(10000)
    
    actions_by_case = defaultdict(list)
    for a in actions:
        actions_by_case[a.get("case_id")].append(a)

    records = [extract_evaluation_record(c, actions_by_case.get(c.get("case_id"))) for c in cases]

    eval_a = evaluate_cohort(records, cohort_name=body.cohort, threshold=body.threshold, model_version=body.model_a)
    eval_b = evaluate_cohort(records, cohort_name=body.cohort, threshold=body.threshold, model_version=body.model_b)

    return {
        "cohort": body.cohort,
        "threshold": body.threshold,
        "model_a": {
            "name": body.model_a,
            "sample_size": eval_a["sample_size"],
            "metrics": eval_a["classification_metrics"],
            "brier_score": eval_a["calibration"]["brier_score"],
            "expected_calibration_error": eval_a["calibration"]["expected_calibration_error"],
            "calibration_status": eval_a["calibration"]["calibration_status"],
            "roc_auc": eval_a["curves"]["roc_auc"],
            "pr_auc": eval_a["curves"]["pr_auc"],
        },
        "model_b": {
            "name": body.model_b,
            "sample_size": eval_b["sample_size"],
            "metrics": eval_b["classification_metrics"],
            "brier_score": eval_b["calibration"]["brier_score"],
            "expected_calibration_error": eval_b["calibration"]["expected_calibration_error"],
            "calibration_status": eval_b["calibration"]["calibration_status"],
            "roc_auc": eval_b["curves"]["roc_auc"],
            "pr_auc": eval_b["curves"]["pr_auc"],
        },
        "cohort_compatible": True,
    }


@router.get("/calibration-status")
async def get_calibration_status(request: Request):
    """Returns truthful production model calibration state."""
    await get_current_user(request)
    cases = await _fetch_cohort_cases("GENUINE_TEST")
    records = [extract_evaluation_record(c) for c in cases]
    eval_res = evaluate_cohort(records, cohort_name="GENUINE_TEST")

    return {
        "production_model": "claude-sonnet-4-6",
        "fallback_model": "heuristic-fallback-v1",
        "calibrated_eiv_enabled": False,
        "calibration_status": eval_res["calibration"]["calibration_status"],
        "calibration_status_reason": eval_res["calibration"]["calibration_status_reason"],
        "sample_size": eval_res["sample_size"],
        "note": "Production EIV uses uncalibrated model estimates and is labeled as such. Calibrated probabilities remain disabled until calibration is statistically validated on >= 100 genuine outcomes.",
    }

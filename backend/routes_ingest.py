import time
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, UploadFile, File
from fastapi.responses import JSONResponse

from audit import write_audit
from auth import get_current_user
from constants import now_iso
from database import db
from detection import process_payment_attempt
from ingestion import MAX_FILE_SIZE, ai_enhanced_mapping, parse_file, suggest_mapping, validate_and_normalize

router = APIRouter(prefix="/ingest", tags=["ingest"])

# Aggregate LLM budget per batch import: cases analyzed beyond the budget use
# the deterministic heuristic fallback (model_version records which ran).
BATCH_LLM_BUDGET_SECONDS = 25.0


@router.post("/upload")
async def upload_file(request: Request, file: UploadFile = File(...)):
    user = await get_current_user(request)
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="File exceeds the 5 MB limit.")
    try:
        sheets = parse_file(content, file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    first = sheets[0]
    mapping = suggest_mapping(first["headers"])
    mapping = await ai_enhanced_mapping(first["headers"], first["rows"][:3], mapping)

    batch_id = f"batch_{uuid.uuid4().hex[:12]}"
    await db.ingestion_batches.insert_one({
        "batch_id": batch_id,
        "filename": file.filename,
        "sheets": sheets,
        "status": "MAPPING_REVIEW",
        "uploaded_by": user["email"],
        "created_at": now_iso(),
    })
    return {
        "batch_id": batch_id,
        "filename": file.filename,
        "sheets": [
            {
                "name": s["name"],
                "headers": s["headers"],
                "row_count": s["row_count"],
                "sample_rows": s["rows"][:5],
                "truncated": s["truncated"],
            }
            for s in sheets
        ],
        "suggested_mapping": mapping,
        "default_sheet": first["name"],
    }


@router.post("/{batch_id}/confirm")
async def confirm_import(batch_id: str, request: Request, background_tasks: BackgroundTasks):
    """Returns 202 immediately; the import runs in the background so the
    request can never outlive the ingress timeout. Poll GET /api/ingest/{batch_id}."""
    user = await get_current_user(request)
    body = await request.json()
    mapping = body.get("mapping") or {}
    sheet_name = body.get("sheet")

    batch = await db.ingestion_batches.find_one({"batch_id": batch_id}, {"_id": 0, "sheets": 0})
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    if batch["status"] in ("IMPORTED", "IMPORTING"):
        return JSONResponse(
            status_code=409,
            content={"detail": "Batch already imported or currently importing.", "status": batch["status"], "batch_id": batch_id},
        )

    await db.ingestion_batches.update_one(
        {"batch_id": batch_id},
        {"$set": {"status": "IMPORTING", "mapping": mapping, "sheet_name": sheet_name}},
    )
    background_tasks.add_task(run_import, batch_id, sheet_name, mapping, user["email"])
    return JSONResponse(status_code=202, content={
        "status": "IMPORTING",
        "batch_id": batch_id,
        "detail": "Import is running in the background. Poll GET /api/ingest/{batch_id} for the validation report.",
    })


async def run_import(batch_id: str, sheet_name, mapping: dict, actor: str):
    batch = await db.ingestion_batches.find_one({"batch_id": batch_id}, {"_id": 0})
    if not batch:
        return
    try:
        sheet = next((s for s in batch["sheets"] if s["name"] == sheet_name), batch["sheets"][0])
        source = "CSV_UPLOAD" if batch["filename"].lower().endswith(".csv") else "XLSX_UPLOAD"
        result = validate_and_normalize(sheet["rows"], mapping, source=source, batch_id=batch_id)

        if result["exceptions"]:
            await db.exceptions.insert_many(result["exceptions"])

        import_results = {
            "cases_created": 0,
            "cases_updated": 0,
            "naturally_recovered": 0,
            "payments_recorded": 0,
            "verified_recovered": 0,
            "closed_natural": 0,
            "invalid_cases": 0,
            "duplicates_blocked": 0,
            "exceptions": len(result["exceptions"]),
            "llm_analyses": 0,
            "heuristic_fallbacks": 0,
        }
        deadline = time.monotonic() + BATCH_LLM_BUDGET_SECONDS
        for record in result["records"]:
            allow_llm = time.monotonic() < deadline
            outcome = await process_payment_attempt(record, actor=actor, allow_llm=allow_llm)
            if outcome["result"] == "case_created":
                import_results["cases_created"] += 1
                if allow_llm:
                    import_results["llm_analyses"] += 1
                else:
                    import_results["heuristic_fallbacks"] += 1
            elif outcome["result"] == "case_updated":
                import_results["cases_updated"] += 1
            elif outcome["result"] == "naturally_recovered":
                import_results["naturally_recovered"] += 1
            elif outcome["result"] == "duplicate_attempt":
                import_results["duplicates_blocked"] += 1
            elif outcome["result"] == "verified_recovered":
                import_results["verified_recovered"] += 1
            elif outcome["result"] == "closed_natural":
                import_results["closed_natural"] += 1
            elif outcome["result"] == "invalid_case":
                import_results["invalid_cases"] += 1
            else:
                import_results["payments_recorded"] += 1

        await db.ingestion_batches.update_one(
            {"batch_id": batch_id},
            {"$set": {"status": "IMPORTED", "report": result["report"], "import_results": import_results, "imported_at": now_iso()}},
        )
        await write_audit(
            actor=actor,
            event_type="BATCH_IMPORTED",
            reason=f"{batch['filename']}: {result['report']['valid_rows']} valid / {result['report']['total_rows']} total rows imported; {import_results['cases_created']} recovery cases created.",
            after_state={"batch_id": batch_id, **import_results},
        )
    except Exception as exc:
        await db.ingestion_batches.update_one(
            {"batch_id": batch_id},
            {"$set": {"status": "IMPORT_FAILED", "error": str(exc)[:500]}},
        )


@router.get("/batches")
async def list_batches(request: Request):
    await get_current_user(request)
    batches = await db.ingestion_batches.find(
        {}, {"_id": 0, "sheets": 0}
    ).sort("created_at", -1).to_list(50)
    return {"batches": batches}


@router.get("/{batch_id}")
async def get_batch(batch_id: str, request: Request):
    await get_current_user(request)
    batch = await db.ingestion_batches.find_one({"batch_id": batch_id}, {"_id": 0, "sheets": 0})
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch

"""Platform cron endpoints (Phase 2B scheduled operations).

Contract (per .emergent/crons.yml): bearer token compared in constant time
against WEBHOOK_CRON_SECRET; idempotent on run_id; the real work is handed to
a background task and the endpoint acks immediately.
"""
import hmac
import os
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from constants import now_iso
from database import db

router = APIRouter(prefix="/cron", tags=["cron"])


async def _require_cron_auth(request: Request) -> None:
    secret = os.environ.get("WEBHOOK_CRON_SECRET", "")
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not secret or not token or not hmac.compare_digest(token, secret):
        raise HTTPException(status_code=401, detail="Invalid cron credentials.")


@router.post("/verification-sweep")
async def cron_verification_sweep(request: Request, background_tasks: BackgroundTasks):
    # Cron endpoints must ack 2xx immediately; enqueue/background the actual work.
    await _require_cron_auth(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body.")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid request body.")
    run_id = body.get("run_id") or request.headers.get("X-Webhook-Id") or f"cron_{uuid.uuid4().hex[:10]}"

    existing = await db.cron_runs.find_one({"run_id": run_id, "job": "verification-sweep"})
    if existing:
        return {"accepted": True, "duplicate": True, "run_id": run_id}
    await db.cron_runs.insert_one({"run_id": run_id, "job": "verification-sweep", "status": "QUEUED", "accepted_at": now_iso()})

    async def _run():
        try:
            from sweep_core import run_verification_sweep
            results = await run_verification_sweep(actor=f"cron:verification-sweep:{run_id}")
            await db.cron_runs.update_one({"run_id": run_id}, {"$set": {"status": "COMPLETED", "completed_at": now_iso(), "results": results}})
        except Exception as exc:
            await db.cron_runs.update_one({"run_id": run_id}, {"$set": {"status": "FAILED", "completed_at": now_iso(), "error": str(exc)[:300]}})

    # Starlette BackgroundTasks: awaited by the server after the response is
    # sent — the task cannot be GC-collected or request-cancelled mid-run.
    background_tasks.add_task(_run)
    return {"accepted": True, "duplicate": False, "run_id": run_id}

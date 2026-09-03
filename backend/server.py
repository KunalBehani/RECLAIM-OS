import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, APIRouter
from starlette.middleware.cors import CORSMiddleware

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

from auth import router as auth_router  # noqa: E402
from constants import DEFAULT_SETTINGS  # noqa: E402
from database import client, db  # noqa: E402
from routes_cases import router as cases_router  # noqa: E402
from routes_dashboard import router as dashboard_router  # noqa: E402
from routes_ingest import router as ingest_router  # noqa: E402
from routes_integrations import router as integrations_router  # noqa: E402
from routes_recovery import router as recovery_router  # noqa: E402
from routes_cron import router as cron_router  # noqa: E402
from routes_settings import router as settings_router  # noqa: E402
from routes_simulate import router as simulate_router  # noqa: E402
from routes_webhooks import router as webhooks_router  # noqa: E402

app = FastAPI(title="RECLAIM OS API", version="1.0.0")
api_router = APIRouter(prefix="/api")


@api_router.get("/")
async def root():
    return {"service": "RECLAIM OS API", "status": "ok", "version": "1.0.0"}


for router in (auth_router, ingest_router, cases_router, dashboard_router, webhooks_router, simulate_router, settings_router, integrations_router, recovery_router, cron_router):
    api_router.include_router(router)

app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@app.on_event("startup")
async def startup():
    await db.webhook_events.create_index("event_id", unique=True)
    await db.provider_events.create_index([("provider", 1), ("provider_event_id", 1)], unique=True)
    await db.orders.create_index("order_id", unique=True)
    await db.payment_attempts.create_index("payment_id", unique=True)
    await db.payment_attempts.create_index("order_id")
    await db.cron_runs.create_index([("run_id", 1), ("job", 1)], unique=True)
    await db.payment_attempts.create_index("invoice_id")
    await db.recovery_cases.create_index("order_key")
    await db.recovery_cases.create_index("status")
    await db.recovery_actions.create_index("idempotency_key", unique=True)
    await db.recovery_actions.create_index("case_id")
    await db.audit_events.create_index("case_id")
    await db.user_sessions.create_index("session_token", unique=True)
    existing = await db.settings.find_one({"key": "policy"})
    if not existing:
        await db.settings.insert_one(dict(DEFAULT_SETTINGS))
    logger.info("RECLAIM OS API started")


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()

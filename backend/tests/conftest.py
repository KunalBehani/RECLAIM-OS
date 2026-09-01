"""Shared test fixtures.

The webhook/integration test modules all depend on the single `integrations`
document containing the shared test webhook secret. Under pytest-xdist these
modules run on parallel workers against the same database, so they must be
serialized — and the real stored credentials must survive the suite.
"""
import os
from datetime import datetime, timezone

import pytest
from dotenv import dotenv_values
from filelock import FileLock
from pymongo import MongoClient

_be = dotenv_values("/app/backend/.env")

DUMMY_CONFIG = {
    "provider": "razorpay",
    "mode": "TEST",
    "key_id": "rzp_test_DUMMY1a2b3c",
    "key_secret": "dummy_secret_not_real",
    "webhook_secret": "whsec_test_lab_secret_123",
    "status": "NOT_CONNECTED",
}


@pytest.fixture(scope="module")
def razorpay_integration_guard():
    """Serializes modules that use the shared Razorpay integration doc:
    installs the dummy TEST config for the module's duration, then restores
    whatever config existed before (real credentials are never destroyed)."""
    mdb = MongoClient(os.environ.get("MONGO_URL") or _be.get("MONGO_URL"))[
        os.environ.get("DB_NAME") or _be.get("DB_NAME")
    ]
    with FileLock("/tmp/reclaim_razorpay_integration_test.lock"):
        saved = mdb.integrations.find_one({"provider": "razorpay"})
        # Snapshot the LIVE-mode doc and policy settings too — live tests mutate
        # both, and real production state must survive the suite.
        saved_live = mdb.integrations.find_one({"provider": "razorpay", "mode": "LIVE"})
        saved_settings = mdb.settings.find_one({"key": "policy"})
        # The notification channel must be DISABLED during guarded modules —
        # otherwise ambient enabled state would make executions REAL and leak
        # genuine sends into unrelated tests. Restored afterwards.
        saved_resend = mdb.integrations.find_one({"provider": "resend"})
        mdb.integrations.update_one({"provider": "resend"}, {"$set": {"provider": "resend", "enabled": False}}, upsert=True)
        now = datetime.now(timezone.utc).isoformat()
        mdb.integrations.update_one(
            {"provider": "razorpay"},
            {"$set": {**DUMMY_CONFIG, "updated_at": now}, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        yield
        if saved_resend is None:
            mdb.integrations.delete_many({"provider": "resend"})
        else:
            saved_resend.pop("_id", None)
            mdb.integrations.replace_one({"provider": "resend"}, saved_resend, upsert=True)
        if saved_live is None:
            mdb.integrations.delete_many({"provider": "razorpay", "mode": "LIVE"})
        else:
            saved_live.pop("_id", None)
            mdb.integrations.replace_one({"provider": "razorpay", "mode": "LIVE"}, saved_live, upsert=True)
        if saved_settings is not None:
            saved_settings.pop("_id", None)
            mdb.settings.replace_one({"key": "policy"}, saved_settings, upsert=True)
        if saved is None:
            mdb.integrations.delete_one({"provider": "razorpay"})
        else:
            saved.pop("_id", None)
            mdb.integrations.replace_one({"provider": "razorpay"}, saved, upsert=True)

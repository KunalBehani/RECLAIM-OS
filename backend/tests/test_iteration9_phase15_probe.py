"""Iteration 9 — Phase 1.5 (Resend notification channel + public tokenized retry) probes.

Read-only against shared state EXCEPT one ephemeral synthetic case/action used to
verify idempotent completion (created and deleted inside the test).
Never calls PUT /api/integrations/resend/config, /cases/{id}/execute or test-lab
endpoints (those would send real email / mutate the live demo).
"""
import hashlib
import hmac
import os
import re
import uuid

import pytest
import requests
from dotenv import dotenv_values
from pymongo import MongoClient

_fe = dotenv_values("/app/frontend/.env")
_be = dotenv_values("/app/backend/.env")
BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or _fe.get("REACT_APP_BACKEND_URL")).rstrip("/")

# Serialize with the other webhook modules — this file forges signatures with
# the CURRENT stored key_secret, which guarded modules temporarily swap.
pytestmark = pytest.mark.usefixtures("razorpay_integration_guard")
OWNER_SESSION = "test_session_smoke_1787904424204"


@pytest.fixture(scope="module")
def mdb():
    return MongoClient(os.environ.get("MONGO_URL") or _be.get("MONGO_URL"))[
        os.environ.get("DB_NAME") or _be.get("DB_NAME")
    ]


@pytest.fixture(scope="module")
def owner_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json", "Authorization": f"Bearer {OWNER_SESSION}"})
    return s


@pytest.fixture(scope="module")
def anon_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def real_token(mdb):
    doc = mdb.recovery_actions.find_one({"recovery_token": {"$exists": True}})
    if not doc:
        pytest.skip("No recovery_actions with recovery_token present")
    return doc


# ---------------- Resend endpoints (owner session) ----------------
class TestResendEndpoints:
    def test_resend_status(self, owner_client):
        r = owner_client.get(f"{BASE_URL}/api/integrations/resend")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] in ("NOT_CONFIGURED", "CONNECTED", "ERROR"), d
        assert d["provider"] == "resend"
        assert d["channel"] == "email"
        assert isinstance(d["enabled"], bool)
        assert "ek_" not in r.text

    def test_resend_diagnostics_masked(self, owner_client):
        r = owner_client.get(f"{BASE_URL}/api/integrations/resend/diagnostics")
        assert r.status_code == 200, r.text
        body = r.text
        real_key = (os.environ.get("EMERGENT_EMAIL_KEY") or _be.get("EMERGENT_EMAIL_KEY") or "").strip()
        assert real_key, "EMERGENT_EMAIL_KEY missing from backend env"
        # No raw key, no key-like token, no key_secret material
        assert real_key not in body
        assert not re.search(r"ek_[A-Za-z0-9_\-]{10,}", body), body
        rzp = mdb_secret_cache.get("key_secret")
        if rzp:
            assert rzp not in body
        d = r.json()
        assert d.get("api_key_present") is True
        assert d.get("api_key_length") == len(real_key)
        assert (d.get("api_key_prefix") or "") == real_key[:3]

    def test_resend_status_unauthenticated(self, anon_client):
        r = anon_client.get(f"{BASE_URL}/api/integrations/resend")
        assert r.status_code in (401, 403), r.status_code

    def test_resend_diagnostics_unauthenticated(self, anon_client):
        r = anon_client.get(f"{BASE_URL}/api/integrations/resend/diagnostics")
        assert r.status_code in (401, 403), r.status_code

    def test_resend_config_unauthenticated(self, anon_client):
        # Anonymous PUT must be rejected BEFORE any state change (channel stays enabled).
        r = anon_client.put(f"{BASE_URL}/api/integrations/resend/config", json={"enabled": True})
        assert r.status_code in (401, 403), r.status_code


mdb_secret_cache = {}


# ---------------- Public tokenized retry endpoints ----------------
class TestPublicRetryEndpoints:
    def test_random_token_get_404(self, anon_client):
        r = anon_client.get(f"{BASE_URL}/api/recovery/pay/rct_{uuid.uuid4().hex}")
        assert r.status_code == 404, r.text

    def test_random_token_complete_404(self, anon_client):
        r = anon_client.post(
            f"{BASE_URL}/api/recovery/pay/rct_{uuid.uuid4().hex}/complete",
            json={"razorpay_payment_id": "pay_TEST_x", "razorpay_order_id": "order_TEST_x", "razorpay_signature": "deadbeef"},
        )
        assert r.status_code == 404, r.text

    def test_complete_missing_fields_400(self, anon_client, real_token):
        r = anon_client.post(f"{BASE_URL}/api/recovery/pay/{real_token['recovery_token']}/complete", json={})
        assert r.status_code in (400, 422), r.text

    def test_real_token_get_exposes_no_secret(self, anon_client, real_token, mdb):
        r = anon_client.get(f"{BASE_URL}/api/recovery/pay/{real_token['recovery_token']}")
        assert r.status_code == 200, r.text
        d = r.json()
        cfg = mdb.integrations.find_one({"provider": "razorpay"}) or {}
        mdb_secret_cache["key_secret"] = (cfg.get("key_secret") or "").strip()
        assert d["order_id"]
        assert isinstance(d["amount_paise"], int) and d["amount_paise"] > 0
        assert d["currency"] == "INR"
        assert d["key_id"] == (cfg.get("key_id") or "").strip()
        for forbidden in ("key_secret", "webhook_secret"):
            assert forbidden not in r.text
        if mdb_secret_cache["key_secret"]:
            assert mdb_secret_cache["key_secret"] not in r.text
        if cfg.get("webhook_secret"):
            assert cfg["webhook_secret"] not in r.text

    def test_invalid_signature_rejected_and_not_linked(self, anon_client, real_token, mdb):
        token = real_token["recovery_token"]
        case = mdb.recovery_cases.find_one({"case_id": real_token["case_id"]})
        r = anon_client.post(
            f"{BASE_URL}/api/recovery/pay/{token}/complete",
            json={
                "razorpay_payment_id": "pay_TESTINVALID9",
                "razorpay_order_id": case["order_key"],
                "razorpay_signature": "0" * 64,
            },
        )
        assert r.status_code == 400, r.text
        assert "signature" in r.json()["detail"].lower()
        after = mdb.recovery_actions.find_one({"action_id": real_token["action_id"]})
        assert not after.get("linked_payment_id"), "invalid signature must NOT link a payment"

    def test_order_mismatch_rejected(self, anon_client, real_token):
        r = anon_client.post(
            f"{BASE_URL}/api/recovery/pay/{real_token['recovery_token']}/complete",
            json={
                "razorpay_payment_id": "pay_TESTMISMATCH",
                "razorpay_order_id": "order_TEST_NOT_MINE",
                "razorpay_signature": "0" * 64,
            },
        )
        assert r.status_code == 400, r.text
        assert "does not match" in r.json()["detail"].lower()


# ---------------- Idempotent completion (ephemeral synthetic action) ----------------
class TestIdempotentCompletion:
    def test_duplicate_completion_no_extra_audit(self, anon_client, mdb):
        cfg = mdb.integrations.find_one({"provider": "razorpay"}) or {}
        key_secret = (cfg.get("key_secret") or "").strip()
        if not key_secret:
            pytest.skip("Razorpay key_secret not configured; cannot forge a valid checkout signature")

        case_id = f"case_TESTIT9{uuid.uuid4().hex[:6]}"
        action_id = f"act_TESTIT9{uuid.uuid4().hex[:6]}"
        token = f"rct_TESTIT9{uuid.uuid4().hex}"
        order_key = f"order_TESTIT9{uuid.uuid4().hex[:6].upper()}"
        payment_id = f"pay_TESTIT9{uuid.uuid4().hex[:8]}"
        sig = hmac.new(key_secret.encode(), f"{order_key}|{payment_id}".encode(), hashlib.sha256).hexdigest()

        mdb.recovery_cases.insert_one({
            "case_id": case_id, "order_key": order_key, "amount_at_risk": 1234.0,
            "currency": "INR", "status": "ACTION_EXECUTED", "source": "TEST_IT9",
        })
        mdb.recovery_actions.insert_one({
            "action_id": action_id, "case_id": case_id, "recovery_token": token,
            "action_type": "SEND_RECOVERY_LINK", "execution_mode": "REAL", "source": "TEST_IT9",
        })
        try:
            r1 = anon_client.post(
                f"{BASE_URL}/api/recovery/pay/{token}/complete",
                json={"razorpay_payment_id": payment_id, "razorpay_order_id": order_key, "razorpay_signature": sig},
            )
            assert r1.status_code == 200, r1.text
            assert r1.json() == {"linked": True, "duplicate": False, "payment_id": payment_id}
            linked = mdb.recovery_actions.find_one({"action_id": action_id})
            assert linked["linked_payment_id"] == payment_id
            audits1 = mdb.audit_events.count_documents({"case_id": case_id, "event_type": "RECOVERY_PAYMENT_LINKED"})
            assert audits1 == 1, audits1

            r2 = anon_client.post(
                f"{BASE_URL}/api/recovery/pay/{token}/complete",
                json={"razorpay_payment_id": payment_id, "razorpay_order_id": order_key, "razorpay_signature": sig},
            )
            assert r2.status_code == 200, r2.text
            assert r2.json()["duplicate"] is True
            assert mdb.audit_events.count_documents({"case_id": case_id, "event_type": "RECOVERY_PAYMENT_LINKED"}) == 1
        finally:
            mdb.recovery_cases.delete_many({"case_id": case_id})
            mdb.recovery_actions.delete_many({"action_id": action_id})
            mdb.audit_events.delete_many({"case_id": case_id})

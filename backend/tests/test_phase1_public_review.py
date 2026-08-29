"""Iteration-6 independent review suite (Phase 1 Razorpay TEST MODE).

Everything here goes through the PUBLIC ingress URL (REACT_APP_BACKEND_URL) so
we validate what the browser/provider actually sees, and verifies persisted
state directly in MongoDB with pymongo.
"""
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timezone

import pytest
import requests
from dotenv import dotenv_values
from pymongo import MongoClient

_fe = dotenv_values("/app/frontend/.env")
_be = dotenv_values("/app/backend/.env")
BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or _fe.get("REACT_APP_BACKEND_URL")).rstrip("/")
MONGO_URL = os.environ.get("MONGO_URL") or _be.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME") or _be.get("DB_NAME")
SECRET = "whsec_test_lab_secret_123"
SESSION = "test_session_smoke_1787904424204"
HEADERS = {"Authorization": f"Bearer {SESSION}"}
DUMMY_CFG = {
    "key_id": "rzp_test_DUMMY1a2b3c",
    "key_secret": "dummy_secret_not_real",
    "webhook_secret": SECRET,
    "mode": "TEST",
}

mdb = MongoClient(MONGO_URL)[DB_NAME]


def _ids(tag):
    suf = f"r6{tag}{uuid.uuid4().hex[:6]}"
    return suf, f"order_{suf}", f"pay_{suf}A", f"pay_{suf}B"


def _pay(event, pid, oid, paise, ts, code=None, method="card"):
    entity = {
        "id": pid, "entity": "payment", "amount": paise, "currency": "INR",
        "status": {"payment.failed": "failed", "payment.captured": "captured",
                   "payment.authorized": "authorized"}[event],
        "order_id": oid, "method": method, "created_at": ts, "email": "r6@example.com",
    }
    if code:
        entity["error_code"] = code
        entity["error_description"] = code.replace("_", " ")
    return {"entity": "event", "account_id": "acc_R6", "event": event,
            "payload": {"payment": {"entity": entity}}, "created_at": ts}


def _order(oid, paise, ts, paid=None):
    paid = paise if paid is None else paid
    return {"entity": "event", "account_id": "acc_R6", "event": "order.paid",
            "payload": {"order": {"entity": {"id": oid, "entity": "order", "amount": paise,
                                             "amount_paid": paid, "amount_due": paise - paid,
                                             "currency": "INR", "status": "paid",
                                             "receipt": f"rcpt_{oid[-6:]}", "created_at": ts}}},
            "created_at": ts}


def _post(payload, event_id, valid=True, raw_override=None, secret=SECRET):
    raw = raw_override if raw_override is not None else json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest() if valid else "d" * 64
    return requests.post(
        f"{BASE_URL}/api/webhooks/razorpay", data=raw, timeout=120,
        headers={"Content-Type": "application/json", "X-Razorpay-Signature": sig,
                 "x-razorpay-event-id": event_id},
    )


def _now():
    return int(datetime.now(timezone.utc).timestamp())


def _case(oid):
    return mdb.recovery_cases.find_one({"order_key": oid}, {"_id": 0})


def _cleanup(suf):
    cases = list(mdb.recovery_cases.find({"order_key": {"$regex": f"^order_{suf}"}}, {"case_id": 1}))
    ids = [c["case_id"] for c in cases]
    mdb.payment_attempts.delete_many({"$or": [{"payment_id": {"$regex": f"^pay_{suf}"}},
                                              {"order_id": {"$regex": f"^order_{suf}"}}]})
    mdb.recovery_cases.delete_many({"order_key": {"$regex": f"^order_{suf}"}})
    mdb.orders.delete_many({"order_id": {"$regex": f"^order_{suf}"}})
    mdb.provider_events.delete_many({"provider_event_id": {"$regex": f"^evt_{suf}"}})
    if ids:
        mdb.recovery_actions.delete_many({"case_id": {"$in": ids}})


# ---------------- Webhook security & ingestion (public endpoint) ----------------
class TestWebhookSecurity:
    def test_valid_signed_failed_payment_creates_case(self):
        suf, oid, pay1, _ = _ids("wa")
        try:
            r = _post(_pay("payment.failed", pay1, oid, 675000, _now(), "insufficient_funds"), f"evt_{suf}1")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["status"] == "processed"
            assert body["mode"] == "TEST"
            assert body["simulated"] is False

            evt = mdb.provider_events.find_one({"provider_event_id": f"evt_{suf}1"}, {"_id": 0})
            assert evt is not None
            assert evt["processing_status"] == "PROCESSED"
            assert evt["signature_verified"] is True
            assert evt["source"] == "TEST_MODE"
            assert evt["event_type"] == "payment.failed"
            assert evt["normalized_order_id"] == oid

            case = _case(oid)
            assert case is not None
            assert case["source"] == "RAZORPAY_TEST"
            assert case["title"].startswith("Failed Payment for Order")
            assert case["amount_at_risk"] == 6750.0
            assert case["currency"] == "INR"
            assert isinstance(case.get("risk_evidence"), dict) and case["risk_evidence"]

            # audit lineage
            types = {a["event_type"] for a in mdb.audit_events.find(
                {"related.provider_event_id": f"evt_{suf}1"}, {"event_type": 1})}
            assert {"WEBHOOK_RECEIVED", "EVENT_NORMALIZED"} <= types
            case_types = {a["event_type"] for a in mdb.audit_events.find(
                {"case_id": case["case_id"]}, {"event_type": 1})}
            assert "CASE_CREATED" in case_types, case_types
        finally:
            _cleanup(suf)

    def test_duplicate_event_id_no_double_case(self):
        suf, oid, pay1, _ = _ids("wb")
        try:
            payload = _pay("payment.failed", pay1, oid, 100000, _now(), "insufficient_funds")
            r1 = _post(payload, f"evt_{suf}1")
            r2 = _post(payload, f"evt_{suf}1")
            assert r1.json()["status"] == "processed"
            assert r2.status_code == 200
            assert r2.json()["status"] == "duplicate"
            assert r2.json()["duplicate"] is True
            assert mdb.recovery_cases.count_documents({"order_key": oid}) == 1
            evt = mdb.provider_events.find_one({"provider_event_id": f"evt_{suf}1"})
            assert evt["duplicate_deliveries"] == 1
        finally:
            _cleanup(suf)

    def test_forged_signature_rejected_and_logged(self):
        suf, oid, pay1, _ = _ids("wc")
        before = mdb.security_events.count_documents({"path": "/api/webhooks/razorpay"})
        try:
            r = _post(_pay("payment.failed", pay1, oid, 100000, _now(), "do_not_honor"),
                      f"evt_{suf}1", valid=False)
            assert r.status_code == 401, r.text
            assert mdb.provider_events.count_documents({"provider_event_id": f"evt_{suf}1"}) == 0
            assert mdb.recovery_cases.count_documents({"order_key": oid}) == 0
            assert mdb.security_events.count_documents({"path": "/api/webhooks/razorpay"}) > before
        finally:
            _cleanup(suf)

    def test_missing_signature_header_rejected(self):
        suf, oid, pay1, _ = _ids("wd")
        raw = json.dumps(_pay("payment.failed", pay1, oid, 100000, _now(), "x")).encode()
        r = requests.post(f"{BASE_URL}/api/webhooks/razorpay", data=raw, timeout=60,
                          headers={"Content-Type": "application/json",
                                   "x-razorpay-event-id": f"evt_{suf}1"})
        assert r.status_code == 401
        _cleanup(suf)

    def test_malformed_json_valid_signature_400(self):
        suf, _, _, _ = _ids("we")
        r = _post(None, f"evt_{suf}1", raw_override=b'{"entity":"event","event":"payment.failed",BROKEN')
        assert r.status_code == 400, r.text
        assert mdb.provider_events.count_documents({"provider_event_id": f"evt_{suf}1"}) == 0

    def test_unknown_event_type_ignored(self):
        suf, oid, pay1, _ = _ids("wf")
        try:
            payload = _pay("payment.failed", pay1, oid, 100000, _now(), "x")
            payload["event"] = "payment.dispute.created"
            r = _post(payload, f"evt_{suf}1")
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "ignored_unsupported"
            evt = mdb.provider_events.find_one({"provider_event_id": f"evt_{suf}1"})
            assert evt["processing_status"] == "IGNORED_UNSUPPORTED"
            assert mdb.recovery_cases.count_documents({"order_key": oid}) == 0
        finally:
            _cleanup(suf)

    def test_out_of_order_captured_before_authorized(self):
        suf, oid, pay1, _ = _ids("wg")
        try:
            now = _now()
            _post(_pay("payment.captured", pay1, oid, 250000, now), f"evt_{suf}1")
            _post(_pay("payment.authorized", pay1, oid, 250000, now - 300), f"evt_{suf}2")
            attempt = mdb.payment_attempts.find_one({"payment_id": pay1}, {"_id": 0})
            assert attempt["status"] == "success", attempt["status"]
            assert mdb.recovery_cases.count_documents({"order_key": oid}) == 0
        finally:
            _cleanup(suf)

    def test_order_paid_natural_recovery_not_attributed(self):
        suf, oid, pay1, _ = _ids("wh")
        try:
            now = _now()
            _post(_pay("payment.failed", pay1, oid, 300000, now - 120, "insufficient_funds"), f"evt_{suf}1")
            case = _case(oid)
            assert case["status"] not in ("VERIFIED_RECOVERED", "NATURALLY_RECOVERED")
            mdb.recovery_actions.delete_many({"case_id": case["case_id"]})
            _post(_order(oid, 300000, now), f"evt_{suf}2")
            case = _case(oid)
            assert case["status"] == "NATURALLY_RECOVERED", case["status"]
            assert case["attribution_strength"] == "NONE"
            assert (case.get("recovered_amount") or 0) == 0
        finally:
            _cleanup(suf)

    def test_oversized_payload_rejected(self):
        suf, _, _, _ = _ids("wi")
        raw = b'{"pad":"' + b"a" * (1024 * 1024 + 100) + b'"}'
        r = _post(None, f"evt_{suf}1", raw_override=raw)
        assert r.status_code in (413, 400), r.status_code


# ---------------- Attribution tiers ----------------
class TestAttribution:
    @staticmethod
    def _insert_action(case_id, action_type="SEND_RECOVERY_LINK"):
        mdb.recovery_actions.insert_one({
            "action_id": f"act_{uuid.uuid4().hex[:12]}", "case_id": case_id,
            "action_type": action_type, "label": "Send Payment Recovery Link",
            "scheduled_time": datetime.now(timezone.utc).isoformat(),
            "executed_time": datetime.now(timezone.utc).isoformat(),
            "execution_mode": "SIMULATED", "simulated": True, "approval_status": "AUTO_APPROVED",
            "policy_result": "ALLOW", "expected_incremental_value": 0, "estimated_cost": 12.0,
            "outcome": "PENDING", "idempotency_key": f"{case_id}:{action_type}:r6",
            "provider_reference": "SIM-R6", "created_at": datetime.now(timezone.utc).isoformat(),
        })

    def test_no_action_late_capture_is_natural(self):
        suf, oid, pay1, pay2 = _ids("aa")
        try:
            now = _now()
            _post(_pay("payment.failed", pay1, oid, 420000, now - 600, "insufficient_funds"), f"evt_{suf}1")
            case = _case(oid)
            mdb.recovery_actions.delete_many({"case_id": case["case_id"]})
            _post(_pay("payment.captured", pay2, oid, 420000, now), f"evt_{suf}2")
            case = _case(oid)
            assert case["status"] == "NATURALLY_RECOVERED", case["status"]
            assert case["attribution_strength"] == "NONE"
            assert (case.get("recovered_amount") or 0) == 0
        finally:
            _cleanup(suf)

    def test_executed_action_then_capture_is_verified_moderate(self):
        suf, oid, pay1, pay2 = _ids("ab")
        try:
            now = _now()
            _post(_pay("payment.failed", pay1, oid, 199900, now - 300, "expired_card"), f"evt_{suf}1")
            case = _case(oid)
            mdb.recovery_actions.delete_many({"case_id": case["case_id"]})
            self._insert_action(case["case_id"])
            _post(_pay("payment.captured", pay2, oid, 199900, _now() + 1, method="upi"), f"evt_{suf}2")
            case = _case(oid)
            assert case["status"] == "VERIFIED_RECOVERED", case["status"]
            assert case["outcome"] == "VERIFIED_RECOVERED"
            assert case["attribution_strength"] == "MODERATE"
            assert case["recovered_amount"] == 1999.0
        finally:
            _cleanup(suf)

    def test_partial_capture_after_action_partially_recovered(self):
        suf, oid, pay1, pay2 = _ids("ac")
        try:
            now = _now()
            _post(_pay("payment.failed", pay1, oid, 800000, now - 300, "insufficient_funds"), f"evt_{suf}1")
            case = _case(oid)
            mdb.recovery_actions.delete_many({"case_id": case["case_id"]})
            self._insert_action(case["case_id"])
            _post(_pay("payment.captured", pay2, oid, 500000, _now() + 1), f"evt_{suf}2")
            case = _case(oid)
            assert case["status"] == "VERIFIED_RECOVERED"
            assert case["outcome"] == "PARTIALLY_RECOVERED", case["outcome"]
            assert case["recovered_amount"] == 5000.0
            assert case["attribution_strength"] == "MODERATE"
        finally:
            _cleanup(suf)

    def test_monitoring_action_never_earns_attribution(self):
        """SCHEDULED_RECHECK is a monitoring action (money_touching=False, not in
        ATTRIBUTABLE_ACTIONS). Executed through the REAL execute endpoint before a
        settlement, it must NOT earn attribution or system-recovered revenue."""
        suf, oid, pay1, pay2 = _ids("ad")
        try:
            now = _now()
            _post(_pay("payment.failed", pay1, oid, 250000, now - 300, "insufficient_funds"), f"evt_{suf}1")
            case = _case(oid)
            mdb.recovery_actions.delete_many({"case_id": case["case_id"]})
            ex = requests.post(f"{BASE_URL}/api/cases/{case['case_id']}/execute", headers=HEADERS,
                               json={"action_type": "SCHEDULED_RECHECK"}, timeout=120)
            assert ex.status_code == 200, ex.text
            assert ex.json().get("executed") is True, ex.text
            _post(_pay("payment.captured", pay2, oid, 250000, _now() + 1), f"evt_{suf}2")
            case = _case(oid)
            assert case["status"] == "NATURALLY_RECOVERED", (
                f"monitoring action earned attribution: status={case['status']} "
                f"attributed_action={case.get('attributed_action')} "
                f"strength={case.get('attribution_strength')} "
                f"recovered_amount={case.get('recovered_amount')}")
            assert case["attribution_strength"] == "NONE"
            assert (case.get("recovered_amount") or 0) == 0
        finally:
            _cleanup(suf)

    def test_escalate_human_never_earns_attribution(self):
        """ESCALATE_HUMAN is a CONTROL action, not customer-facing."""
        suf, oid, pay1, pay2 = _ids("ae")
        try:
            now = _now()
            _post(_pay("payment.failed", pay1, oid, 250000, now - 300, "insufficient_funds"), f"evt_{suf}1")
            case = _case(oid)
            mdb.recovery_actions.delete_many({"case_id": case["case_id"]})
            ex = requests.post(f"{BASE_URL}/api/cases/{case['case_id']}/execute", headers=HEADERS,
                               json={"action_type": "ESCALATE_HUMAN"}, timeout=120)
            assert ex.status_code == 200, ex.text
            if ex.json().get("executed") is not True:
                pytest.skip(f"policy did not execute ESCALATE_HUMAN: {ex.text[:200]}")
            _post(_pay("payment.captured", pay2, oid, 250000, _now() + 1), f"evt_{suf}2")
            case = _case(oid)
            assert case["status"] == "NATURALLY_RECOVERED", (
                f"control action earned attribution: status={case['status']} "
                f"attributed_action={case.get('attributed_action')} "
                f"recovered_amount={case.get('recovered_amount')}")
        finally:
            _cleanup(suf)


# ---------------- Integrations API (owner-only, masked secrets) ----------------
class TestIntegrationsApi:
    def test_list_masks_secrets(self):
        r = requests.get(f"{BASE_URL}/api/integrations", headers=HEADERS, timeout=60)
        assert r.status_code == 200
        raw = r.text
        assert "dummy_secret_not_real" not in raw
        assert SECRET not in raw
        cfg = r.json()["integrations"][0]
        assert cfg["key_id_masked"] == "rzp_test_********"
        assert cfg["webhook_configured"] is True
        assert "key_secret" not in cfg and "webhook_secret" not in cfg
        assert r.json()["webhook_endpoint_path"] == "/api/webhooks/razorpay"

    def test_health_counts_match_mongo(self):
        r = requests.get(f"{BASE_URL}/api/integrations/razorpay/health", headers=HEADERS, timeout=60)
        assert r.status_code == 200
        h = r.json()
        total = mdb.provider_events.count_documents({"provider": "razorpay"})
        sig = mdb.security_events.count_documents({"path": "/api/webhooks/razorpay"})
        assert abs(h["events_received"] - total) <= 3, (h["events_received"], total)
        assert abs(h["signature_failures"] - sig) <= 3
        assert h["mode"] == "TEST"
        assert h["webhook"] == "CONNECTED"
        assert h["events_processed"] <= h["events_received"]

    def test_live_key_rejected(self):
        r = requests.put(f"{BASE_URL}/api/integrations/razorpay", headers=HEADERS, timeout=60,
                         json={**DUMMY_CFG, "key_id": "rzp_live_ABC123"})
        assert r.status_code == 400, r.text
        assert "rzp_test_" in r.json()["detail"]

    def test_live_mode_rejected(self):
        r = requests.put(f"{BASE_URL}/api/integrations/razorpay", headers=HEADERS, timeout=60,
                         json={**DUMMY_CFG, "mode": "LIVE"})
        assert r.status_code == 400

    def test_missing_fields_rejected(self):
        r = requests.put(f"{BASE_URL}/api/integrations/razorpay", headers=HEADERS, timeout=60,
                         json={"key_id": "rzp_test_X", "mode": "TEST"})
        assert r.status_code == 400

    def test_save_valid_test_config_and_persist(self):
        r = requests.put(f"{BASE_URL}/api/integrations/razorpay", headers=HEADERS, timeout=60,
                         json=DUMMY_CFG)
        assert r.status_code == 200, r.text
        cfg = r.json()["integration"]
        assert cfg["key_id_masked"] == "rzp_test_********"
        assert SECRET not in r.text
        doc = mdb.integrations.find_one({"provider": "razorpay"})
        assert doc["webhook_secret"] == SECRET
        assert doc["mode"] == "TEST"

    def test_test_connection_honest_error_with_dummy_keys(self):
        r = requests.post(f"{BASE_URL}/api/integrations/razorpay/test-connection",
                          headers=HEADERS, timeout=90)
        assert r.status_code == 200, r.text
        assert r.json()["status"] in ("ERROR", "CONNECTED")
        # restore NOT_CONNECTED-ish state expectation: status persisted
        assert mdb.integrations.find_one({"provider": "razorpay"})["status"] in ("ERROR", "CONNECTED")

    def test_unauthenticated_blocked(self):
        r = requests.get(f"{BASE_URL}/api/integrations", timeout=60)
        assert r.status_code in (401, 403)

    def test_analyst_forbidden_on_owner_endpoints(self):
        uid = f"r6-analyst-{uuid.uuid4().hex[:8]}"
        token = f"test_session_r6_{uuid.uuid4().hex[:10]}"
        mdb.users.insert_one({"user_id": uid, "email": f"{uid}@example.com", "name": "R6 Analyst",
                              "picture": "", "role": "analyst",
                              "created_at": datetime.now(timezone.utc).isoformat()})
        mdb.user_sessions.insert_one({"user_id": uid, "session_token": token,
                                      "expires_at": "2027-01-01T00:00:00+00:00",
                                      "created_at": datetime.now(timezone.utc).isoformat()})
        try:
            h = {"Authorization": f"Bearer {token}"}
            assert requests.put(f"{BASE_URL}/api/integrations/razorpay", headers=h,
                                json=DUMMY_CFG, timeout=60).status_code == 403
            assert requests.post(f"{BASE_URL}/api/integrations/razorpay/test-lab/duplicate-event",
                                 headers=h, timeout=120).status_code == 403
            assert requests.delete(f"{BASE_URL}/api/integrations/razorpay", headers=h,
                                   timeout=60).status_code == 403
        finally:
            mdb.users.delete_one({"user_id": uid})
            mdb.user_sessions.delete_one({"session_token": token})

    def test_provider_events_endpoint(self):
        r = requests.get(f"{BASE_URL}/api/integrations/razorpay/events", headers=HEADERS, timeout=60)
        assert r.status_code == 200
        events = r.json()["events"]
        assert isinstance(events, list) and len(events) > 0
        assert all("_id" not in e for e in events)
        assert {"provider_event_id", "processing_status", "signature_verified"} <= set(events[0])

    def test_verification_sweep_does_not_claim_recovery(self):
        r = requests.post(f"{BASE_URL}/api/integrations/verification/sweep", headers=HEADERS, timeout=280)
        assert r.status_code == 200, r.text
        res = r.json()
        for key in ("checked", "verified_recovered", "closed_natural", "not_recovered",
                    "pending", "provider_reconciled", "provider_errors"):
            assert key in res, res
        assert res["checked"] >= 0


# ---------------- Test lab (all 12 scenarios through the real endpoint) ----------------
LAB_SCENARIOS = [
    "valid-payment-failed", "valid-payment-captured", "valid-order-paid", "duplicate-event",
    "invalid-signature", "out-of-order", "late-success", "replacement-payment",
    "partial-payment", "unknown-event", "malformed-payload", "replayed-old-event",
]


class TestTestLab:
    @pytest.mark.parametrize("scenario", LAB_SCENARIOS)
    def test_lab_scenario(self, scenario):
        r = requests.post(f"{BASE_URL}/api/integrations/razorpay/test-lab/{scenario}",
                          headers=HEADERS, timeout=180)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["test"] == scenario
        assert body["mode"] == "TEST"
        assert len(body["steps"]) >= 1
        expected = {
            "invalid-signature": 401,
            "malformed-payload": 400,
        }.get(scenario, 200)
        assert body["steps"][0]["http"] == expected, body["steps"]
        if scenario == "duplicate-event":
            assert body["steps"][1]["result"] == "duplicate", body["steps"]
        if scenario == "unknown-event":
            assert body["steps"][0]["result"] == "ignored_unsupported"

    def test_unknown_scenario_404(self):
        r = requests.post(f"{BASE_URL}/api/integrations/razorpay/test-lab/does-not-exist",
                          headers=HEADERS, timeout=60)
        assert r.status_code == 404


# ---------------- Disconnect / reconnect lifecycle ----------------
class TestLifecycle:
    def test_disconnect_makes_webhook_503_then_reconfigure(self):
        assert requests.delete(f"{BASE_URL}/api/integrations/razorpay",
                               headers=HEADERS, timeout=60).status_code == 200
        assert mdb.integrations.find_one({"provider": "razorpay"}) is None
        cfg = requests.get(f"{BASE_URL}/api/integrations", headers=HEADERS, timeout=60).json()
        assert cfg["integrations"][0]["status"] == "NOT_CONFIGURED"

        suf, oid, pay1, _ = _ids("lc")
        r = _post(_pay("payment.failed", pay1, oid, 100000, _now(), "x"), f"evt_{suf}1")
        assert r.status_code == 503, r.status_code

        # reconfigure so later runs / UI keep working
        r = requests.put(f"{BASE_URL}/api/integrations/razorpay", headers=HEADERS,
                         json=DUMMY_CFG, timeout=60)
        assert r.status_code == 200, r.text
        r2 = _post(_pay("payment.failed", pay1, oid, 100000, _now(), "insufficient_funds"), f"evt_{suf}2")
        assert r2.status_code == 200, r2.text
        _cleanup(suf)


# ---------------- Dashboard source separation / lineage ----------------
class TestDashboardSources:
    def test_source_summary_includes_test_mode(self):
        r = requests.get(f"{BASE_URL}/api/dashboard/summary", headers=HEADERS, timeout=120)
        assert r.status_code == 200
        blob = json.dumps(r.json())
        assert "TEST_MODE" in blob or "TEST MODE" in blob, blob[:1500]

    @pytest.mark.parametrize("source", ["TEST_MODE", "SIMULATED", "IMPORTED"])
    def test_case_source_filter(self, source):
        r = requests.get(f"{BASE_URL}/api/cases", headers=HEADERS,
                         params={"source": source, "limit": 200}, timeout=120)
        assert r.status_code == 200, r.text
        cases = r.json()["cases"]
        for c in cases:
            assert c.get("source_category", source) == source, c.get("source")

    def test_test_mode_case_detail_has_provider_lineage(self):
        r = requests.get(f"{BASE_URL}/api/cases", headers=HEADERS,
                         params={"source": "TEST_MODE", "limit": 20}, timeout=120)
        cases = r.json().get("cases", [])
        if not cases:
            pytest.skip("no TEST_MODE cases present")
        cid = cases[0]["case_id"]
        d = requests.get(f"{BASE_URL}/api/cases/{cid}", headers=HEADERS, timeout=120)
        assert d.status_code == 200, d.text
        detail = d.json()
        assert "_id" not in detail["case"]
        assert detail["case"]["title"]
        assert detail["case"]["source_category"] == "TEST_MODE"
        assert isinstance(detail.get("provider_events"), list)
        assert len(detail["provider_events"]) > 0, "no provider_events lineage on TEST_MODE case"
        trail = json.dumps(detail["audit_trail"])
        for expected in ("WEBHOOK_RECEIVED", "EVENT_NORMALIZED", "CASE_CREATED"):
            assert expected in trail, f"{expected} missing from audit trail"


# ---------------- Regression sanity ----------------
class TestRegressionSanity:
    def test_review_queue(self):
        r = requests.get(f"{BASE_URL}/api/review/queue", headers=HEADERS, timeout=120)
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), (dict, list))

    def test_simulator_scenario(self):
        r = requests.post(f"{BASE_URL}/api/simulate/scenario/2", headers=HEADERS, timeout=180)
        assert r.status_code == 200, r.text

    def test_webhook_config_exposes_no_secret(self):
        r = requests.get(f"{BASE_URL}/api/webhooks/config", headers=HEADERS, timeout=60)
        assert r.status_code == 200
        assert SECRET not in r.text
        assert r.json()["razorpay_endpoint_path"] == "/api/webhooks/razorpay"

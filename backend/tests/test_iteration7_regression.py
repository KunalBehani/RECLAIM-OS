"""Iteration-7 independent regression: verifies iteration_6 fixes via PUBLIC APIs only.

FIX 1 attribution allow-list (executed_before), FIX 2 provider lineage in case
audit_trail, FIX 4 /api/cases default limit.
"""
import hashlib
import hmac
import json
import os
import time
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
HEADERS = {"Authorization": "Bearer test_session_smoke_1787904424204"}

mdb = MongoClient(MONGO_URL)[DB_NAME]


def _now():
    return int(datetime.now(timezone.utc).timestamp())


def _pay(event, pid, oid, paise, ts, code=None):
    ent = {"id": pid, "entity": "payment", "amount": paise, "currency": "INR",
           "status": "failed" if event == "payment.failed" else "captured",
           "order_id": oid, "method": "card", "created_at": ts, "email": "i7@example.com"}
    if code:
        ent["error_code"] = code
        ent["error_description"] = code.replace("_", " ")
    return {"entity": "event", "account_id": "acc_I7", "event": event,
            "payload": {"payment": {"entity": ent}}, "created_at": ts}


def _post(payload, event_id):
    raw = json.dumps(payload).encode()
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    r = requests.post(f"{BASE_URL}/api/webhooks/razorpay", data=raw, timeout=180,
                      headers={"Content-Type": "application/json",
                               "X-Razorpay-Signature": sig, "x-razorpay-event-id": event_id})
    assert r.status_code == 200, f"webhook rejected {r.status_code} {r.text[:300]}"
    return r


def _case_by_order(oid):
    return mdb.recovery_cases.find_one({"order_key": oid}, {"_id": 0})


def _cleanup(suf):
    for c in mdb.recovery_cases.find({"order_key": {"$regex": f"^order_{suf}"}}, {"case_id": 1}):
        mdb.recovery_actions.delete_many({"case_id": c["case_id"]})
        mdb.audit_records.delete_many({"case_id": c["case_id"]})
    mdb.payment_attempts.delete_many({"order_id": {"$regex": f"^order_{suf}"}})
    mdb.recovery_cases.delete_many({"order_key": {"$regex": f"^order_{suf}"}})
    mdb.orders.delete_many({"order_id": {"$regex": f"^order_{suf}"}})


def _flow(action_type):
    """failed webhook -> case -> execute action -> captured webhook -> return case."""
    suf = f"i7{uuid.uuid4().hex[:6]}"
    oid = f"order_{suf}"
    try:
        ts = _now() - 600
        _post(_pay("payment.failed", f"pay_{suf}A", oid, 250000, ts, "GATEWAY_ERROR"), f"ev_{suf}_f")
        case = None
        for _ in range(40):
            case = _case_by_order(oid)
            if case:
                break
            time.sleep(2)
        assert case, f"no case created for {oid}"
        cid = case["case_id"]

        # Isolate the action under test from autonomous pipeline actions.
        time.sleep(3)
        pipeline_actions = [a.get("action_type") for a in
                            mdb.recovery_actions.find({"case_id": cid}, {"action_type": 1})]
        print(f"[{action_type}] pipeline actions removed for isolation: {pipeline_actions}")
        mdb.recovery_actions.delete_many({"case_id": cid})

        ex = requests.post(f"{BASE_URL}/api/cases/{cid}/execute", headers=HEADERS,
                           json={"action_type": action_type}, timeout=120)
        assert ex.status_code == 200, f"execute {action_type} failed: {ex.status_code} {ex.text[:300]}"
        assert ex.json().get("executed") is True, ex.text[:300]

        _post(_pay("payment.captured", f"pay_{suf}B", oid, 250000, _now() + 1), f"ev_{suf}_c")
        for _ in range(40):
            case = _case_by_order(oid)
            if case and case.get("status") in ("VERIFIED_RECOVERED", "NATURALLY_RECOVERED",
                                               "INVALIDATED"):
                break
            time.sleep(2)
        return cid, _case_by_order(oid)
    finally:
        pass  # cleanup done by caller after assertions


# ---------- FIX 1: attribution allow-list ----------
@pytest.mark.parametrize("action", ["SCHEDULED_RECHECK", "ESCALATE_HUMAN"])
def test_non_attributable_action_before_settlement_earns_nothing(action):
    cid, case = _flow(action)
    suf = case["order_key"].split("order_")[1]
    try:
        assert case["status"] == "NATURALLY_RECOVERED", \
            f"{action}: expected NATURALLY_RECOVERED got {case['status']}"
        assert case.get("attribution_strength") == "NONE", \
            f"{action}: expected attribution NONE got {case.get('attribution_strength')}"
        assert float(case.get("recovered_amount") or 0) == 0.0, \
            f"{action}: recovered_amount should be 0, got {case.get('recovered_amount')}"
    finally:
        _cleanup(suf)


def test_attributable_action_still_earns_moderate():
    cid, case = _flow("SEND_RECOVERY_LINK")
    suf = case["order_key"].split("order_")[1]
    try:
        assert case["status"] == "VERIFIED_RECOVERED", f"got {case['status']}"
        assert case.get("attribution_strength") == "MODERATE", \
            f"got {case.get('attribution_strength')}"
        assert float(case.get("recovered_amount") or 0) == 2500.0, \
            f"got {case.get('recovered_amount')}"
    finally:
        _cleanup(suf)


# ---------- FIX 2: provider lineage on case detail ----------
def test_case_detail_includes_provider_lineage():
    suf = f"i7{uuid.uuid4().hex[:6]}"
    oid = f"order_{suf}"
    try:
        _post(_pay("payment.failed", f"pay_{suf}A", oid, 180000, _now() - 300, "GATEWAY_ERROR"),
              f"ev_{suf}_lf")
        case = None
        for _ in range(40):
            case = _case_by_order(oid)
            if case:
                break
            time.sleep(2)
        assert case, "case not created"
        r = requests.get(f"{BASE_URL}/api/cases/{case['case_id']}", headers=HEADERS, timeout=60)
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert '"_id"' not in json.dumps(body), "ObjectId leaked in response"
        trail = body.get("audit_trail") or []
        events = [e.get("event_type") for e in trail]
        print(f"[lineage] audit_trail: {events}")
        assert "WEBHOOK_RECEIVED" in events, f"missing WEBHOOK_RECEIVED, got {events}"
        assert "EVENT_NORMALIZED" in events, f"missing EVENT_NORMALIZED, got {events}"
        assert "CASE_CREATED" in events, f"missing CASE_CREATED, got {events}"
        # lineage entries must come first chronologically
        assert events.index("WEBHOOK_RECEIVED") < events.index("CASE_CREATED"), events

        rp = requests.get(f"{BASE_URL}/api/cases/{case['case_id']}/replay",
                          headers=HEADERS, timeout=60)
        assert rp.status_code == 200, rp.text[:300]
        replay_events = [s.get("event_type") for s in rp.json().get("steps", [])]
        print(f"[lineage] replay steps: {replay_events}")
        assert "WEBHOOK_RECEIVED" in replay_events and "EVENT_NORMALIZED" in replay_events, \
            f"decision replay missing provider lineage steps: {replay_events}"
    finally:
        _cleanup(suf)


# ---------- FIX 4: list limit ----------
def test_cases_default_limit_is_500():
    r = requests.get(f"{BASE_URL}/api/cases", headers=HEADERS, timeout=90)
    assert r.status_code == 200, r.text[:300]
    cases = r.json()["cases"]
    total = mdb.recovery_cases.count_documents({})
    assert len(cases) == min(total, 500), f"returned {len(cases)} of {total} total"
    assert '"_id"' not in json.dumps(cases[:5]), "ObjectId leaked"


# ---------- FIX 3: pre-execution settle guard (live near-simultaneous delivery) ----------
def test_race_no_post_settlement_execution():
    suf = f"i7{uuid.uuid4().hex[:6]}"
    oid = f"order_{suf}"
    try:
        ts = _now() - 60
        import threading
        errs = []

        def _deliver(payload, eid, delay=0.0):
            time.sleep(delay)
            try:
                _post(payload, eid)
            except Exception as exc:  # noqa: BLE001
                errs.append(exc)

        t1 = threading.Thread(target=_deliver, args=(
            _pay("payment.failed", f"pay_{suf}A", oid, 300000, ts, "GATEWAY_ERROR"), f"ev_{suf}_rf"))
        t2 = threading.Thread(target=_deliver, args=(
            _pay("payment.captured", f"pay_{suf}B", oid, 300000, ts + 5), f"ev_{suf}_rc", 1.0))
        t1.start(); t2.start(); t1.join(); t2.join()
        assert not errs, errs
        case = None
        for _ in range(45):
            case = _case_by_order(oid)
            if case and case.get("status") in ("VERIFIED_RECOVERED", "NATURALLY_RECOVERED",
                                               "INVALIDATED", "PARTIALLY_RECOVERED"):
                break
            time.sleep(2)
        assert case, "no case created"
        time.sleep(5)
        case = _case_by_order(oid)
        actions = list(mdb.recovery_actions.find({"case_id": case["case_id"]}, {"_id": 0}))
        executed = [a["action_type"] for a in actions if a.get("executed_time")]
        print(f"[race] status={case['status']} attribution={case.get('attribution_strength')} "
              f"executed={executed} recovered={case.get('recovered_amount')}")
        assert case["status"] in ("NATURALLY_RECOVERED", "INVALIDATED"), case["status"]
        assert case.get("attribution_strength") in (None, "NONE"), case.get("attribution_strength")
        money_touching = [a for a in executed if a in
                          {"SAFE_PAYMENT_RETRY", "SEND_RECOVERY_LINK", "CUSTOMER_REMINDER"}]
        assert not money_touching, f"money-touching action executed after settlement: {money_touching}"
    finally:
        _cleanup(suf)

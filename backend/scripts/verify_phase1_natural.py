"""Live Phase-1 verification: deliver a genuinely-signed payment.captured
webhook for the REAL case order_TWKE56rzxX1S63 through the public webhook
endpoint (same signed-delivery mechanism as the built-in Webhook Test Lab),
then assert the case closes as NATURALLY_RECOVERED and contributes nothing to
Verified Net Recovery — because its only executed action was SIMULATED.

Never prints or logs secrets.
"""
import hashlib
import hmac
import json
import os
import uuid

import requests
from dotenv import dotenv_values

_be = dotenv_values("/app/backend/.env")
_fe = dotenv_values("/app/frontend/.env")
BASE = _fe["REACT_APP_BACKEND_URL"].rstrip("/")
SESSION = "test_session_smoke_1787904424204"
HEADERS = {"Authorization": f"Bearer {SESSION}"}
import sys

ORDER = sys.argv[1] if len(sys.argv) > 1 else "order_TWKE56rzzX1S63"
AMOUNT_PAISE = 50000

import pymongo

mdb = pymongo.MongoClient(_be["MONGO_URL"])[_be["DB_NAME"]]
cfg = mdb.integrations.find_one({"provider": "razorpay"}, {"_id": 0, "webhook_secret": 1})
assert cfg and cfg.get("webhook_secret"), "razorpay integration not configured"
secret = cfg["webhook_secret"]

case = mdb.recovery_cases.find_one({"order_key": ORDER}, {"_id": 0})
assert case, f"case for {ORDER} not found"
print(json.dumps({"before": {"status": case["status"], "source": case.get("source"),
                             "amount_at_risk": case.get("amount_at_risk"),
                             "recovered_amount": case.get("recovered_amount")}}, indent=2))

metrics_before = requests.get(f"{BASE}/api/dashboard/summary", headers=HEADERS, timeout=60).json()
print("verified_net_recovery BEFORE:", json.dumps(metrics_before["kpis"]["verified_net_recovery"]))

suf = uuid.uuid4().hex[:8]
pay_id = f"pay_E2E{suf}"
event_id = f"evt_E2E{suf}"
import time
now = int(time.time())
payload = {
    "entity": "event", "account_id": "acc_E2E", "event": "payment.captured",
    "payload": {"payment": {"entity": {
        "id": pay_id, "entity": "payment", "amount": AMOUNT_PAISE, "currency": "INR",
        "status": "captured", "order_id": ORDER, "method": "card", "created_at": now,
        "email": "e2e-verify@example.com",
    }}},
    "created_at": now,
}
raw = json.dumps(payload).encode()
sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
r = requests.post(f"{BASE}/api/webhooks/razorpay", data=raw, timeout=60,
                  headers={"Content-Type": "application/json",
                           "X-Razorpay-Signature": sig, "x-razorpay-event-id": event_id})
print("webhook delivery:", r.status_code, json.dumps(r.json())[:400])

case = mdb.recovery_cases.find_one({"order_key": ORDER}, {"_id": 0})
audit = list(mdb.audit_events.find({"case_id": case["case_id"], "event_type": "CASE_CLOSED"}, {"_id": 0, "reason": 1}))
metrics_after = requests.get(f"{BASE}/api/dashboard/summary", headers=HEADERS, timeout=60).json()
print(json.dumps({"after": {"status": case["status"], "outcome": case.get("outcome"),
                            "attribution_strength": case.get("attribution_strength"),
                            "recovered_amount": case.get("recovered_amount"),
                            "natural_recovered_amount": case.get("natural_recovered_amount"),
                            "verification_status": case.get("verification_status")}}, indent=2))
print("close reason:", audit[-1]["reason"] if audit else None)
print("verified_net_recovery AFTER:", json.dumps(metrics_after["kpis"]["verified_net_recovery"]))
print("natural_recovered_not_counted AFTER:", json.dumps(metrics_after["kpis"]["natural_recovered_not_counted"]))

ok = (
    r.status_code == 200
    and case["status"] == "NATURALLY_RECOVERED"
    and case.get("attribution_strength") == "NONE"
    and float(case.get("recovered_amount") or 0) == 0.0
    and float(case.get("natural_recovered_amount") or 0) == 500.0
    and metrics_after["kpis"]["verified_net_recovery"] == metrics_before["kpis"]["verified_net_recovery"]
)
print("VERDICT:", "PASS — NATURALLY_RECOVERED, zero Verified Net Recovery contribution" if ok else "FAIL")

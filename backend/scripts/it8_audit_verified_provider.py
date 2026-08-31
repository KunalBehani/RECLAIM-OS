"""Audit: do any RAZORPAY_* cases currently counted as VERIFIED_RECOVERED owe
their attribution to a SIMULATED action? (mandate violation if yes)"""
import json

import pymongo
from dotenv import dotenv_values

be = dotenv_values("/app/backend/.env")
db = pymongo.MongoClient(be["MONGO_URL"])[be["DB_NAME"]]
ATTRIB = {"SAFE_PAYMENT_RETRY", "SEND_RECOVERY_LINK", "CUSTOMER_REMINDER"}

bad, ok = [], []
for c in db.recovery_cases.find({"source": {"$in": ["RAZORPAY_TEST", "RAZORPAY_LIVE"]},
                                 "status": "VERIFIED_RECOVERED"}, {"_id": 0}):
    acts = list(db.recovery_actions.find({"case_id": c["case_id"], "executed_time": {"$ne": None}}, {"_id": 0}))
    real = [a for a in acts if a.get("action_type") in ATTRIB and not a.get("simulated")]
    sim = [a for a in acts if a.get("action_type") in ATTRIB and a.get("simulated")]
    row = {"case_id": c["case_id"], "order_key": c["order_key"], "recovered": c.get("recovered_amount"),
           "attributed_action": c.get("attributed_action"), "strength": c.get("attribution_strength"),
           "closed_at": c.get("closed_at"), "real_actions": len(real), "simulated_actions": len(sim)}
    (ok if real else bad).append(row)

print("VERIFIED_RECOVERED provider cases attributed WITHOUT any genuine (non-simulated) action:", len(bad))
print(json.dumps(bad, indent=1)[:4000])
print("sum recovered in violating cases:", round(sum(float(r["recovered"] or 0) for r in bad), 2))
print("cases with a genuine action:", len(ok))
print(json.dumps(ok[:5], indent=1))

"""One-off re-attribution migration (attribution-honesty mandate).

Provider-sourced cases (RAZORPAY_TEST / RAZORPAY_LIVE) that were closed as
VERIFIED_RECOVERED / PARTIALLY_RECOVERED under the OLD semantics — where a
SIMULATED action was allowed to earn attribution — are re-closed as
NATURALLY_RECOVERED with attribution NONE and recovered_amount 0, so Verified
Net Recovery never contains value attributable only to simulated actions.

Cases with at least one genuinely executed (non-simulated) attributable action
are left untouched. Every change is recorded as a CASE_REATTRIBUTED audit event.

Usage:
    python3 scripts/reattribute_simulated_closures.py            # dry-run
    python3 scripts/reattribute_simulated_closures.py --apply    # write changes
"""
import json
import os
import sys
from datetime import datetime, timezone

import pymongo
from dotenv import dotenv_values

sys.path.insert(0, "/app/backend")
from detection import ATTRIBUTABLE_ACTIONS

ATTRIBUTABLE = list(ATTRIBUTABLE_ACTIONS)

_be = dotenv_values("/app/backend/.env")
mdb = pymongo.MongoClient(_be["MONGO_URL"])[_be["DB_NAME"]]

APPLY = "--apply" in sys.argv
RECOVERED_STATUSES = ("VERIFIED_RECOVERED", "PARTIALLY_RECOVERED")

candidates = list(mdb.recovery_cases.find(
    {"source": {"$in": ["RAZORPAY_TEST", "RAZORPAY_LIVE"]}, "status": {"$in": list(RECOVERED_STATUSES)}},
    {"_id": 0},
))

results = {"examined": len(candidates), "kept_genuine": 0, "migrated": []}
for case in candidates:
    cid = case["case_id"]
    actions = list(mdb.recovery_actions.find(
        {"case_id": cid, "action_type": {"$in": ATTRIBUTABLE}, "executed_time": {"$ne": None}},
        {"_id": 0},
    ))
    genuine = [a for a in actions if not a.get("simulated")]
    if genuine:
        results["kept_genuine"] += 1
        continue
    before = {"status": case["status"], "attribution_strength": case.get("attribution_strength"),
              "recovered_amount": case.get("recovered_amount")}
    recovered = float(case.get("recovered_amount") or 0)
    results["migrated"].append({"case_id": cid, "order_key": case.get("order_key"), **before})
    if not APPLY:
        continue
    now = datetime.now(timezone.utc).isoformat()
    mdb.recovery_cases.update_one({"case_id": cid}, {"$set": {
        "status": "NATURALLY_RECOVERED", "outcome": "NATURALLY_RECOVERED",
        "verification_status": "VERIFIED", "recovered_amount": 0.0,
        "natural_recovered_amount": recovered,
        "attribution": "NONE", "attribution_strength": "NONE",
        "attributed_action": None,
        "updated_at": now,
    }})
    mdb.audit_events.insert_one({
        "audit_id": f"aud_{os.urandom(6).hex()}",
        "case_id": cid, "order_key": case.get("order_key"), "actor": "system",
        "event_type": "CASE_REATTRIBUTED", "created_at": now,
        "reason": (
            "Re-attribution migration (attribution-honesty mandate): this case was closed as "
            f"{before['status']} with attribution earned solely by SIMULATED action(s). Simulated executions "
            "never contact the customer and never earn attribution on provider-sourced cases — re-closed as "
            "NATURALLY_RECOVERED, attribution NONE, recovered_amount 0. Historical record preserved."
        ),
        "before_state": json.dumps(before),
        "after_state": json.dumps({"status": "NATURALLY_RECOVERED", "attribution_strength": "NONE", "recovered_amount": 0.0}),
    })

print(json.dumps(results, indent=2))
print("MODE:", "APPLY" if APPLY else "DRY-RUN (pass --apply to write)")

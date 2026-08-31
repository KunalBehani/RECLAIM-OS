"""Iteration-8 helper: report lab/provider case distribution and clean up the
lab cases created by this testing run (order_LAB* created within WINDOW mins).
Never touches secrets. Usage: python it8_check_cleanup.py [--delete] [minutes]
"""
import sys
from datetime import datetime, timedelta, timezone

import pymongo
from dotenv import dotenv_values

be = dotenv_values("/app/backend/.env")
db = pymongo.MongoClient(be["MONGO_URL"])[be["DB_NAME"]]

do_delete = "--delete" in sys.argv
mins = int([a for a in sys.argv[1:] if a.isdigit()][0]) if any(a.isdigit() for a in sys.argv[1:]) else 25
cutoff = (datetime.now(timezone.utc) - timedelta(minutes=mins)).isoformat()

print("--- provider-sourced case status distribution ---")
for row in db.recovery_cases.aggregate([
    {"$match": {"source": {"$in": ["RAZORPAY_TEST", "RAZORPAY_LIVE"]}}},
    {"$group": {"_id": {"s": "$status", "a": "$attribution_strength"}, "n": {"$sum": 1},
                "rec": {"$sum": "$recovered_amount"}}},
    {"$sort": {"n": -1}},
]):
    print(row)

print("\n--- non-provider VERIFIED_RECOVERED sanity (fix must not affect them) ---")
for row in db.recovery_cases.aggregate([
    {"$match": {"status": "VERIFIED_RECOVERED"}},
    {"$group": {"_id": "$source", "n": {"$sum": 1}, "rec": {"$sum": "$recovered_amount"}}},
]):
    print(row)

lab = list(db.recovery_cases.find({"order_key": {"$regex": "^order_LAB"}, "created_at": {"$gte": cutoff}},
                                  {"_id": 0, "case_id": 1, "order_key": 1, "status": 1,
                                   "attribution_strength": 1, "recovered_amount": 1}))
print(f"\n--- lab cases created in last {mins} min: {len(lab)} ---")
for c in lab:
    print(c)

if do_delete:
    ids = [c["case_id"] for c in lab]
    keys = [c["order_key"] for c in lab]
    print("deleting", len(ids), "lab cases and their trail")
    print("cases", db.recovery_cases.delete_many({"case_id": {"$in": ids}}).deleted_count)
    print("actions", db.recovery_actions.delete_many({"case_id": {"$in": ids}}).deleted_count)
    print("audit", db.audit_events.delete_many({"case_id": {"$in": ids}}).deleted_count)
    print("attempts", db.payment_attempts.delete_many({"order_id": {"$in": keys}}).deleted_count)
    print("orders", db.orders.delete_many({"order_id": {"$in": keys}}).deleted_count)
    print("provider_events", db.provider_events.delete_many({"normalized_order_id": {"$in": keys}}).deleted_count)

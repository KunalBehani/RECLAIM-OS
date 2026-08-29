"""Controlled server-side reproduction of the Razorpay 401.

Reads the credentials RECLAIM has stored, prints SAFE metadata only, then
calls Razorpay directly (bypassing all app code) to distinguish:
  A. Razorpay rejects the stored credentials themselves, vs
  B. RECLAIM constructs/sends the request incorrectly.
Never prints the secret or the Authorization header.
"""
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

import pymongo
import requests

client = pymongo.MongoClient(os.environ["MONGO_URL"])
db = client[os.environ["DB_NAME"]]
cfg = db.integrations.find_one({"provider": "razorpay"}, {"_id": 0})
if not cfg:
    print(json.dumps({"error": "no razorpay integration configured in DB"}))
    sys.exit(1)

kid_raw = cfg.get("key_id") or ""
sec_raw = cfg.get("key_secret") or ""
print(json.dumps({
    "stored_metadata": {
        "mode": cfg.get("mode"),
        "status": cfg.get("status"),
        "key_id_prefix": kid_raw.strip()[:9],
        "key_id_is_test": kid_raw.strip().startswith("rzp_test_"),
        "key_id_length_raw": len(kid_raw),
        "key_id_length_stripped": len(kid_raw.strip()),
        "key_secret_present": bool(sec_raw.strip()),
        "key_secret_length_raw": len(sec_raw),
        "key_secret_length_stripped": len(sec_raw.strip()),
        "webhook_secret_present": bool(cfg.get("webhook_secret")),
        "last_error": cfg.get("last_error"),
        "updated_at": cfg.get("updated_at"),
    }
}, indent=2))

kid = kid_raw.strip()
sec = sec_raw.strip()

# Direct call to Razorpay — raw requests, no app code in the path.
resp = requests.get(
    "https://api.razorpay.com/v1/orders?count=1",
    auth=(kid, sec),
    timeout=10,
)
out = {"direct_call_http_status": resp.status_code}
if resp.status_code == 200:
    out["verdict"] = "B_suspect: credentials are VALID outside the app — request construction issue"
    out["orders_count"] = resp.json().get("count")
else:
    try:
        err = resp.json().get("error", {})
        out["razorpay_error_code"] = err.get("code")
        out["razorpay_error_description"] = err.get("description")
    except ValueError:
        out["raw_body_head"] = resp.text[:200]
    out["verdict"] = "A: Razorpay itself rejected the stored credentials"
print(json.dumps(out, indent=2))

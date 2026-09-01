"""Independent (non-pytest) verification of Phase 2A LIVE-mode semantics.

Runs entirely against the PUBLIC preview URL (REACT_APP_BACKEND_URL) using the
owner session token. Mongo is used read-only for assertions plus cleanup of the
artifacts this script creates. Never touches the TEST razorpay doc or resend doc.
"""
import hashlib
import hmac
import json
import sys
import uuid
from datetime import datetime, timezone

import requests
from dotenv import dotenv_values
from pymongo import MongoClient

fe = dotenv_values("/app/frontend/.env")
be = dotenv_values("/app/backend/.env")
BASE = fe["REACT_APP_BACKEND_URL"].rstrip("/")
mdb = MongoClient(be["MONGO_URL"])[be["DB_NAME"]]
H = {"Authorization": "Bearer test_session_smoke_1787904424204"}

LIVE_KEY_ID = "rzp_live_TESTONLYqa1234"
LIVE_KEY_SECRET = "qa_live_dummy_secret_xyz"
LIVE_WH = "whsec_live_qa_secret_42"

results = []
created_orders = []


def check(name, cond, extra=""):
    results.append((name, bool(cond), str(extra)[:400]))
    print(("PASS " if cond else "FAIL ") + name + ((" | " + str(extra)[:300]) if extra else ""))


def sign(secret, raw):
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def pay(event, p, oid, amount, ts):
    e = {"id": p, "entity": "payment", "amount": amount, "currency": "INR",
         "order_id": oid, "method": "card", "created_at": ts}
    if event == "payment.failed":
        e.update({"status": "failed", "error_code": "BAD_REQUEST_ERROR", "error_description": "insufficient_funds"})
    else:
        e["status"] = "captured"
    return {"entity": "event", "account_id": "acc_qa_live", "event": event,
            "payload": {"payment": {"entity": e}}, "created_at": ts}


def post_live(payload, eid, secret=LIVE_WH, sig=None, path="/api/webhooks/razorpay/live"):
    raw = json.dumps(payload).encode()
    return requests.post(f"{BASE}{path}", data=raw, timeout=120,
                         headers={"Content-Type": "application/json",
                                  "X-Razorpay-Signature": sig or sign(secret, raw),
                                  "x-razorpay-event-id": eid})


def save_live():
    return requests.put(f"{BASE}/api/integrations/razorpay/live/config",
                        json={"key_id": LIVE_KEY_ID, "key_secret": LIVE_KEY_SECRET, "webhook_secret": LIVE_WH},
                        headers=H, timeout=60)


def activate():
    return requests.post(f"{BASE}/api/integrations/razorpay/live/activate",
                         json={"confirmation": "ACTIVATE LIVE"}, headers=H, timeout=60)


def test_diag():
    return requests.get(f"{BASE}/api/integrations/razorpay/diagnostics", headers=H, timeout=60)


# ---------- snapshot TEST doc (must remain byte-identical) ----------
test_doc_before = mdb.integrations.find_one({"provider": "razorpay", "mode": "TEST"}, {"_id": 0})
resend_before = mdb.integrations.find_one({"provider": "resend"}, {"_id": 0})

# ---------- 1. mode isolation ----------
r = save_live()
check("1a live config saved (200)", r.status_code == 200, r.text[:200])
st = requests.get(f"{BASE}/api/integrations/razorpay/live", headers=H, timeout=60).json()
check("1b live configured & not activated after save",
      st["configured"] is True and st["activated"] is False and st["live"]["mode"] == "LIVE", st)
td = test_diag().json()
check("1c TEST diagnostics untouched (TEST/rzp_test_)",
      td["mode"] == "TEST" and td["key_id_prefix"] == "rzp_test_", {k: td.get(k) for k in ("mode", "key_id_prefix", "status")})
ld = requests.get(f"{BASE}/api/integrations/razorpay/live/diagnostics", headers=H, timeout=60).json()
check("1d LIVE diagnostics mode/prefix", ld["mode"] == "LIVE" and ld["key_id_prefix"] == "rzp_live_", ld)
docs = list(mdb.integrations.find({"provider": "razorpay"}, {"_id": 0, "mode": 1, "key_id": 1}))
check("1e two isolated razorpay docs (TEST + LIVE)", len(docs) == 2 and {d["mode"] for d in docs} == {"TEST", "LIVE"}, docs)

# ---------- 2. credential rejection ----------
r = requests.put(f"{BASE}/api/integrations/razorpay/live/config",
                 json={"key_id": "rzp_test_ABC123", "key_secret": "x", "webhook_secret": "y"}, headers=H, timeout=60)
check("2a LIVE rejects rzp_test_ key (400)", r.status_code == 400, r.text[:200])
r = requests.put(f"{BASE}/api/integrations/razorpay/live/config",
                 json={"key_id": "nonprefixed_key", "key_secret": "x", "webhook_secret": "y"}, headers=H, timeout=60)
check("2b LIVE rejects non-prefixed key (400)", r.status_code == 400, r.text[:200])
r = requests.put(f"{BASE}/api/integrations/razorpay",
                 json={"key_id": LIVE_KEY_ID, "key_secret": "x", "webhook_secret": "y", "mode": "TEST"}, headers=H, timeout=60)
check("2c TEST endpoint rejects rzp_live_ key (400)", r.status_code == 400, r.text[:200])
td = test_diag().json()
check("2d TEST doc still real rzp_test_ after rejection", td["key_id_prefix"] == "rzp_test_", td.get("key_id_prefix"))

# ---------- 3. masking + anonymous access ----------
for url in ("/api/integrations/razorpay/live/diagnostics", "/api/integrations/razorpay/live"):
    r = requests.get(f"{BASE}{url}", headers=H, timeout=60)
    check(f"3a no secrets leaked in {url}",
          r.status_code == 200 and LIVE_KEY_SECRET not in r.text and LIVE_WH not in r.text, r.text[:200])
ld = requests.get(f"{BASE}/api/integrations/razorpay/live/diagnostics", headers=H, timeout=60).json()
check("3b diagnostics reports lengths/flags only",
      ld["key_secret_present"] is True and ld["key_secret_length"] == len(LIVE_KEY_SECRET)
      and ld["webhook_secret_present"] is True and ld["key_id_is_live"] is True, ld)
for url in ("/api/integrations/razorpay/live", "/api/integrations/razorpay/live/diagnostics"):
    r = requests.get(f"{BASE}{url}", timeout=60)
    check(f"3c anonymous GET {url} blocked", r.status_code in (401, 403), r.status_code)
for method, url in (("put", "/api/integrations/razorpay/live/config"), ("post", "/api/integrations/razorpay/live/activate"),
                    ("post", "/api/integrations/razorpay/live/test-connection"), ("delete", "/api/integrations/razorpay/live")):
    r = getattr(requests, method)(f"{BASE}{url}", json={}, timeout=60)
    check(f"3d anonymous {method.upper()} {url} blocked", r.status_code in (401, 403), r.status_code)

# ---------- 4. activation gate ----------
oid = f"order_lv{uuid.uuid4().hex[:6]}"
created_orders.append(oid)
now = int(datetime.now(timezone.utc).timestamp())
p = pay("payment.failed", f"pay_lv{uuid.uuid4().hex[:6]}", oid, 50000, now)
r = post_live(p, f"evt_lv{uuid.uuid4().hex[:6]}")
check("4a live webhook 403 before activation", r.status_code == 403, r.text[:200])
r = requests.post(f"{BASE}/api/integrations/razorpay/live/activate", json={"confirmation": "activate live"}, headers=H, timeout=60)
check("4b wrong activation phrase rejected (400)", r.status_code == 400, r.text[:200])
r = activate()
check("4c exact phrase activates (200)", r.status_code == 200, r.text[:200])
st = requests.get(f"{BASE}/api/integrations/razorpay/live", headers=H, timeout=60).json()
check("4d activated flag + actor", st["activated"] is True and bool(st["activated_by"]), st.get("activated_by"))
# credential re-save resets activation
save_live()
st = requests.get(f"{BASE}/api/integrations/razorpay/live", headers=H, timeout=60).json()
check("4e credential re-save RESETS activation", st["activated"] is False, st)
check("4f LIVE_MODE_ACTIVATED audit exists", mdb.audit_events.count_documents({"event_type": "LIVE_MODE_ACTIVATED"}) >= 1)
check("4g LIVE_CREDENTIALS_UPDATED audit exists", mdb.audit_events.count_documents({"event_type": "LIVE_CREDENTIALS_UPDATED"}) >= 1)
activate()

# ---------- 5. webhook security + idempotency ----------
suf = uuid.uuid4().hex[:6]
oid = f"order_lv{suf}"
created_orders.append(oid)
p = pay("payment.failed", f"pay_lv{suf}A", oid, 50000, int(datetime.now(timezone.utc).timestamp()))
sec_before = mdb.security_events.count_documents({"path": "/api/webhooks/razorpay/live"})
r = post_live(p, f"evt_lv{suf}0", sig="0" * 64)
check("5a invalid signature rejected (401)", r.status_code == 401, r.text[:200])
check("5b security_events entry logged",
      mdb.security_events.count_documents({"path": "/api/webhooks/razorpay/live"}) == sec_before + 1)
r = post_live(p, f"evt_lv{suf}1")
body = r.json() if r.status_code == 200 else r.text[:300]
check("5c genuine signature processed as LIVE",
      r.status_code == 200 and body.get("status") == "processed" and body.get("mode") == "LIVE", body)
ev = mdb.provider_events.find_one({"provider_event_id": f"evt_lv{suf}1"}, {"_id": 0})
check("5d provider_event mode/source/signature_verified",
      ev and ev.get("mode") == "LIVE" and ev.get("source") == "LIVE" and ev.get("signature_verified") is True,
      {k: (ev or {}).get(k) for k in ("mode", "source", "signature_verified")})
r2 = post_live(p, f"evt_lv{suf}1")
check("5e exact replay -> duplicate:true", r2.json().get("duplicate") is True, r2.text[:200])
ev = mdb.provider_events.find_one({"provider_event_id": f"evt_lv{suf}1"}, {"_id": 0})
check("5f duplicate_deliveries incremented to 1", ev.get("duplicate_deliveries") == 1, ev.get("duplicate_deliveries"))
case = mdb.recovery_cases.find_one({"order_key": oid}, {"_id": 0})
check("5g case source RAZORPAY_LIVE", case and case.get("source") == "RAZORPAY_LIVE", (case or {}).get("source"))
check("5h LIVE_EVENT_PROCESSED audit exists", mdb.audit_events.count_documents({"event_type": "LIVE_EVENT_PROCESSED"}) >= 1)

# ---------- 6. cross-contamination ----------
real_test_secret = test_doc_before.get("webhook_secret")
raw = json.dumps(p).encode()
r = requests.post(f"{BASE}/api/webhooks/razorpay/live", data=raw, timeout=60,
                  headers={"Content-Type": "application/json",
                           "X-Razorpay-Signature": sign(real_test_secret, raw),
                           "x-razorpay-event-id": f"evt_lv{suf}x"})
check("6a TEST-secret signature rejected on LIVE endpoint (401)", r.status_code == 401, r.status_code)
r = requests.post(f"{BASE}/api/webhooks/razorpay", data=raw, timeout=60,
                  headers={"Content-Type": "application/json",
                           "X-Razorpay-Signature": sign(LIVE_WH, raw),
                           "x-razorpay-event-id": f"evt_lv{suf}y"})
check("6b LIVE-secret signature rejected on TEST endpoint (401)", r.status_code == 401, r.status_code)

# ---------- 7. LIVE action gates ----------
cid = case["case_id"] if case else None
if cid:
    requests.put(f"{BASE}/api/settings", json={"emergency_stop": True}, headers=H, timeout=60)
    r = requests.post(f"{BASE}/api/cases/{cid}/execute", json={"action_type": "SEND_RECOVERY_LINK"}, headers=H, timeout=120)
    b = r.json()
    check("7a emergency_stop blocks execution", b.get("executed") is False, b)
    check("7b no executed action during emergency stop",
          mdb.recovery_actions.count_documents({"case_id": cid, "executed_time": {"$ne": None}}) == 0)
    requests.put(f"{BASE}/api/settings", json={"emergency_stop": False}, headers=H, timeout=60)

    r = requests.post(f"{BASE}/api/cases/{cid}/execute", json={"action_type": "SEND_RECOVERY_LINK"}, headers=H, timeout=120)
    b = r.json()
    check("7c LIVE_ACTIONS_DISABLED block by default", b.get("blocked") == "LIVE_ACTIONS_DISABLED", b)
    check("7d LIVE_ACTION_BLOCKED audit for case",
          mdb.audit_events.count_documents({"case_id": cid, "event_type": "LIVE_ACTION_BLOCKED"}) >= 1)
    check("7e still no executed action",
          mdb.recovery_actions.count_documents({"case_id": cid, "executed_time": {"$ne": None}}) == 0)

    requests.put(f"{BASE}/api/settings", json={"live_actions_enabled": True}, headers=H, timeout=60)
    r = requests.post(f"{BASE}/api/cases/{cid}/execute", json={"action_type": "SEND_RECOVERY_LINK"}, headers=H, timeout=120)
    b = r.json()
    check("7f execution allowed after live_actions_enabled=true", b.get("executed") is True, b)
    act = mdb.recovery_actions.find_one({"case_id": cid, "executed_time": {"$ne": None}}, {"_id": 0})
    check("7g executed action is SIMULATED", act and act.get("simulated") is True,
          {k: (act or {}).get(k) for k in ("simulated", "outcome", "channel")})
    check("7h simulated LIVE action earns no attribution",
          not (act or {}).get("attributed_recovery", False), (act or {}).get("attributed_recovery"))
    requests.put(f"{BASE}/api/settings", json={"emergency_stop": False, "live_actions_enabled": False}, headers=H, timeout=60)
    s = requests.get(f"{BASE}/api/settings", headers=H, timeout=60).json().get("settings", {})
    check("7i settings reset (emergency_stop=false, live_actions_enabled=false)",
          s.get("emergency_stop") is False and not s.get("live_actions_enabled"),
          {k: s.get(k) for k in ("emergency_stop", "live_actions_enabled")})

# ---------- 8. honest connection test ----------
r = requests.post(f"{BASE}/api/integrations/razorpay/live/test-connection", headers=H, timeout=120)
b = r.json()
check("8a live test-connection returns honest ERROR with real 401",
      b.get("status") == "ERROR" and "401" in str(b.get("detail")), b)
ld = requests.get(f"{BASE}/api/integrations/razorpay/live/diagnostics", headers=H, timeout=60).json()
check("8b diagnostics status ERROR", ld.get("status") == "ERROR", ld.get("status"))
check("8c LIVE_CONNECTION_TEST_FAILED audit", mdb.audit_events.count_documents({"event_type": "LIVE_CONNECTION_TEST_FAILED"}) >= 1)

# ---------- 9. TEST-side preservation ----------
td = test_diag().json()
check("9a TEST integration still CONNECTED/rzp_test_",
      td["mode"] == "TEST" and td["key_id_prefix"] == "rzp_test_", {k: td.get(k) for k in ("mode", "key_id_prefix", "status")})
test_doc_after = mdb.integrations.find_one({"provider": "razorpay", "mode": "TEST"}, {"_id": 0})
diffs = {k: (test_doc_before.get(k), test_doc_after.get(k)) for k in set(test_doc_before) | set(test_doc_after)
         if test_doc_before.get(k) != test_doc_after.get(k) and k not in ("last_successful_event_at", "last_error_at", "updated_at")}
check("9b TEST doc credentials unchanged",
      test_doc_before.get("key_id") == test_doc_after.get("key_id")
      and test_doc_before.get("key_secret") == test_doc_after.get("key_secret")
      and test_doc_before.get("webhook_secret") == test_doc_after.get("webhook_secret"), diffs)
resend_after = mdb.integrations.find_one({"provider": "resend"}, {"_id": 0})
check("9c resend doc still enabled", resend_after.get("enabled") is True, resend_after)

# ---------- 10. deletion ----------
r = requests.delete(f"{BASE}/api/integrations/razorpay/live", headers=H, timeout=60)
check("10a DELETE live config (200)", r.status_code == 200, r.text[:200])
st = requests.get(f"{BASE}/api/integrations/razorpay/live", headers=H, timeout=60).json()
check("10b live not configured / not activated", st["configured"] is False and st["activated"] is False, st)
check("10c LIVE_CREDENTIALS_REMOVED audit", mdb.audit_events.count_documents({"event_type": "LIVE_CREDENTIALS_REMOVED"}) >= 1)
r = post_live(pay("payment.failed", "pay_lvZ", "order_lvZ", 1000, int(datetime.now(timezone.utc).timestamp())), "evt_lvZ")
check("10d live webhook 503 after deletion", r.status_code == 503, r.status_code)

# ---------- cleanup ----------
for o in set(created_orders + ["order_lvZ"]):
    c = mdb.recovery_cases.find_one({"order_key": o}, {"_id": 0, "case_id": 1})
    mdb.recovery_cases.delete_many({"order_key": o})
    mdb.payment_attempts.delete_many({"order_id": o})
    mdb.provider_events.delete_many({"normalized_order_id": o})
    if c:
        mdb.recovery_actions.delete_many({"case_id": c["case_id"]})
        mdb.audit_events.delete_many({"case_id": c["case_id"]})
mdb.provider_events.delete_many({"provider_event_id": {"$regex": "^evt_lv"}})
mdb.audit_events.delete_many({"event_type": {"$regex": "^LIVE_"}})
mdb.security_events.delete_many({"path": "/api/webhooks/razorpay/live"})
print("\ncleanup: leftover order_lv cases =", mdb.recovery_cases.count_documents({"order_key": {"$regex": "^order_lv"}}),
      "| LIVE_ audits =", mdb.audit_events.count_documents({"event_type": {"$regex": "^LIVE_"}}),
      "| live doc =", mdb.integrations.count_documents({"provider": "razorpay", "mode": "LIVE"}))

failed = [n for n, ok, _ in results if not ok]
print(f"\n===== {len(results) - len(failed)}/{len(results)} passed =====")
if failed:
    print("FAILED:", failed)
sys.exit(1 if failed else 0)

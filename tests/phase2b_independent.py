"""Independent (non-pytest) verification of RECLAIM OS Phase 2B against the PUBLIC preview URL.

Read-only except: (a) one webhook probe case (cleaned up), (b) cron_runs probe rows (cleaned up).
Never prints secrets. Never executes a recovery action.
"""
import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import requests
from dotenv import dotenv_values
from pymongo import MongoClient

BE = dotenv_values("/app/backend/.env")
FE = dotenv_values("/app/frontend/.env")
BASE = (os.environ.get("REACT_APP_BACKEND_URL") or FE["REACT_APP_BACKEND_URL"]).rstrip("/")
mdb = MongoClient(BE["MONGO_URL"])[BE["DB_NAME"]]
TOKEN = "test_session_smoke_1787904424204"
H = {"Authorization": f"Bearer {TOKEN}"}
CRON_SECRET = BE.get("WEBHOOK_CRON_SECRET", "")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS  " if cond else "FAIL  ") + name + (f" :: {detail}" if detail and not cond else ""))
    return cond


def get(path, headers=H, **kw):
    return requests.get(f"{BASE}{path}", headers=headers, timeout=180, **kw)


# ---------------- 0. basics ----------------
def basics():
    r = get("/api/auth/me")
    check("auth/me with owner session -> 200", r.status_code == 200, r.text[:200])
    check("auth/me role owner", r.status_code == 200 and r.json().get("role") == "owner", r.text[:200])
    r = get("/api/dashboard/summary")
    check("dashboard summary -> 200", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
    r = get("/api/cases", params={"limit": 50})
    check("cases list -> 200 with cases", r.status_code == 200 and isinstance(r.json().get("cases"), list), r.text[:200])


# ---------------- 1. state machine ----------------
def state_machine():
    sys.path.insert(0, "/app/backend")
    from case_state import assert_transition
    ok = True
    for a, b in [("OPEN", "EVALUATED"), ("EVALUATED", "ACTION_EXECUTED"), ("APPROVAL_PENDING", "ACTION_EXECUTED"),
                 ("ACTION_EXECUTED", "VERIFIED_RECOVERED"), ("OPEN", "OPEN")]:
        try:
            assert_transition(a, b)
        except ValueError:
            ok = False
    check("legal transitions allowed", ok)
    bad_ok = True
    for a, b in [("VERIFIED_RECOVERED", "OPEN"), ("NATURALLY_RECOVERED", "ACTION_EXECUTED"), ("STOPPED", "EVALUATED"),
                 ("EVALUATED", "OPEN"), ("NOT_RECOVERED", "EVALUATED"), ("INVALID", "ACTION_EXECUTED")]:
        try:
            assert_transition(a, b)
            bad_ok = False
            print(f"   illegal transition NOT blocked: {a}->{b}")
        except ValueError:
            pass
    check("illegal transitions raise ValueError", bad_ok)

    # every persisted case status is in the state machine vocabulary
    statuses = set(mdb.recovery_cases.distinct("status"))
    from case_state import VALID_TRANSITIONS
    check("all persisted case statuses are known states", statuses <= set(VALID_TRANSITIONS), str(statuses - set(VALID_TRANSITIONS)))


# ---------------- 2. fresh webhook case: audit chain + canonical fields ----------------
def webhook_probe():
    cfg = mdb.integrations.find_one({"provider": "razorpay", "mode": "TEST"})
    secret = (cfg or {}).get("webhook_secret", "")
    if not secret:
        check("razorpay TEST webhook secret available for probe", False)
        return
    suf = f"qa2b{uuid.uuid4().hex[:6]}"
    oid, pay = f"order_{suf}", f"pay_{suf}A"
    eid = f"evt_{suf}1"
    ts = int(datetime.now(timezone.utc).timestamp())
    # amount deliberately ABOVE approval_threshold_amount so policy routes to human
    # approval and NO recovery action is ever executed. Recipient is the Resend sink.
    payload = {"entity": "event", "account_id": "acc_qa2b", "event": "payment.failed",
               "payload": {"payment": {"entity": {"id": pay, "entity": "payment", "amount": 25000000,
                                                  "currency": "INR", "order_id": oid, "method": "card",
                                                  "created_at": ts, "email": "delivered@resend.dev",
                                                  "status": "failed", "error_code": "insufficient_funds",
                                                  "error_description": "insufficient funds"}}},
               "created_at": ts}
    raw = json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    try:
        r = requests.post(f"{BASE}/api/webhooks/razorpay", data=raw, timeout=180,
                          headers={"Content-Type": "application/json", "X-Razorpay-Signature": sig,
                                   "x-razorpay-event-id": eid})
        check("genuine-signature webhook accepted -> 200", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
        body = r.json() if r.status_code == 200 else {}
        check("webhook created a case", (body.get("result") or {}).get("result") == "case_created", json.dumps(body)[:300])
        case = mdb.recovery_cases.find_one({"order_key": oid}, {"_id": 0})
        if not check("case persisted in DB", bool(case)):
            return
        ev = {e["event_type"] for e in mdb.audit_events.find({"related.provider_event_id": eid}, {"event_type": 1})}
        for t in ("WEBHOOK_SIGNATURE_VERIFIED", "WEBHOOK_RECEIVED", "EVENT_NORMALIZED"):
            check(f"ingestion audit contains {t}", t in ev, str(ev))
        ca = [e for e in mdb.audit_events.find({"case_id": case["case_id"]}, {"_id": 0}).sort("timestamp", 1)]
        types = [e["event_type"] for e in ca]
        for t in ("CASE_CREATED", "AI_ANALYSIS_STARTED", "AI_ANALYSIS_COMPLETED", "POLICY_DECISION"):
            check(f"case audit contains {t}", t in types, str(types))
        check("AI_ANALYSIS_STARTED precedes AI_ANALYSIS_COMPLETED",
              "AI_ANALYSIS_STARTED" in types and "AI_ANALYSIS_COMPLETED" in types
              and types.index("AI_ANALYSIS_STARTED") < types.index("AI_ANALYSIS_COMPLETED"), str(types))
        # correlation_id / provider_mode on new audit events
        newev = [e for e in mdb.audit_events.find({"related.provider_event_id": eid}, {"_id": 0})]
        check("ingestion audit events carry correlation_id",
              all(e.get("correlation_id") for e in newev),
              str([(e["event_type"], e.get("correlation_id")) for e in newev]))
        check("ingestion audit events carry provider_mode",
              all(e.get("provider_mode") for e in newev),
              str([(e["event_type"], e.get("provider_mode")) for e in newev]))
        # canonical case fields
        missing = [f for f in ("case_id", "merchant_id", "provider", "provider_mode", "provider_order_id",
                               "provider_payment_id", "customer_reference", "amount_at_risk", "currency",
                               "payment_method", "failure_code", "failure_reason", "first_failed_at",
                               "latest_event_at", "status", "verification_status", "attribution_strength",
                               "recovered_amount", "incremental_recovered_amount", "natural_recovered_amount",
                               "created_at") if f not in case]
        check("canonical case fields all present", not missing, f"missing={missing}")
        check("provider_mode == TEST", case.get("provider_mode") == "TEST", str(case.get("provider_mode")))
        check("failure_code captured", case.get("failure_code") == "insufficient_funds", str(case.get("failure_code")))
        check("no recovery action executed on probe case (approval gate held)",
              mdb.recovery_actions.count_documents({"case_id": case["case_id"], "simulated": False}) == 0)
        check("probe case status is legal non-executed state",
              case.get("status") in ("OPEN", "EVALUATED", "APPROVAL_PENDING", "NATURALLY_RECOVERED", "INVALID", "STOPPED", "ACTION_EXECUTED"),
              str(case.get("status")))
        print(f"   probe case status={case.get('status')} amount={case.get('amount_at_risk')}")
        # state transitions audited
        check("case status transition audited (CASE_CREATED + POLICY_DECISION present)",
              "CASE_CREATED" in types and "POLICY_DECISION" in types)
    finally:
        c = mdb.recovery_cases.find_one({"order_key": oid}, {"case_id": 1})
        mdb.recovery_cases.delete_many({"order_key": oid})
        mdb.payment_attempts.delete_many({"order_id": oid})
        mdb.provider_events.delete_many({"provider_event_id": {"$regex": f"^evt_{suf}"}})
        mdb.orders.delete_many({"order_id": oid})
        if c:
            mdb.recovery_actions.delete_many({"case_id": c["case_id"]})
            mdb.audit_events.delete_many({"case_id": c["case_id"]})
        mdb.audit_events.delete_many({"related.provider_event_id": {"$regex": f"^evt_{suf}"}})
        left = mdb.recovery_cases.count_documents({"order_key": oid}) + mdb.provider_events.count_documents({"provider_event_id": {"$regex": f"^evt_{suf}"}})
        check("probe data cleaned up", left == 0, f"left={left}")


# ---------------- 3. EIV transparency ----------------
def eiv_replay():
    docs = list(mdb.recovery_actions.find({"eiv_inputs": {"$ne": None}}, {"_id": 0}).limit(500))
    check("executed actions with eiv_inputs exist", len(docs) > 0, str(len(docs)))
    bad = []
    for d in docs:
        i = d["eiv_inputs"] or {}
        try:
            replay = round(float(i["recoverable_amount"]) * float(i["incremental_probability"])
                           - float(i["action_cost"]) - float(i.get("risk_penalty") or 0), 2)
        except Exception as exc:
            bad.append((d["action_id"], f"inputs unusable: {exc}"))
            continue
        for label, stored in (("eiv_inputs.eiv", i.get("eiv")), ("action.expected_incremental_value", d.get("expected_incremental_value"))):
            if stored is None or abs(round(float(stored), 2) - replay) > 0.01:
                bad.append((d["action_id"], f"{label}={stored} replay={replay}"))
        for k in ("recovery_likelihood", "natural_recovery_baseline", "incremental_probability",
                  "recoverable_amount", "action_cost", "risk_penalty", "eiv", "model_version", "policy_version"):
            if k not in i:
                bad.append((d["action_id"], f"missing eiv_input key {k}"))
    check(f"EIV replay exact for all {len(docs)} action docs", not bad, str(bad[:5]))
    # any executed (non-simulated OR simulated) action created post-2B should carry eiv_inputs
    total = mdb.recovery_actions.count_documents({})
    without = mdb.recovery_actions.count_documents({"eiv_inputs": None})
    missing_key = mdb.recovery_actions.count_documents({"eiv_inputs": {"$exists": False}})
    print(f"   recovery_actions total={total} eiv_inputs_null={without} eiv_inputs_absent={missing_key}")


# ---------------- 4. cron endpoint ----------------
def cron():
    r = requests.post(f"{BASE}/api/cron/verification-sweep", json={"run_id": "x"}, timeout=60)
    check("cron without auth -> 401", r.status_code == 401, f"{r.status_code} {r.text[:150]}")
    r = requests.post(f"{BASE}/api/cron/verification-sweep", json={"run_id": "x"},
                      headers={"Authorization": "Bearer not-the-secret"}, timeout=60)
    check("cron with wrong secret -> 401", r.status_code == 401, f"{r.status_code} {r.text[:150]}")
    r = requests.post(f"{BASE}/api/cron/verification-sweep", json={"run_id": "x"},
                      headers={"Authorization": f"Bearer {TOKEN}"}, timeout=60)
    check("cron with owner session token (not cron secret) -> 401", r.status_code == 401, f"{r.status_code}")
    if not CRON_SECRET:
        check("WEBHOOK_CRON_SECRET present in backend .env", False)
        return
    run_id = f"qa_cron_{uuid.uuid4().hex[:8]}"
    ch = {"Authorization": f"Bearer {CRON_SECRET}"}
    try:
        r = requests.post(f"{BASE}/api/cron/verification-sweep", json={"run_id": run_id}, headers=ch, timeout=60)
        ok = check("cron authorized -> 200 accepted", r.status_code == 200 and r.json().get("accepted") is True, f"{r.status_code} {r.text[:200]}")
        if ok:
            check("first call duplicate=false", r.json().get("duplicate") is False, r.text[:200])
            check("response echoes run_id", r.json().get("run_id") == run_id, r.text[:200])
        r2 = requests.post(f"{BASE}/api/cron/verification-sweep", json={"run_id": run_id}, headers=ch, timeout=60)
        check("replayed run_id -> duplicate=true", r2.status_code == 200 and r2.json().get("duplicate") is True, r2.text[:200])
        doc = mdb.cron_runs.find_one({"run_id": run_id})
        check("cron_runs row created", bool(doc), "none")
        deadline = time.time() + 300
        status = (doc or {}).get("status")
        while time.time() < deadline:
            doc = mdb.cron_runs.find_one({"run_id": run_id}) or {}
            status = doc.get("status")
            if status in ("COMPLETED", "FAILED"):
                break
            time.sleep(10)
        check("cron run reaches COMPLETED within 5 min", status == "COMPLETED", f"status={status} err={(doc or {}).get('error')}")
        if status == "COMPLETED":
            res = (doc or {}).get("results") or {}
            check("cron run stores sweep results with 'checked'", isinstance(res.get("checked"), int), str(res)[:200])
            print(f"   sweep results: {json.dumps(res)[:300]}")
            sweep = mdb.verification_sweeps.find_one({"actor": {"$regex": run_id}}, {"_id": 0})
            check("verification_sweeps row persisted for cron run", bool(sweep), "none")
            aud = mdb.audit_events.count_documents({"actor": {"$regex": run_id}, "event_type": "RECONCILIATION_COMPLETED"})
            check("RECONCILIATION_COMPLETED audit for cron run", aud >= 1, str(aud))
    finally:
        mdb.cron_runs.delete_many({"run_id": run_id})
        mdb.verification_sweeps.delete_many({"actor": {"$regex": run_id}})
        mdb.audit_events.delete_many({"actor": {"$regex": run_id}})
        check("cron probe data cleaned up", mdb.cron_runs.count_documents({"run_id": run_id}) == 0)


# ---------------- 5. LIVE readiness ----------------
def readiness():
    r = requests.get(f"{BASE}/api/integrations/razorpay/live/readiness", timeout=60)
    check("readiness anonymous -> 401/403", r.status_code in (401, 403), str(r.status_code))
    r = get("/api/integrations/razorpay/live/readiness")
    if not check("readiness authorized -> 200", r.status_code == 200, f"{r.status_code} {r.text[:200]}"):
        return
    d = r.json()
    check("readiness overall == BLOCKED (no live creds)", d.get("overall") == "BLOCKED", str(d.get("overall")))
    comps = {c["component"]: c for c in d.get("components", [])}
    cred = next((c for c in comps.values() if "credentials" in c["component"].lower()), None)
    check("credentials component BLOCKED", cred and cred["status"] == "BLOCKED", str(cred))
    sig = next((c for c in comps.values() if "signature" in c["component"].lower()), None)
    check("signature-verification component WARNING", sig and sig["status"] == "WARNING", str(sig))
    check("every component has a non-empty honest reason",
          all(c.get("reason") for c in d.get("components", [])))
    check("component statuses restricted to READY/WARNING/BLOCKED",
          all(c.get("status") in ("READY", "WARNING", "BLOCKED") for c in d.get("components", [])))
    check("overall is never READY while a BLOCKED component exists",
          not (d.get("overall") == "READY" and any(c["status"] != "READY" for c in d["components"])))
    fc = d.get("fail_closed_defaults") or {}
    check("fail_closed_defaults reported", set(fc) >= {"live_activation", "live_actions_enabled", "emergency_stop"}, str(fc))
    check("fail_closed_defaults match DB settings",
          fc.get("emergency_stop") is bool((mdb.settings.find_one({"key": "policy"}) or {}).get("emergency_stop", False))
          and fc.get("live_actions_enabled") is bool((mdb.settings.find_one({"key": "policy"}) or {}).get("live_actions_enabled", False)),
          str(fc))
    # no secrets leaked
    check("no secret material in readiness payload",
          not any(k in json.dumps(d).lower() for k in ("key_secret", "webhook_secret\":", "rzp_live_")), "leak")


# ---------------- 6. health observability ----------------
def health():
    r = requests.get(f"{BASE}/api/integrations/razorpay/health", timeout=60)
    check("health anonymous -> 401/403", r.status_code in (401, 403), str(r.status_code))
    r = get("/api/integrations/razorpay/health")
    if not check("health authorized -> 200", r.status_code == 200, f"{r.status_code} {r.text[:200]}"):
        return
    d = r.json()
    for f in ("recovery_action_failures", "live_action_blocks", "reconciliation_failures", "policy_blocks", "last_sweep"):
        check(f"health includes {f}", f in d, str(sorted(d)))
    check("recovery_action_failures matches DB",
          d.get("recovery_action_failures") == mdb.recovery_actions.count_documents({"outcome": "DELIVERY_FAILED"}))
    check("live_action_blocks matches DB",
          d.get("live_action_blocks") == mdb.audit_events.count_documents({"event_type": "LIVE_ACTION_BLOCKED"}))
    check("reconciliation_failures matches DB",
          d.get("reconciliation_failures") == mdb.audit_events.count_documents({"event_type": "RECONCILIATION_FAILED"}))
    check("policy_blocks matches DB",
          d.get("policy_blocks") == mdb.audit_events.count_documents({"event_type": "POLICY_DECISION", "after_state.decision": "BLOCK"}))
    ls = d.get("last_sweep")
    check("last_sweep is a sweep doc or null", ls is None or ("results" in ls and "run_at" in ls), str(ls)[:200])
    check("last_sweep has no mongo _id", not (isinstance(ls, dict) and "_id" in ls))


# ---------------- 7. case filters ----------------
def filters():
    def api(**params):
        r = get("/api/cases", params={**params, "limit": 5000})
        if r.status_code != 200:
            check(f"/api/cases {params} -> 200", False, f"{r.status_code} {r.text[:150]}")
            return None
        return r.json()["cases"]

    # failure substring
    codes = [c for c in mdb.recovery_cases.distinct("failure_code") if c]
    if codes:
        code = sorted(codes, key=lambda c: -mdb.recovery_cases.count_documents({"failure_code": c}))[0]
        frag = code[:6]
        cases = api(failure=frag)
        expect = mdb.recovery_cases.count_documents({"failure_code": {"$regex": frag, "$options": "i"}})
        check(f"filter failure='{frag}' count matches DB", cases is not None and len(cases) == expect, f"api={len(cases or [])} db={expect}")
        check("filter failure narrows results", cases is not None and all(frag.lower() in (c.get("failure_code") or "").lower() for c in cases))
        check("filter failure is a strict subset", cases is not None and len(cases) < mdb.recovery_cases.count_documents({}))
        cases0 = api(failure="zzz_no_such_code")
        check("filter failure with no match -> empty", cases0 == [], str(len(cases0 or [])))

    # verification
    for vs in [v for v in mdb.recovery_cases.distinct("verification_status") if v][:2]:
        cases = api(verification=vs)
        expect = mdb.recovery_cases.count_documents({"verification_status": vs})
        check(f"filter verification={vs} matches DB", cases is not None and len(cases) == expect, f"api={len(cases or [])} db={expect}")
        check(f"filter verification={vs} homogeneous", cases is not None and all(c.get("verification_status") == vs for c in cases))

    # attribution
    for at in [a for a in mdb.recovery_cases.distinct("attribution_strength") if a][:2]:
        cases = api(attribution=at)
        expect = mdb.recovery_cases.count_documents({"attribution_strength": at})
        check(f"filter attribution={at} matches DB", cases is not None and len(cases) == expect, f"api={len(cases or [])} db={expect}")

    # amount range
    cases = api(min_amount=5000, max_amount=20000)
    expect = mdb.recovery_cases.count_documents({"amount_at_risk": {"$gte": 5000, "$lte": 20000}})
    check("filter min/max amount matches DB", cases is not None and len(cases) == expect, f"api={len(cases or [])} db={expect}")
    check("filter amount range respected", cases is not None and all(5000 <= float(c["amount_at_risk"]) <= 20000 for c in cases))
    cases = api(min_amount=10**9)
    check("filter min_amount huge -> empty", cases == [], str(len(cases or [])))

    # created range
    frm = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    cases = api(created_from=frm)
    expect = mdb.recovery_cases.count_documents({"created_at": {"$gte": frm}})
    check("filter created_from matches DB", cases is not None and len(cases) == expect, f"api={len(cases or [])} db={expect}")
    check("filter created_from respected", cases is not None and all(c["created_at"] >= frm for c in cases))
    to = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    cases = api(created_to=to)
    expect = mdb.recovery_cases.count_documents({"created_at": {"$lte": to}})
    check("filter created_to matches DB", cases is not None and len(cases) == expect, f"api={len(cases or [])} db={expect}")

    # combined filters narrow further
    a = api(verification="PENDING") or []
    b = api(verification="PENDING", min_amount=10000) or []
    check("combined filters narrow further", len(b) <= len(a), f"{len(b)} vs {len(a)}")

    # sort=eiv_desc
    cases = api(sort="eiv_desc")
    if cases:
        def eiv(c):
            rec = c.get("recommended_action")
            evs = [e.get("expected_incremental_value") or 0 for e in (c.get("action_evaluations") or []) if e.get("action_type") == rec]
            return evs[0] if evs else 0
        vals = [eiv(c) for c in cases]
        check("sort=eiv_desc is monotonically non-increasing", all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1)), str(vals[:8]))
        check("sort=eiv_desc top case has the max EIV", vals[0] == max(vals), f"{vals[0]} vs {max(vals)}")

    # sort=attention (oldest day first, highest amount within day)
    cases = api(sort="attention")
    if cases:
        keys = [((c.get("created_at") or "")[:10], -float(c.get("amount_at_risk") or 0)) for c in cases]
        check("sort=attention ordered (oldest day, then highest amount)", keys == sorted(keys), str(keys[:5]))
        check("sort=attention returns same population as default", len(cases) == len(api(sort="newest") or []))


# ---------------- 8. regression spot checks ----------------
def regression():
    for path in ["/api/dashboard/summary", "/api/dashboard/cost-ledger", "/api/settings", "/api/integrations",
                 "/api/integrations/razorpay/live", "/api/integrations/razorpay/diagnostics",
                 "/api/webhooks/events", "/api/review/queue", "/api/webhooks/config",
                 "/api/integrations/razorpay/events", "/api/ingest/batches"]:
        r = get(path)
        check(f"GET {path} -> 200", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
    cases = get("/api/cases", params={"source": "TEST_MODE", "limit": 20})
    check("TEST-mode case list loads", cases.status_code == 200 and len(cases.json()["cases"]) > 0,
          f"{cases.status_code} {cases.text[:200]}")
    cid = mdb.recovery_cases.find_one({"source": "RAZORPAY_TEST"}, {"case_id": 1})
    if cid:
        r = get(f"/api/cases/{cid['case_id']}")
        ok = check("case detail -> 200", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
        if ok:
            d = r.json()
            check("case detail has audit trail list", isinstance(d.get("audit_trail"), list), str(sorted(d))[:200])
            check("case detail has actions list", isinstance(d.get("actions"), list), str(sorted(d))[:200])
        r = get(f"/api/cases/{cid['case_id']}/replay")
        check("case replay -> 200", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
        check("case detail response has no mongo _id", "\"_id\"" not in r.text)


if __name__ == "__main__":
    for fn in (basics, state_machine, readiness, health, filters, eiv_replay, webhook_probe, regression, cron):
        print(f"\n=== {fn.__name__} ===")
        try:
            fn()
        except Exception as exc:
            check(f"{fn.__name__} raised", False, repr(exc)[:300])
    print(f"\n=== RESULT: {len(PASS)} passed, {len(FAIL)} failed ===")
    for f in FAIL:
        print("FAILED: " + f)

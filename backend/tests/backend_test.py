"""API-level tests for RECLAIM OS (auth, dashboard, cases, ingest, review, webhooks, simulator)."""
import json
import os

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE = base_url.rstrip("/")
TOKEN = "test_session_smoke_1787904424204"
CSV_PATH = "/app/test_data/sample_payments.csv"


@pytest.fixture(scope="session")
def client():
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {TOKEN}"})
    return s


@pytest.fixture(scope="session")
def anon():
    return requests.Session()


# ---- auth gating ----

def test_auth_me(client):
    r = client.get(f"{BASE}/api/auth/me", timeout=30)
    assert r.status_code == 200
    assert r.json()["email"] == "smoke.tester@example.com"


@pytest.mark.parametrize("path", ["/api/cases", "/api/dashboard/summary", "/api/review/queue", "/api/settings", "/api/ingest/batches"])
def test_protected_endpoints_require_auth(anon, path):
    r = anon.get(f"{BASE}{path}", timeout=30)
    assert r.status_code in (401, 403), f"{path} returned {r.status_code}"


def test_bad_token_rejected(anon):
    r = anon.get(f"{BASE}/api/auth/me", headers={"Authorization": "Bearer nope"}, timeout=30)
    assert r.status_code in (401, 403)


# ---- dashboard ----

def test_dashboard_summary(client):
    r = client.get(f"{BASE}/api/dashboard/summary", timeout=60)
    assert r.status_code == 200
    d = r.json()
    body = json.dumps(d)
    assert '"_id"' not in body
    for key in ("kpis", "funnel"):
        assert key in d, f"missing {key} in dashboard summary: {list(d.keys())}"


# ---- webhook security ----

def test_webhook_bad_signature_401(anon):
    payload = {"event_id": "evt_badsig_test", "type": "payment.failed", "timestamp": "2026-07-01T00:00:00Z",
               "data": {"payment_id": "pay_x", "order_id": "ORD-BADSIG", "amount": 100, "currency": "INR", "status": "failed"}}
    r = anon.post(f"{BASE}/api/webhooks/payments", json=payload,
                  headers={"X-Reclaim-Signature": "sha256=" + "0" * 64}, timeout=30)
    assert r.status_code == 401


def test_webhook_missing_signature_rejected(anon):
    r = anon.post(f"{BASE}/api/webhooks/payments", json={"event_id": "evt_nosig"}, timeout=30)
    assert r.status_code in (400, 401)


def test_webhook_config_and_events(client):
    cfg = client.get(f"{BASE}/api/webhooks/config", timeout=30)
    assert cfg.status_code == 200
    assert "secret" not in json.dumps(cfg.json()).lower() or "whsec_" not in json.dumps(cfg.json())
    ev = client.get(f"{BASE}/api/webhooks/events", timeout=30)
    assert ev.status_code == 200


def test_invalid_signature_test_endpoint(client):
    r = client.post(f"{BASE}/api/simulate/invalid-signature-test", json={}, timeout=30)
    assert r.status_code == 200
    d = r.json()
    assert d["rejected"] is True and d["security_event_logged"] is True


# ---- ingestion end-to-end ----

def _fresh_csv(path="/tmp/reclaim_fresh.csv"):
    """Sample CSV with unique order/payment ids so re-runs are not blocked as duplicates."""
    import uuid as _u
    suffix = _u.uuid4().hex[:5].upper()
    src = open(CSV_PATH).read().replace("ORD-90", f"ORD-{suffix}-").replace("txn_10", f"txn_{suffix}_")
    open(path, "w").write(src)
    return path, suffix


class TestIngestion:
    batch_id = None
    mapping = None
    report = None
    suffix = None
    confirm_status = None

    def test_upload_and_mapping(self, client):
        path, suffix = _fresh_csv()
        TestIngestion.suffix = suffix
        with open(path, "rb") as fh:
            r = client.post(f"{BASE}/api/ingest/upload", files={"file": ("sample_payments.csv", fh, "text/csv")}, timeout=120)
        assert r.status_code == 200, r.text[:400]
        d = r.json()
        TestIngestion.batch_id = d["batch_id"]
        sm = d["suggested_mapping"]
        expected = {
            "payment_id": "transaction_id", "order_id": "order_id", "customer_reference": "customer_email",
            "amount": "total", "status": "payment_status", "failure_code": "error_code", "timestamp": "created_at",
        }
        for field, header in expected.items():
            assert sm[field]["header"] == header, f"{field} mapped to {sm[field]['header']} not {header}"
            assert sm[field].get("confidence") is not None
            assert sm[field].get("source")
        TestIngestion.mapping = {k: v["header"] for k, v in sm.items()}

    def test_confirm_completes_within_gateway_timeout(self, client):
        """Ingestion confirm must return through the public ingress (~60s limit).

        Contract: 200 (synchronous report) or 202 (accepted; import runs in the
        background and is polled via GET /api/ingest/{batch_id} or /batches).
        Anything else (notably 502) is a failure.
        """
        assert TestIngestion.batch_id, "upload step failed"
        try:
            r = client.post(f"{BASE}/api/ingest/{TestIngestion.batch_id}/confirm",
                            json={"mapping": TestIngestion.mapping}, timeout=55)
            TestIngestion.confirm_status = r.status_code
            if r.status_code == 200:
                TestIngestion.report = r.json()["report"]
        except requests.RequestException as exc:
            TestIngestion.confirm_status = f"exception:{exc}"
        assert TestIngestion.confirm_status in (200, 202), (
            f"POST /api/ingest/{{batch}}/confirm did not return 200/202 through the public URL "
            f"(got {TestIngestion.confirm_status}); the import continues server-side but the UI sees a failure."
        )

    def _await_report(self, client):
        import time
        if TestIngestion.report:
            return TestIngestion.report
        for _ in range(40):
            batches = client.get(f"{BASE}/api/ingest/batches", timeout=60).json()["batches"]
            b = next((x for x in batches if x["batch_id"] == TestIngestion.batch_id), None)
            if b and b.get("status") == "IMPORTED" and b.get("report"):
                TestIngestion.report = b["report"]
                TestIngestion.import_results = b.get("import_results")
                return b["report"]
            time.sleep(5)
        pytest.fail("Batch never reached IMPORTED status")

    def test_validation_report_counts(self, client):
        rep = self._await_report(client)
        assert rep["total_rows"] == 16, rep
        assert rep["valid_rows"] == 12, rep
        assert rep["duplicate_rows"] == 1, rep
        assert rep["invalid_amounts"] == 1, rep
        assert rep["unsupported_statuses"] == 1, rep
        assert rep["missing_linkage"] == 1, rep
        assert rep["rows_to_exception_queue"] == 4, (
            f"spec expects 4 rows to exception queue, got {rep['rows_to_exception_queue']} "
            "(duplicate row is not queued as an exception)"
        )

    def test_reimport_blocked(self, client):
        self._await_report(client)
        r = client.post(f"{BASE}/api/ingest/{TestIngestion.batch_id}/confirm",
                        json={"mapping": TestIngestion.mapping}, timeout=120)
        assert r.status_code == 409, r.status_code

    def test_natural_recovery_orders_not_at_risk(self, client):
        self._await_report(client)
        s = TestIngestion.suffix
        for order in (f"ORD-{s}-01", f"ORD-{s}-10"):
            r = client.get(f"{BASE}/api/cases", params={"q": order}, timeout=60)
            assert r.status_code == 200
            cases = [c for c in r.json()["cases"] if c["order_key"] == order]
            assert len(cases) <= 1, f"{order}: {len(cases)} cases"
            for c in cases:
                assert c["status"] == "INVALID" and c.get("recovered_amount", 0) == 0, (
                    f"{order} recovered naturally but case {c['case_id']} is {c['status']}")

    def test_no_double_counting(self, client):
        self._await_report(client)
        order = f"ORD-{TestIngestion.suffix}-02"
        r = client.get(f"{BASE}/api/cases", params={"q": order}, timeout=60)
        cases = [c for c in r.json()["cases"] if c["order_key"] == order]
        assert len(cases) == 1, f"expected 1 case for {order}, got {len(cases)}"
        assert cases[0]["amount_at_risk"] == 8999.0

    def test_high_value_case_awaits_approval(self, client):
        self._await_report(client)
        order = f"ORD-{TestIngestion.suffix}-04"
        r = client.get(f"{BASE}/api/cases", params={"q": order}, timeout=60)
        cases = [c for c in r.json()["cases"] if c["order_key"] == order]
        assert len(cases) == 1, f"ORD-9004 cases: {len(cases)}"
        case = cases[0]
        assert case["amount_at_risk"] == 75000.0
        assert case["status"] == "APPROVAL_PENDING", f"status={case['status']}"
        q = client.get(f"{BASE}/api/review/queue", timeout=60).json()
        assert case["case_id"] in [c["case_id"] for c in q["approval_pending"]]


# ---- case detail / analysis / policy / replay ----

class TestCaseDetail:
    def _a_case(self, client):
        r = client.get(f"{BASE}/api/cases", timeout=60)
        assert r.status_code == 200
        cases = r.json()["cases"]
        assert cases, "no cases available"
        # pick a fully analyzed case (other parallel tests may create brand-new ones mid-pipeline)
        analyzed = [c for c in cases if c.get("model_version") and c.get("policy_result")]
        assert analyzed, "no analyzed case available"
        return analyzed[0]["case_id"]

    def test_case_detail_shape(self, client):
        cid = self._a_case(client)
        r = client.get(f"{BASE}/api/cases/{cid}", timeout=60)
        assert r.status_code == 200
        d = r.json()
        assert '"_id"' not in json.dumps(d)
        case = d["case"]
        assert case["case_id"] == cid
        assert case["currency"] in ("INR", "USD", "EUR")
        assert isinstance(d["attempts"], list) and d["attempts"]
        assert isinstance(d["audit_trail"], list) and d["audit_trail"]
        assert case.get("model_version") in ("claude-sonnet-4-6", "heuristic-fallback-v1"), case.get("model_version")
        assert case.get("natural_recovery_probability") is not None
        evals = case.get("action_evaluations")
        assert evals, "no action evaluations / EIV ranking"
        for row in evals:
            assert "expected_incremental_value" in row, row.keys()
        assert case.get("recommended_action")
        assert case.get("policy_result", {}).get("decision") in ("ALLOW", "BLOCK", "APPROVAL", "STOP")

    def test_case_not_found(self, client):
        r = client.get(f"{BASE}/api/cases/case_does_not_exist", timeout=30)
        assert r.status_code == 404

    def test_replay(self, client):
        cid = self._a_case(client)
        r = client.get(f"{BASE}/api/cases/{cid}/replay", timeout=60)
        assert r.status_code == 200
        d = r.json()
        assert d["case_id"] == cid
        assert d["stage_order"]
        assert len(d["steps"]) >= 1
        assert d["steps"][0]["stage"]

    def test_filters(self, client):
        r = client.get(f"{BASE}/api/cases", params={"status": "APPROVAL_PENDING"}, timeout=60)
        assert r.status_code == 200
        assert all(c["status"] == "APPROVAL_PENDING" for c in r.json()["cases"])
        r2 = client.get(f"{BASE}/api/cases", params={"policy": "STOP"}, timeout=60)
        assert r2.status_code == 200


# ---- human review + verification honesty ----

class TestReviewAndVerification:
    def test_approve_high_value_case_executes_simulated(self, client):
        q = client.get(f"{BASE}/api/review/queue", timeout=60).json()
        pending = q["approval_pending"]
        assert pending, "no APPROVAL_PENDING cases to approve"
        cid = pending[0]["case_id"]
        r = client.post(f"{BASE}/api/cases/{cid}/review", json={"decision": "approve", "note": "TEST_approval"}, timeout=120)
        assert r.status_code == 200, r.text[:400]
        d = r.json()
        assert d.get("executed") is True, d
        act = d["action"]
        assert act["approval_status"] == "HUMAN_APPROVED"
        assert act["execution_mode"] == "SIMULATED"
        assert act["simulated"] is True
        assert act["outcome"] == "PENDING", "executed action must not be pre-marked recovered"
        detail = client.get(f"{BASE}/api/cases/{cid}", timeout=60).json()
        assert any(a["approval_status"] == "HUMAN_APPROVED" for a in detail["actions"])

    def test_reject_pending_case(self, client):
        q = client.get(f"{BASE}/api/review/queue", timeout=60).json()
        pending = q["approval_pending"]
        if not pending:
            # create one via scenario 3
            s = client.post(f"{BASE}/api/simulate/scenario/3", json={}, timeout=180)
            assert s.status_code == 200
            pending = client.get(f"{BASE}/api/review/queue", timeout=60).json()["approval_pending"]
        assert pending, "no pending case for reject test"
        cid = pending[0]["case_id"]
        r = client.post(f"{BASE}/api/cases/{cid}/review", json={"decision": "reject", "note": "TEST_reject"}, timeout=60)
        assert r.status_code == 200, r.text[:300]
        assert r.json()["action_status"] == "REJECTED"

    def test_invalid_review_decision(self, client):
        cases = client.get(f"{BASE}/api/cases", timeout=60).json()["cases"]
        open_cases = [c for c in cases if c["status"] not in ("VERIFIED_RECOVERED", "STOPPED", "INVALID", "CLOSED_NATURAL")]
        assert open_cases
        r = client.post(f"{BASE}/api/cases/{open_cases[0]['case_id']}/review", json={"decision": "bogus"}, timeout=60)
        assert r.status_code == 400

    def test_verification_stays_pending_without_success(self, client):
        # scenario 6 = executed but unverifiable
        s = client.post(f"{BASE}/api/simulate/scenario/6", json={}, timeout=240)
        assert s.status_code == 200, s.text[:300]
        cid = s.json()["case_id"]
        assert cid
        v = client.post(f"{BASE}/api/cases/{cid}/verify", json={}, timeout=120)
        assert v.status_code == 200, v.text[:300]
        detail = client.get(f"{BASE}/api/cases/{cid}", timeout=60).json()["case"]
        assert detail.get("recovered_amount", 0) == 0, detail.get("recovered_amount")
        assert detail["status"] not in ("VERIFIED_RECOVERED",), detail["status"]


# ---- simulator + scenarios ----

class TestSimulator:
    def test_simulate_failed_event(self, client):
        r = client.post(f"{BASE}/api/simulate/payment-event",
                        json={"amount": 4321, "currency": "INR", "status": "failed", "failure_code": "insufficient_funds"},
                        timeout=240)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["result"]["result"] in ("case_created", "case_updated")
        assert d["result"].get("case_id")

    def test_simulate_bad_status(self, client):
        r = client.post(f"{BASE}/api/simulate/payment-event", json={"status": "explode"}, timeout=60)
        assert r.status_code == 400

    def test_scenario_1_no_case(self, client):
        r = client.post(f"{BASE}/api/simulate/scenario/1", json={}, timeout=240)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["case_id"] is None
        assert len(d["steps"]) >= 3

    def test_scenario_2_verified(self, client):
        r = client.post(f"{BASE}/api/simulate/scenario/2", json={}, timeout=300)
        assert r.status_code == 200, r.text[:300]
        cid = r.json()["case_id"]
        case = client.get(f"{BASE}/api/cases/{cid}", timeout=60).json()["case"]
        assert case["status"] == "VERIFIED_RECOVERED", case["status"]
        assert case["recovered_amount"] == 8500.0

    def test_scenario_4_policy_block(self, client):
        r = client.post(f"{BASE}/api/simulate/scenario/4", json={}, timeout=240)
        assert r.status_code == 200
        case = client.get(f"{BASE}/api/cases/{r.json()['case_id']}", timeout=60).json()["case"]
        assert case["policy_result"]["decision"] in ("BLOCK", "STOP"), case["policy_result"]

    def test_scenario_5_duplicate_blocked(self, client):
        r = client.post(f"{BASE}/api/simulate/scenario/5", json={}, timeout=240)
        assert r.status_code == 200, r.text[:300]
        detail = " ".join(s["detail"] for s in r.json()["steps"])
        assert "BLOCKED_AS_DUPLICATE" in detail, detail

    def test_unknown_scenario(self, client):
        r = client.post(f"{BASE}/api/simulate/scenario/99", json={}, timeout=60)
        assert r.status_code == 404


# ---- settings / emergency stop ----

class TestEmergencyStop:
    def test_emergency_stop_forces_stop_decision(self, client):
        try:
            r = client.put(f"{BASE}/api/settings", json={"emergency_stop": True}, timeout=60)
            assert r.status_code == 200, r.text[:300]
            assert client.get(f"{BASE}/api/settings", timeout=30).json()["settings"]["emergency_stop"] is True
            sim = client.post(f"{BASE}/api/simulate/payment-event",
                              json={"amount": 2500, "status": "failed", "failure_code": "do_not_honor"}, timeout=240)
            assert sim.status_code == 200, sim.text[:300]
            cid = sim.json()["result"].get("case_id")
            assert cid, sim.json()
            detail = client.get(f"{BASE}/api/cases/{cid}", timeout=60).json()
            assert detail["case"]["policy_result"]["decision"] == "STOP", detail["case"]["policy_result"]
            assert not [a for a in detail["actions"] if a.get("executed_time")], "action executed while emergency stop was ON"
        finally:
            client.put(f"{BASE}/api/settings", json={"emergency_stop": False}, timeout=60)
            assert client.get(f"{BASE}/api/settings", timeout=30).json()["settings"]["emergency_stop"] is False

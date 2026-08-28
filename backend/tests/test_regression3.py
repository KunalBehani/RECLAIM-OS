"""Regression round 3: ingest validation-report consistency (duplicate rows listed in row_errors)."""
import os
import random
import time

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base_url.rstrip("/")
TOKEN = "test_session_smoke_1787904424204"


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {TOKEN}"})
    return s


def test_auth_me(client):
    r = client.get(f"{BASE_URL}/api/auth/me")
    assert r.status_code == 200, r.text
    assert "email" in r.json()


def test_ingest_report_lists_duplicate_rows(client):
    suffix = f"{random.randint(10000, 99999)}"
    raw = open("/app/test_data/sample_payments.csv").read()
    fresh = raw.replace("ORD-90", f"ORD-{suffix}-").replace("txn_10", f"txn_{suffix}_")

    r = client.post(
        f"{BASE_URL}/api/ingest/upload",
        files={"file": (f"TEST_r3_{suffix}.csv", fresh.encode(), "text/csv")},
    )
    assert r.status_code == 200, r.text
    up = r.json()
    batch_id = up["batch_id"]
    mapping = {k: v["header"] for k, v in up["suggested_mapping"].items()}

    c = client.post(f"{BASE_URL}/api/ingest/{batch_id}/confirm", json={"mapping": mapping}, timeout=55)
    assert c.status_code in (200, 202), f"{c.status_code} {c.text[:300]}"

    report = None
    if c.status_code == 200:
        report = c.json()["report"]
    else:
        for _ in range(40):
            time.sleep(4)
            g = client.get(f"{BASE_URL}/api/ingest/batches", timeout=60)
            assert g.status_code == 200, g.text
            batch = next((x for x in g.json()["batches"] if x["batch_id"] == batch_id), None)
            assert batch is not None
            assert "_id" not in batch
            if batch.get("status") == "IMPORTED" and batch.get("report"):
                report = batch["report"]
                break
        assert report, "batch never reached IMPORTED"
    row_errors = report.get("row_errors") or []
    dup_entries = [e for e in row_errors if "duplicate_payment_id" in e.get("errors", [])]
    assert dup_entries, f"no duplicate_payment_id in row_errors: {row_errors}"
    assert report.get("rows_to_exception_queue") == 4, report
    assert len(row_errors) == report["rows_to_exception_queue"], (
        f"visible rows {len(row_errors)} != queue count {report['rows_to_exception_queue']}"
    )
    assert report.get("duplicate_rows") == 1, report


def test_dashboard_and_cases(client):
    r = client.get(f"{BASE_URL}/api/dashboard/summary")
    assert r.status_code == 200, r.text
    d = r.json()
    assert isinstance(d, dict) and d, d

    r2 = client.get(f"{BASE_URL}/api/cases")
    assert r2.status_code == 200, r2.text
    assert "cases" in r2.json()


def test_invalid_signature_endpoint(client):
    r = client.post(f"{BASE_URL}/api/simulate/invalid-signature-test")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["http_status"] == 401, data
    assert data["security_event_logged"] is True, data

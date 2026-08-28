"""Manual probe: measure ingest confirm latency on public URL vs localhost."""
import time
import uuid
import requests

BASE_PUB = "https://reclaim-verify.preview.emergentagent.com"
BASE_LOC = "http://localhost:8001"
TOKEN = "test_session_smoke_1787904424204"
H = {"Authorization": f"Bearer {TOKEN}"}


def fresh_csv():
    src = open("/app/test_data/sample_payments.csv").read()
    suffix = uuid.uuid4().hex[:6]
    out = src.replace("ORD-90", f"ORD-{suffix}-").replace("txn_10", f"txn_{suffix}_")
    p = f"/tmp/probe_{suffix}.csv"
    open(p, "w").write(out)
    return p


def run(base, label):
    p = fresh_csv()
    with open(p, "rb") as fh:
        r = requests.post(f"{base}/api/ingest/upload", headers=H,
                          files={"file": ("s.csv", fh, "text/csv")}, timeout=180)
    print(label, "upload", r.status_code)
    if r.status_code != 200:
        print(r.text[:300]); return
    d = r.json()
    mapping = {k: v["header"] for k, v in d["suggested_mapping"].items()}
    t0 = time.time()
    try:
        r2 = requests.post(f"{base}/api/ingest/{d['batch_id']}/confirm", headers=H,
                           json={"mapping": mapping}, timeout=600)
        print(f"{label} confirm status={r2.status_code} elapsed={time.time()-t0:.1f}s")
        if r2.status_code == 200:
            print(label, "report", r2.json()["report"])
        else:
            print(label, r2.text[:300])
    except Exception as e:
        print(f"{label} confirm EXC after {time.time()-t0:.1f}s: {e}")


#run(BASE_LOC, "LOCAL")
run(BASE_PUB, "PUBLIC")

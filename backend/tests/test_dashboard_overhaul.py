"""Iteration 4 — dashboard overhaul (metrics/funnel/KPI/charts/cost-ledger/case filters)."""
import os

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base.rstrip("/")
TOKEN = "test_session_smoke_1787904424204"

FUNNEL_STAGES = ["detected", "eligible", "evaluated", "policy_decided", "ready", "executed", "verifying", "recovered"]


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    r = s.get(f"{BASE_URL}/api/auth/me", timeout=60)
    if r.status_code != 200:
        pytest.fail(f"auth/me failed {r.status_code}: {r.text[:300]}")
    return s


@pytest.fixture(scope="module")
def summary(client):
    r = client.get(f"{BASE_URL}/api/dashboard/summary", params={"days": 30}, timeout=120)
    assert r.status_code == 200, r.text[:300]
    return r.json()


# --- funnel integrity ---
class TestFunnel:
    def test_monotonic_non_increasing(self, summary):
        stages = summary["funnel"]["stages"]
        assert summary["funnel"]["order"] == FUNNEL_STAGES
        vals = [stages[s] for s in FUNNEL_STAGES]
        assert all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1)), vals
        assert summary["funnel"]["mode"] == "cumulative"

    def test_side_stats_and_meta(self, summary):
        side = summary["funnel"]["side"]
        for k in ("stopped", "invalid", "blocked"):
            assert isinstance(side[k], int) and side[k] >= 0
        for s in FUNNEL_STAGES:
            assert summary["funnel"]["meta"][s]["label"]
            assert summary["funnel"]["meta"][s]["description"]

    @pytest.mark.parametrize("stage", FUNNEL_STAGES)
    def test_stage_filter_count_matches_funnel(self, client, summary, stage):
        r = client.get(f"{BASE_URL}/api/cases", params={"stage": stage, "limit": 500}, timeout=120)
        assert r.status_code == 200
        cases = r.json()["cases"]
        expected = min(summary["funnel"]["stages"][stage], 500)
        assert len(cases) == expected, f"{stage}: api={len(cases)} funnel={expected}"

    def test_recovered_stage_all_verified(self, client):
        r = client.get(f"{BASE_URL}/api/cases", params={"stage": "recovered"}, timeout=120)
        assert r.status_code == 200
        for c in r.json()["cases"]:
            assert c["status"] == "VERIFIED_RECOVERED"

    @pytest.mark.parametrize("stage,expect", [("stopped", "STOPPED"), ("invalid", "INVALID")])
    def test_side_stage_filters(self, client, summary, stage, expect):
        r = client.get(f"{BASE_URL}/api/cases", params={"stage": stage}, timeout=120)
        assert r.status_code == 200
        cases = r.json()["cases"]
        assert len(cases) == summary["funnel"]["side"][stage]
        assert all(c["status"] == expect for c in cases)

    def test_blocked_stage_filter(self, client, summary):
        r = client.get(f"{BASE_URL}/api/cases", params={"stage": "blocked"}, timeout=120)
        assert r.status_code == 200
        cases = r.json()["cases"]
        assert len(cases) == summary["funnel"]["side"]["blocked"]
        assert all(c["policy_result"]["decision"] == "BLOCK" for c in cases)

    def test_at_risk_stage_matches_kpi(self, client, summary):
        r = client.get(f"{BASE_URL}/api/cases", params={"stage": "at_risk", "limit": 500}, timeout=120)
        assert r.status_code == 200
        assert len(r.json()["cases"]) == min(summary["kpis"]["revenue_at_risk_cases"], 500)


# --- KPIs / currency integrity / recovery rate ---
class TestKpis:
    def test_money_maps_are_per_currency(self, summary):
        k = summary["kpis"]
        for field in ("revenue_at_risk", "verified_gross_recovery", "action_costs", "verified_net_recovery"):
            assert isinstance(k[field], dict), field
            for ccy, amt in k[field].items():
                assert isinstance(ccy, str) and len(ccy) >= 3
                assert isinstance(amt, (int, float))

    def test_net_equals_gross_minus_costs(self, summary):
        k = summary["kpis"]
        for ccy in set(k["verified_gross_recovery"]) | set(k["action_costs"]):
            expected = round(k["verified_gross_recovery"].get(ccy, 0) - k["action_costs"].get(ccy, 0), 2)
            assert k["verified_net_recovery"][ccy] == pytest.approx(expected, abs=0.01)

    def test_recovery_rate_formula(self, client, summary):
        k = summary["kpis"]
        num, den = k["recovery_rate_numerator"], k["recovery_rate_denominator"]
        if den == 0:
            assert k["recovery_rate_pct"] is None
        else:
            assert k["recovery_rate_pct"] == pytest.approx(round(num / den * 100, 1))
        # denominator excludes STOPPED / INVALID
        statuses = {s["status"]: s["count"] for s in summary["charts"]["cases_by_status"]}
        known = sum(statuses.get(s, 0) for s in ("VERIFIED_RECOVERED", "NATURALLY_RECOVERED", "NOT_RECOVERED"))
        assert den == known
        assert num == statuses.get("VERIFIED_RECOVERED", 0)

    def test_at_risk_sum_matches_cases(self, client, summary):
        r = client.get(f"{BASE_URL}/api/cases", params={"stage": "at_risk", "limit": 500}, timeout=120)
        totals = {}
        for c in r.json()["cases"]:
            totals[c["currency"]] = round(totals.get(c["currency"], 0) + float(c["amount_at_risk"] or 0), 2)
        for ccy, amt in totals.items():
            assert summary["kpis"]["revenue_at_risk"][ccy] == pytest.approx(amt, abs=1.0)

    def test_exceptions_open_matches_review_queue(self, client, summary):
        r = client.get(f"{BASE_URL}/api/review/queue", timeout=120)
        assert r.status_code == 200
        assert r.json()["counts"]["exceptions"] == summary["kpis"]["exceptions_open"]


# --- charts ---
class TestCharts:
    def test_timeseries_per_currency_strict(self, client):
        r = client.get(f"{BASE_URL}/api/dashboard/summary", params={"days": 7}, timeout=120)
        assert r.status_code == 200
        charts = r.json()["charts"]
        assert set(charts["timeseries"]) == set(charts["currencies"])
        for ccy, pts in charts["timeseries"].items():
            assert len(pts) == 7
            assert all({"date", "at_risk", "verified_recovered"} <= set(p) for p in pts)

    def test_days_param_controls_length(self, client):
        for days in (7, 30, 90):
            r = client.get(f"{BASE_URL}/api/dashboard/summary", params={"days": days}, timeout=180)
            assert r.status_code == 200
            data = r.json()
            assert data["days"] == days
            for pts in data["charts"]["timeseries"].values():
                assert len(pts) == days

    def test_failure_reasons_humanized(self, summary):
        rows = summary["charts"]["failure_reasons"]
        assert rows
        for row in rows:
            assert "_" not in row["label"], row
            assert row["label"] != row["code"] or row["code"] == "unknown"
            assert row["count"] > 0

    def test_sources_taxonomy(self, summary):
        allowed = {"LIVE", "TEST_MODE", "IMPORTED", "SIMULATED"}
        for s in summary["charts"]["sources"]:
            assert s["source"] in allowed, s

    def test_cases_by_status_totals_detected(self, summary):
        total = sum(s["count"] for s in summary["charts"]["cases_by_status"])
        assert total == summary["funnel"]["stages"]["detected"]

    def test_recovery_by_action_currency_separated(self, summary):
        for row in summary["charts"]["recovery_by_action"]:
            assert isinstance(row["amounts"], dict) and row["amounts"]

    def test_attributed_action_filter(self, client, summary):
        rows = summary["charts"]["recovery_by_action"]
        if not rows:
            pytest.skip("no attributed recoveries")
        action = rows[0]["action"]
        r = client.get(f"{BASE_URL}/api/cases", params={"attributed_action": action}, timeout=120)
        assert r.status_code == 200
        cases = r.json()["cases"]
        assert cases
        assert all(c["attributed_action"] == action and c["status"] == "VERIFIED_RECOVERED" for c in cases)

    def test_policy_activity_from_audit_trail(self, summary):
        pa = summary["policy_activity"]
        assert pa["total_decisions"] > 0
        assert sum(d["count"] for d in pa["decisions"]) == pa["total_decisions"]
        assert {d["decision"] for d in pa["decisions"]} <= {"ALLOW", "APPROVAL", "BLOCK", "STOP"}


# --- cost ledger ---
class TestCostLedger:
    def test_ledger_matches_kpi_costs(self, client, summary):
        r = client.get(f"{BASE_URL}/api/dashboard/cost-ledger", timeout=120)
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == len(data["entries"]) == summary["kpis"]["executed_action_count"]
        assert data["totals"] == summary["kpis"]["action_costs"]
        for e in data["entries"]:
            assert e["case_title"] and not e["case_title"].startswith("case_")
            assert isinstance(e["estimated_cost"], (int, float))
            assert e["execution_mode"]
            assert "_id" not in e

    def test_ledger_requires_auth(self):
        r = requests.get(f"{BASE_URL}/api/dashboard/cost-ledger", timeout=60)
        assert r.status_code in (401, 403)


# --- case enrichment / naming / filters ---
class TestCases:
    def test_titles_human_readable(self, client):
        r = client.get(f"{BASE_URL}/api/cases", params={"limit": 200}, timeout=120)
        assert r.status_code == 200
        cases = r.json()["cases"]
        assert cases
        for c in cases:
            assert c["title"] and not c["title"].startswith("case_"), c["title"]
            assert c["why_at_risk"]
            assert c["source_category"] in {"LIVE", "TEST_MODE", "IMPORTED", "SIMULATED"}
            assert "_id" not in c

    def test_source_filter(self, client, summary):
        counts = {s["source"]: s["count"] for s in summary["charts"]["sources"]}
        for source, count in counts.items():
            r = client.get(f"{BASE_URL}/api/cases", params={"source": source, "limit": 500}, timeout=120)
            assert r.status_code == 200
            cases = r.json()["cases"]
            assert len(cases) == min(count, 500)
            assert all(c["source_category"] == source for c in cases)

    def test_source_filter_empty_state(self, client, summary):
        counts = {s["source"]: s["count"] for s in summary["charts"]["sources"]}
        if counts.get("LIVE"):
            pytest.skip("LIVE data exists")
        r = client.get(f"{BASE_URL}/api/cases", params={"source": "LIVE"}, timeout=120)
        assert r.status_code == 200
        assert r.json()["cases"] == []

    @pytest.mark.parametrize("sort", ["newest", "oldest", "amount_desc", "amount_asc"])
    def test_sorting(self, client, sort):
        r = client.get(f"{BASE_URL}/api/cases", params={"sort": sort, "limit": 100}, timeout=120)
        assert r.status_code == 200
        cases = r.json()["cases"]
        assert len(cases) > 1
        if sort == "newest":
            keys = [c["created_at"] for c in cases]
            assert keys == sorted(keys, reverse=True)
        elif sort == "oldest":
            keys = [c["created_at"] for c in cases]
            assert keys == sorted(keys)
        else:
            # Currency-grouped semantics: currencies form contiguous blocks (alphabetical),
            # amounts are sorted only WITHIN each currency block (never blended).
            reverse = sort == "amount_desc"
            groups = []
            for c in cases:
                cur = c.get("currency") or "UNKNOWN"
                if not groups or groups[-1][0] != cur:
                    groups.append((cur, []))
                groups[-1][1].append(float(c["amount_at_risk"] or 0))
            currencies = [g[0] for g in groups]
            assert len(currencies) == len(set(currencies)), f"currency blocks not contiguous: {currencies}"
            assert currencies == sorted(currencies), f"currency blocks not alphabetical: {currencies}"
            for cur, amounts in groups:
                assert amounts == sorted(amounts, reverse=reverse), f"{cur} block not sorted ({sort})"

    def test_search_by_order_key(self, client):
        r = client.get(f"{BASE_URL}/api/cases", params={"limit": 5}, timeout=120)
        order_key = r.json()["cases"][0]["order_key"]
        r2 = client.get(f"{BASE_URL}/api/cases", params={"q": order_key}, timeout=120)
        assert r2.status_code == 200
        assert any(c["order_key"] == order_key for c in r2.json()["cases"])

    def test_policy_and_status_filters(self, client):
        r = client.get(f"{BASE_URL}/api/cases", params={"policy": "BLOCK"}, timeout=120)
        assert r.status_code == 200
        assert all(c["policy_result"]["decision"] == "BLOCK" for c in r.json()["cases"])
        r = client.get(f"{BASE_URL}/api/cases", params={"status": "APPROVAL_PENDING"}, timeout=120)
        assert r.status_code == 200
        assert all(c["status"] == "APPROVAL_PENDING" for c in r.json()["cases"])


# --- confidence honesty ---
class TestConfidence:
    def test_no_fake_confidence_on_heuristic_cases(self, client):
        """Heuristic-analyzed cases must expose confidence=None + confidence_type='heuristic'.
        Legacy records written before the fix still carry the fake 0.5 placeholder."""
        r = client.get(f"{BASE_URL}/api/cases", params={"limit": 500}, timeout=120)
        cases = r.json()["cases"]
        stale_heuristic, ok_heuristic, llm_missing_type, llm_ok = [], 0, [], 0
        for c in cases:
            mv = c.get("model_version")
            if not mv:
                continue
            if "heuristic" in mv:
                if c.get("confidence") is None and c.get("confidence_type") == "heuristic":
                    ok_heuristic += 1
                else:
                    stale_heuristic.append((c["case_id"], c.get("confidence"), c.get("confidence_type")))
            else:
                if c.get("confidence_type") == "model_uncalibrated":
                    llm_ok += 1
                else:
                    llm_missing_type.append((c["case_id"], c.get("confidence"), c.get("confidence_type")))
        print(f"heuristic_ok={ok_heuristic} heuristic_stale={len(stale_heuristic)} "
              f"llm_ok={llm_ok} llm_missing_confidence_type={len(llm_missing_type)}")
        assert not stale_heuristic, (
            f"{len(stale_heuristic)} legacy heuristic cases still return the fake 0.5 confidence "
            f"placeholder via API (not backfilled): {stale_heuristic[:5]}")
        assert not llm_missing_type, (
            f"{len(llm_missing_type)} LLM cases have no confidence_type, so the UI cannot label them "
            f"'uncalibrated': {llm_missing_type[:5]}")

    def test_review_queue_no_bare_50pct(self, client):
        r = client.get(f"{BASE_URL}/api/review/queue", timeout=120)
        assert r.status_code == 200
        bad = [c["case_id"] for c in r.json()["approval_pending"]
               if c.get("model_version") and "heuristic" in c["model_version"] and c.get("confidence") is not None]
        assert not bad, f"review queue cases still carry heuristic confidence values: {bad[:5]}"

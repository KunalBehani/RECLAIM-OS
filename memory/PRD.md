# RECLAIM OS — Product Requirements Document

## Original Problem Statement
Build a production-quality, full-stack web application called **RECLAIM OS** — "AI Revenue Recovery, with Control." An intelligent, policy-bounded revenue recovery platform for merchants focused deeply on one use case: **failed payment recovery**. It must detect genuinely unresolved revenue at risk (never equating a failed payment with lost money), estimate a natural-recovery baseline, rank recovery actions by expected incremental value, enforce a deterministic policy engine (ALLOW/BLOCK/APPROVAL/STOP), support human-in-the-loop approval, execute only authorized actions, independently verify outcomes before counting recovered revenue, and maintain a complete audit trail with a visual Decision Replay. Ingestion via real-time signed webhooks and CSV/XLSX batch upload with AI-assisted field mapping. Honest metrics: Revenue at Risk / Expected Recoverable / Verified Recovered are never blended; SIMULATED vs REAL vs VERIFIED are never blurred.

## User Choices (confirmed)
- AI provider: **Claude Sonnet** (claude-sonnet-4-6 via Emergent Universal Key), with deterministic heuristic fallback
- Auth: **Emergent-managed Google social login**
- Scope: full depth on the core loop + real webhook endpoint with labeled simulator
- Currency: **multi-currency per record**

## Architecture
Modular monolith: React frontend, FastAPI backend, MongoDB (motor).

- `backend/detection.py` — unified recovery engine: order linking, natural-recovery detection, double-count prevention (one case per order), case pipeline, independent verification
- `backend/intelligence.py` — Claude Sonnet analysis (diagnosis, natural-recovery probability, action probabilities, explanation, evidence) blended 50/50 with deterministic heuristics; all financial arithmetic deterministic; `model_version` recorded per case
- `backend/policy.py` — action catalog + deterministic policy engine (12 rules: emergency stop, closed case, window expiry, max executions, cooldown, duplicate pending, cost cap, amount threshold, low confidence, do-not-contact, unknown action)
- `backend/execution.py` — idempotent action adapter (SIMULATED in this environment, labeled end-to-end)
- `backend/routes_webhooks.py` — HMAC-SHA256 signature verification, unique event_id (replay-safe), 24h timestamp tolerance, security-event logging
- `backend/ingestion.py` — CSV/XLSX/XLS parsing, synonym + Claude-assisted field mapping, strict validation with exception queue
- `backend/routes_ingest.py` — 202 + background import (bounded per-batch LLM budget, heuristic fallback beyond it) with status polling
- `frontend/src/pages/` — Dashboard, CaseDetail (Decision Replay), Ingest, ReviewQueue, Events (simulator + scenario runner), Login

## User Personas
- **Merchant operations lead** — uploads payment data, monitors recovery, reviews high-value cases
- **Risk/compliance reviewer** — approves/rejects actions, inspects audit trail and decision replay
- **Judge/demo presenter** — runs labeled demo scenarios, verifies failure handling

## Core Requirements (static)
1. Failed payment ≠ lost revenue; later-success check before case creation
2. Natural-recovery baseline before any intervention
3. EIV-ranked action selection from a fixed catalog
4. Deterministic policy gate; AI cannot override; humans cannot override BLOCK/STOP
5. Only independently verified settlements count as recovered revenue
6. Immutable audit trail + Decision Replay per case
7. Two ingestion modes into one engine; bad records never enter financial totals
8. Honest metrics and explicit REAL/TEST/SIMULATED/UNVERIFIED/VERIFIED taxonomy

## Implemented (2026-08-28)
- Full core loop: webhook + CSV/XLSX ingestion → detection → AI analysis (Claude + fallback) → policy engine → auto-execution (SIMULATED) / approval queue → independent verification → audit
- Emergent Google OAuth (owner: kunalkavya20@gmail.com) with session cookies; test-session flow documented in /app/auth_testing.md
- Dashboard: 6 KPI cards (per-currency), recovery funnel, 5 charts, filterable/searchable case table
- Case detail: AI analysis, EIV action comparison, policy result with rule reasons, payment timeline, execution history, verification evidence, audit trail, interactive Decision Replay (step/prev/next/play)
- Human Review Queue: approve / reject / alternate action / mark invalid / stop; exception queue with resolve
- Events & Simulator: signed SIMULATED events through the real pipeline, invalid-signature test (401 + security log), 6 demo scenarios, webhook event log
- Emergency stop (global policy halt), policy settings API
- 56+ automated tests (unit + integration + targeted regression suite) — all passing
- Testing agent iterations 1–3 complete: critical ingest-timeout bug found and fixed (202 + background import + polling); duplicate-exception semantics, batch status badges, mobile responsiveness, net-recovery clarity, and invalid-signature UI feedback all verified fixed (iteration_3: 100% pass, no open action items)
- Sample merchant CSV at /app/test_data/sample_payments.csv; comprehensive README.md

## Implemented (2026-08-28, dashboard data-integrity overhaul)
- New `backend/metrics.py`: single source of truth — pure functions for case titles, source taxonomy (LIVE/TEST MODE/IMPORTED/SIMULATED), why-at-risk text, strict cumulative funnel (monotonic by construction, execution evidence from immutable audit trail), KPIs with known-final-outcome recovery-rate denominator, charts, policy activity from audit events
- Fake 50% confidence eliminated: heuristic fallback now carries `confidence=null` + `confidence_type='heuristic'`; LLM cases labeled `model_uncalibrated`; legacy data backfilled via migration (32 + 174 cases); money-moving actions without model confidence now require human approval (policy)
- Case naming: truthful human-readable titles ("Failed Payment for Order ORD-XXXX") primary everywhere; internal IDs only in detail/audit
- Dashboard: KPI drill-downs (filtered case views + cost/recovery ledger modal), definition/formula tooltips, clickable funnel + side stats (stopped/invalid/blocked/exceptions), click-to-filter charts, humanized failure labels, 7/30/90D range, strict per-currency filtering (never summed/converted), currency-grouped amount sorting, honest empty/insufficient-data states
- Fixed by testing rounds 4-5: invisible Policy Control Activity card (ResponsiveContainer 0x0 on non-chart JSX), misplaced/stretched status legend, legacy confidence backfill, cross-currency sort blending
- Tests: 109/109 passing (test_core + test_metrics + test_dashboard_overhaul + backend_test)

## Prioritized Backlog
### P0 (remaining)
- None

### P1
- Batch Evaluation Lab: baseline comparison (do-nothing / fixed-rule / blanket-action) with held-out labeled data support, precision/recall when ground truth exists
- Scheduled verification sweeps (platform cron) instead of manual/on-event verification
- Real payment-provider adapter (e.g., Stripe test mode) behind the existing webhook architecture
- Scale hardening: persist stage membership/source_category on case documents (funnel currently recomputed per stage-filtered request); extract Dashboard.jsx sub-components

### P2
- Failure Lab as a dedicated page (behaviors currently covered by simulator scenarios)
- PDF ingestion (structured reports only, confidence-gated)
- Multi-tenant merchant accounts and role-based permissions beyond owner/analyst
- Dedicated "executed, outcome pending" metric distinct from broad active-case count

## Next Tasks
1. Build the Evaluation Lab (P1) as the next major feature
2. Add scheduled verification sweep via .emergent/crons.yml (P1)
3. Scale hardening: denormalize stage/source fields onto case docs (P1)

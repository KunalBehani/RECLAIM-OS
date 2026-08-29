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

## Implemented (2026-08-29, Phase 1 — Razorpay TEST MODE real data architecture)
- Provider abstraction (`providers/base.py`) + `providers/razorpay_adapter.py`: raw-body HMAC-SHA256 signature verification (constant-time), event normalization (payment.authorized/captured/failed, order.paid; paise→major units), provider API client (test connection, fetch order/payment, sanitized errors)
- Public webhook endpoint `POST /api/webhooks/razorpay`: 1MB limit, 503 when unconfigured, signature-first processing, `x-razorpay-event-id` idempotency via durable `provider_events` store (unique provider+event id), malformed/unsupported/replay handling
- Engine refactor (`detection.py`): order-centric, arrival-order-independent evaluation; precedence-aware payment ledger upserts (never downgrade); `orders` collection for order.paid; risk_evidence stored per case; attribution tiers STRONG/MODERATE/UNCERTAIN/NONE (only customer-facing actions attributable; monitoring never earns attribution); PARTIALLY_RECOVERED outcome; natural recovery never attributed
- Integrations UI page: TEST MODE config (owner-only, masked creds, secrets server-side only), test connection (honest ERROR with dummy keys), disconnect, webhook endpoint + copy, integration health (real counts), verification sweep, 12-scenario webhook test lab through the real endpoint, LIVE mode card marked unavailable
- Source taxonomy: TEST_MODE added end-to-end (metrics, badges, filters); LIVE/TEST MODE/IMPORTED/SIMULATED never blended
- Review queue exception count fixed (capped-list bug); out-of-order INVALID guard now references last failed attempt instead of case-creation time (real engine bug found by tests)
- Tests: 129 main suite + 20 Razorpay pipeline (A–T) + 47 public-review + 6 iteration-7 regression = 176 passing, 0 failing
- Round-7 follow-ups verified: Decision Replay now starts at WEBHOOK_RECEIVED → EVENT_NORMALIZED → CASE_CREATED (shared `_audit_for_case` helper prevents endpoint drift); manual execute has the same pre-execution settle guard as the autopilot
- Round-6 testing-agent defects fixed (round-7 verified): attribution allow-list now applied symmetrically (monitoring/control actions can never earn recovery attribution); WEBHOOK_RECEIVED/EVENT_NORMALIZED lineage surfaced in case audit trail; pre-execution settle guard closes the LLM-latency race; internal webhook base URL moved to env (INTERNAL_WEBHOOK_BASE_URL); minor UI cleanups
- NOT DONE (blocked by external config, per spec §30): a genuine provider-originated Razorpay test event — requires user's real rzp_test_ credentials + webhook registration. Exact steps in finish summary.

## Implemented (2026-08-29, Razorpay 401 root-cause diagnostic + credential-safety hardening)
- **Root cause proven external**: controlled server-side reproduction (`backend/scripts/razorpay_repro.py`) called Razorpay directly with the stored credentials, bypassing all app code → HTTP 401 `BAD_REQUEST_ERROR "Authentication failed"`. Verdict A: Razorpay itself rejected the stored key pair; RECLAIM's request construction was correct all along (no whitespace mangling — raw vs stripped lengths identical; correct endpoint, correct Basic Auth).
- `razorpay_adapter.py`: request-time `.strip()` on key_id/key_secret (defense vs copy-paste whitespace/newlines in DB); 401 now raises masked diagnostics only (mode, credential_source, key prefix, lengths, endpoint, method, auth_method — never the secret or Authorization header).
- New owner-only endpoint `GET /api/integrations/razorpay/diagnostics`: safe masked credential state (key_id_prefix, lengths, secret-present booleans, source, endpoint, auth method) — verified via curl, no secrets leaked.
- **Test-fixture flaw found & fixed**: `test_razorpay.py` `setup_module` overwrote the real stored integration doc with dummy creds on every suite run; now snapshots and restores the pre-existing doc in `teardown_module` (verified with sentinel round-trip: RESTORE_OK).
- Tests: 4 new adapter tests (whitespace trim, newline trim, 401 masked diagnostics, TEST/LIVE source selection). Full backend suite: **186 passed**; test_razorpay.py re-run after fixture fix: 24 passed.
- **Blocked**: real connection test cannot reach CONNECTED until the user enters a valid, current `rzp_test_` key pair (previous pair is invalid/revoked at Razorpay; DB cleared to NOT_CONFIGURED).

## Prioritized Backlog
### P0 (remaining)
- None (Razorpay 401 diagnosed to external cause; awaiting valid user credentials to complete CONNECTED verification)

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

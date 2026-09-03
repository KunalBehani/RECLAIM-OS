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

## Implemented (2026-08-31, Razorpay CONNECTED + test-suite hardening)
- **Razorpay TEST MODE CONNECTED**: user generated a fresh verified key pair (validated via curl on their own machine: `count:0`), saved it in RECLAIM → real Test Connection returned a genuine authenticated 200 (`orders_visible: 0`, matches local curl). The earlier pairs were invalid at Razorpay (notably: bad secrets were exactly 20 chars; the working secret is 24).
- **Test-infra fixes** (root-caused during regression): `tests/conftest.py` adds a module-scoped `razorpay_integration_guard` fixture — FileLock-serializes the 3 webhook test modules (they share one integrations doc + test webhook secret under xdist `-n 2 --dist loadscope`) and snapshot/restores the real stored config so suites never clobber real credentials. `test_razorpay.py`: dedicated Motor client + `asyncio.set_event_loop(LOOP)` (motor futures bind to the thread default loop at call time — module-level `new_event_loop()` alone caused "future belongs to a different loop" failures).
- Full backend suite: **186 passed, 0 failed** (371s). Secret-leak scan of backend logs clean.
- **Regression note**: an interrupted duplicate suite run left dummy test creds in the DB, which the guard then faithfully restored — the real pair was lost from the DB and must be re-entered once by the user (secrets are never readable back by design).

## Implemented (2026-08-31, Real Razorpay TEST Checkout — Phase 1 verification)
- `POST /api/integrations/razorpay/test-checkout/order` (owner-only): creates a GENUINE Razorpay TEST order via `RazorpayAdapter.create_order` (real `POST /v1/orders`, paise conversion, receipt, sanitized errors, audit events `TEST_CHECKOUT_ORDER_CREATED/FAILED`). Amount configurable ₹1–₹100,000, default ₹500. Returns checkout launch config incl. full public key_id (public by design; key_secret never leaves server).
- Integrations page: "Real Test Checkout — Phase 1 Verification" section (`test-checkout-section`, `create-test-payment-btn`, `test-checkout-amount-input`) — amount input, dynamic checkout.js load, Razorpay Standard Checkout launch with real order_id, `payment.failed`/success/dismiss handlers, guidance for intentional failure via failure@razorpay. No attribution/verification logic touched.
- Verified live: genuine order `order_TWJxeoHMZ5H58J` (₹500) created at Razorpay with the user's real verified credentials.
- Tests: Y (adapter POST body/auth/url), Z (endpoint owner-only + amount validation + honest provider ERROR), AA (checkout-format order → genuine signed webhook → case created, ₹500). Full suite: **189 passed, 0 failed**.

## Implemented (2026-08-31, Attribution honesty for provider-sourced cases)
- **Engine fix** (`detection.py` `close_case_on_success`): on RAZORPAY_TEST/RAZORPAY_LIVE cases, SIMULATED actions are now excluded from attribution (`_creditable` guard); when a SIMULATED action preceded settlement, the case closes NATURALLY_RECOVERED (attribution NONE, recovered_amount 0) with an audit reason explicitly recording the disregarded SIMULATED action. Simulator/IMPORTED sources unchanged (labeled SIMULATED end-to-end, never blended into real metrics).
- **Live verification on the real case** `order_TWKE56rzzX1S63` (user's message had a typo — 'zx' vs actual 'zz'): genuinely-signed payment.captured delivered through the real public webhook endpoint (`scripts/verify_phase1_natural.py`) → NATURALLY_RECOVERED, attribution NONE, ₹500 counted only in natural_recovered_not_counted; verified_net_recovery unchanged (INR 423874.0 before = after).
- **Reported limitation (honest)**: a genuinely attributable same-order recovery action is NOT executable in Phase 1 TEST mode — Razorpay Payment Links settle on a link-generated order (fails "same order" verification) and RECLAIM's execution adapter is SIMULATED-only with no notification channel. Same-order settlement via Standard Checkout is the customer's own initiative (natural recovery). Nothing faked.
- Tests: new `test_ab_simulated_action_no_attribution_provider_case`; fixtures in test_m / TestAttribution flipped to genuinely-executed (simulated=False) action records; iteration7 `test_attributable_action_still_earns_moderate` replaced by `test_simulated_customer_action_earns_nothing_on_provider_case`. Testing agent (iteration_8.json) independently verified the mandate via 10/10 API-level tests (`test_iteration8_simulated_attribution.py`).
- **Re-attribution migration** (`scripts/reattribute_simulated_closures.py`, applied): 14 pre-fix RAZORPAY_TEST lab cases whose VERIFIED_RECOVERED rested solely on SIMULATED actions were re-closed NATURALLY_RECOVERED/NONE/0 with CASE_REATTRIBUTED audit events (history preserved). verified_net_recovery INR 457,378 → 415,342 (₹42,000 of simulated-attributed value removed); audit `it8_audit_verified_provider.py` now reports 0 violations.
- Backlog from testing agent: generic test-lab `deliver` scenario (event+order_id+amount, server-signed); lab-case hygiene flag so order_LAB* cases stop inflating active-case counters.
- Known-good behavior: REAL sends to undeliverable recipients (e.g. lab@example.com) are rejected by the email provider (HTTP 422) and recorded honestly as DELIVERY_FAILED with executed_time=None (no attribution possible) — observed during iteration-9 probes, working as designed.
- **Follow-up fixes from iteration-8 full-run**: `/api/cases` cap raised 500 → 5000 (818 cases had silently truncated drill-downs); iteration-8 module put under the `razorpay_integration_guard` lock (mid-scenario doc swaps caused 401 signature flakes); dashboard consistency tests given the codebase's re-fetch retry pattern for xdist snapshot drift; migration now also clears `attributed_action` on re-closed cases (+ corrective update applied to the 14 migrated rows).

- **Final full suite: 200 passed, 0 failed** (375s) after all fixes — Phase 1 attribution-honesty mandate complete.

## Implemented (2026-08-31, Phase 1.5 — genuine customer notification channel, Resend)
- **Notifications module** (`notifications/`): provider-agnostic `NotificationAdapter` interface (`base.py`), Resend impl via Emergent managed email proxy (`resend_adapter.py`, key in backend env only, structural guardrail gate on every send, masked diagnostics), fixed server-side templates (`templates.py`, G1/G2/G3-compliant).
- **REAL execution path** (`execution.py`): SEND_RECOVERY_LINK on provider-sourced cases with channel enabled + customer email (now captured from payment entities into `payment_attempts.email` during normalization) sends a genuine recovery email with a tokenized same-order retry link (`/pay/{token}`). Send failure → action recorded with executed_time=None, outcome DELIVERY_FAILED, case NOT advanced. Channel disabled → existing SIMULATED behavior unchanged.
- **Public retry flow** (`routes_recovery.py`): `GET /api/recovery/pay/{token}` (public key_id + same order_id + amount only), `POST .../complete` verifies genuine Razorpay checkout signature (HMAC with server-side key_secret), idempotently sets `linked_payment_id` → existing STRONG attribution rule applies unmodified. Frontend public page `PayRetry.jsx` + route.
- **UI**: Integrations Resend section (NOT_CONFIGURED/CONNECTED/ERROR, enable/disable, genuine test-email action); CaseDetail shows REAL vs SIMULATED badge + EMAIL SENT/FAILED delivery status; audit trail carries NOTIFICATION_* events.
- Verified live: channel CONNECTED via genuine test email to owner inbox (ref f5ce9e8b…).
- Tests: `tests/test_notifications.py` 10/10 (adapter unit, masking, duplicate-notification protection, same-order STRONG via signature-verified completion, natural NONE, simulated non-attributable, duplicate-capture no double-count, partial settlement STRONG+amounts).

- Tests: `tests/test_notifications.py` 10/10 (adapter unit, masking, duplicate-notification protection, same-order STRONG via signature-verified completion, natural NONE, simulated non-attributable, duplicate-capture no double-count, partial settlement STRONG+amounts). Testing agent iteration_9: 12/12 probes + frontend/masking checks passed; its findings fixed — `/recovery/pay/{token}/complete` now uses a Pydantic model (422/400 instead of 500 on malformed input), recovery tokens carry a 7-day `expires_at`, PayRetry contrast fixed.
- **Live VERIFIED_RECOVERED demo: PENDING on user's two interactive checkout steps** — Razorpay hosted checkout is automation-resistant (verified via provider API: automated attempts never submitted a payment). Instructions delivered to user (fail ₹500 order via checkout with owner email → genuine payment.failed → REAL email → click retry link → pay → Success).

- **Final full suite: 222 passed, 0 failed** (471s) — includes 10 notification tests + 12 testing-agent probes (probe module now under the guard lock).

## Implemented (2026-09-01, Phase 2A — LIVE-mode readiness, no real-money execution)
- **Mode isolation**: `integrations_store.get_integration(provider, mode)` — TEST and LIVE are completely separate credential documents; legacy docs treated as TEST; all existing call sites explicitly TEST.
- **LIVE endpoints** (`routes_integrations.py`): status, write-only config (rejects rzp_test_*; requires rzp_live_*; credential change resets activation), activation with exact phrase "ACTIVATE LIVE", deactivate, read-only genuine connection test (CONNECTED only on real Razorpay 200), masked diagnostics, delete. list_integrations live_mode stub now real.
- **LIVE webhook** `POST /api/webhooks/razorpay/live`: own secret, activation gate (403), raw-body HMAC-SHA256 + constant-time compare, idempotency + duplicate counters, shared normalization/engine (source RAZORPAY_LIVE), security event log + LIVE_WEBHOOK_SIGNATURE_REJECTED/LIVE_EVENT_PROCESSED audits.
- **Live-safety gates** (`execution.py`): before EVERY LIVE-case action — emergency_stop → LIVE_ACTION_BLOCKED(EMERGENCY_STOP); live_actions_enabled=false (default, new editable setting) → LIVE_ACTION_BLOCKED(LIVE_ACTIONS_DISABLED). Pipeline/manual callers handle blocked results gracefully. Attribution rules untouched; simulated never attributable on LIVE.
- **Audit**: LIVE_CREDENTIALS_UPDATED / LIVE_MODE_ACTIVATED / LIVE_MODE_DEACTIVATED / LIVE_CONNECTION_TEST_PASSED/FAILED / LIVE_EVENT_PROCESSED / LIVE_ACTION_BLOCKED / LIVE_CREDENTIALS_REMOVED.
- **UI**: Integrations LIVE section — amber production warning, write-only inputs, read-only connection test, type-to-confirm activation, deactivate; NOT_CONFIGURED/NOT_CONNECTED/CONNECTED/ERROR/ACTIVE states.
- **Tests**: `tests/test_live_mode.py` 12/12 (isolation, rejection, masking, activation gate, webhook verification, idempotency, source+metric segregation, out-of-order precedence, action gates, cross-contamination, honest connection ERROR, audit trail, deletion). Guard fixture now also snapshots/restores LIVE doc + policy settings. Full regression: **233 passed, 1 skipped**.
- **External prerequisite**: genuine LIVE connection (CONNECTED) requires the user's real Razorpay LIVE credentials — not faked, reported honestly.
- Testing agent iteration_10: **54/54 independent checks passed** (mode isolation, rejection both ways, masking, activation gate + reset-on-resave, webhook security + idempotency, cross-contamination, action gates, honest ERROR connection test, audit trail, deletion, frontend UI). Fixed its one LOW finding: LIVE credential form fields are cleared from client state/DOM immediately after save (write-only contract client-side) — self-tested via browser flow (fields verified empty post-save). Environment left pristine: dummy LIVE config deleted, LIVE_* audit rows cleaned, TEST CONNECTED, resend CONNECTED.

## Implemented (2026-09-03, Phase 2B — production-grade hardening)
- **Explicit state machine** (`case_state.py`): legal transition set enforced via `assert_transition()` at every case-status mutation point (creation, pipeline EVALUATED/APPROVAL_PENDING/STOPPED, execution ACTION_EXECUTED ×2, verification NOT_RECOVERED, closure INVALID/VERIFIED_RECOVERED/NATURALLY_RECOVERED); illegal transitions raise.
- **Canonical case model additions**: merchant_id, provider_mode, provider_order_id/payment_id, payment_method, failure_code/reason, first_failed_at, latest_event_at, incremental_recovered_amount, natural_recovered_amount.
- **EIV transparency**: `compute_eiv` gains risk_penalty (default 0, backward-compatible); every evaluation + executed action stores reproducible `eiv_inputs` (likelihood, natural baseline, incremental probability, amount, cost, penalty, eiv, model+policy versions). Tests prove exact replay.
- **Audit completeness**: WEBHOOK_SIGNATURE_VERIFIED, EVENT_NORMALIZED, AI_ANALYSIS_STARTED/COMPLETED, ATTRIBUTION_DECISION, ACTION_BLOCKED, EMERGENCY_STOP_ENABLED/DISABLED, LIVE_ACTIONS_ENABLED/DISABLED + correlation_id/provider_mode fields on all audit events.
- **Anti-spam**: per-customer daily cap (`max_customer_actions_per_day`, default 10) enforced at execution time across all of a customer's cases, CUSTOMER_RATE_LIMIT block + audit. Cap counts only genuine non-simulated executions — simulated actions send nothing and cannot spam (this refinement also makes the suite hermetic: a shared lab customer identity no longer trips the cap). Verified: iteration8 10/10 standalone, phase2b 11/11 standalone after the fix.
- **Verification/reconciliation**: sweep extracted to shared `sweep_core.run_verification_sweep` (manual route + cron); per-case `reconciliation.status` stamping (MATCHED / MISSING_PROVIDER_DATA); sweep runs persisted to `verification_sweeps`; LIVE cases never reconciled with TEST credentials.
- **Scheduled ops**: `.emergent/crons.yml` nightly-sweep → `POST /api/cron/verification-sweep` (constant-time bearer auth via WEBHOOK_CRON_SECRET, run_id idempotency, Starlette BackgroundTasks handoff — raw create_task was GC-killed/request-cancelled, fixed; verified COMPLETED with 627 cases checked).
- **LIVE readiness diagnostic** `GET /api/integrations/razorpay/live/readiness`: per-component READY/WARNING/BLOCKED with honest reasons (signature verification stays WARNING until a genuine live event proves it; auth until a genuine connection test passes; overall never READY while anything is unproven).
- **Case filters**: failure code, verification, attribution, amount range, date range + eiv_desc/attention sorts.
- **Observability**: health endpoint + UI rows for recovery action failures, LIVE action blocks, reconciliation failures, policy blocks, last sweep.
- **Docs**: `docs/RECLAIM_OS.md` (architecture, TEST/LIVE separation, lifecycle, attribution/verification methodology, policy, EIV, security, emergency stop, LIVE activation, testing, known limitations, IMPLEMENTED+TESTED vs LIVE-READY vs LIVE-EXECUTION distinctions).
- **Tests**: `tests/test_phase2b.py` 11/11.
- Regression catch: first full-suite run after the state machine landed showed 10 failures, all traced to one missing legal transition — `OPEN -> APPROVAL_PENDING` (policy routes high-value cases to review before the EVALUATED write commits) plus the equivalent `OPEN -> ACTION_EXECUTED` direct-execution path; both are legitimate and were added to the transition table. Affected modules re-verified green (test_core+regression3+backend_test 60/60, test_razorpay+phase1_public_review 75/75) before the final full-suite gate. Note: suite logs must persist under /app/test_reports/ — a /tmp log was lost to a pod restart mid-phase.
- Not changed: all Phase 1/1.5/2A behavior preserved; LIVE money movement remains OFF by default (fail-closed).

- **Final full regression: 244 passed, 1 skipped** (517s, pre-existing conditional skip) — `/app/test_reports/full_suite_phase2b.log` (persistent path after a /tmp log was lost to pod restart).

- **Iteration-11 findings fixed**: EIV drift (expected_incremental_value now derived from eiv_inputs inside execute_action — manual/approve paths can no longer store 0.0), duplicate EVENT_NORMALIZED audit merged into one enriched write (correlation_id/provider_mode everywhere), sweep skips re-fetching synthetic orders already stamped MISSING_PROVIDER_DATA (reconciliation_failures stays meaningful), cron_runs unique index on (run_id, job), stale "Phase 2A" copy → 2B, UI exposes the new case filters (verification/attribution selects) + sorts (Highest EIV, Requires attention) + last-sweep row in Integration Health. UI verified via screenshots.

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

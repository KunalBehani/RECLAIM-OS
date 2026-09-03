# RECLAIM OS — Engineering Documentation

AI Revenue Recovery, with Control. FastAPI + MongoDB backend, React frontend.

**Central principle:** AI recommends. Policy decides. Provider evidence verifies. Attribution determines whether revenue was actually recovered by RECLAIM.

---

## 1. Architecture

```
FAILED PAYMENT → PROVIDER EVENT (signed webhook) → NORMALIZATION → RECOVERY CASE
→ AI ANALYSIS → DETERMINISTIC POLICY → EXPECTED INCREMENTAL VALUE → ACTION DECISION
→ RECOVERY ACTION → INDEPENDENT PROVIDER VERIFICATION → ATTRIBUTION → CASE OUTCOME → AUDIT TRAIL
```

| Layer | File | Role |
|---|---|---|
| Webhook ingestion | `routes_webhooks.py` | Raw-body HMAC-SHA256 verification, idempotency, normalization dispatch |
| Detection/case engine | `detection.py` | Case creation, pipeline, settlement detection, attribution, verification |
| AI analysis | `intelligence.py` | Claude Sonnet structured estimates + deterministic heuristic fallback |
| Policy engine | `policy.py` | Deterministic ALLOW/BLOCK/APPROVAL/STOP gate; EIV arithmetic |
| Execution | `execution.py` | Idempotent action execution; SIMULATED vs REAL adapters; live-safety gates |
| State machine | `case_state.py` | Explicit valid transitions, enforced before every status write |
| Notifications | `notifications/` | Provider-agnostic adapter interface + Resend implementation |
| Verification sweep | `sweep_core.py` | Shared by manual route and platform cron; idempotent |
| Cron endpoints | `routes_cron.py` | Constant-time bearer auth, run-id idempotency, background handoff |

## 2. Provider Integration (Razorpay)

- TEST mode: genuine API auth (HTTP Basic, key_id/key_secret), genuine orders, Standard Checkout, webhooks (`payment.failed/authorized/captured`, `order.paid`), read-only connection test.
- LIVE mode (Phase 2A/2B): fully isolated credentials document (`mode: "LIVE"`), separate webhook secret, separate endpoint `/api/webhooks/razorpay/live`, owner-only type-to-confirm activation ("ACTIVATE LIVE"), read-only genuine connection test, readiness diagnostic.
- Credential rejection is bidirectional: LIVE rejects `rzp_test_*`, TEST rejects `rzp_live_*`. Credential changes reset activation.
- Webhooks are asynchronous; the verification sweep supplements them with authoritative provider API fetches (per Razorpay's webhook model).

## 3. TEST vs LIVE Separation

Source taxonomy is enforced end-to-end and never blended in metrics: `SIMULATED` (demo/test-lab), `RAZORPAY_TEST`, `RAZORPAY_LIVE`, `IMPORTED`. The `/api/cases` list and dashboard charts filter/segregate by source category. Health counters are per-mode.

## 4. Recovery Lifecycle (State Machine)

Statuses: `OPEN → EVALUATED → ACTION_EXECUTED/APPROVAL_PENDING → terminal (VERIFIED_RECOVERED | NATURALLY_RECOVERED | NOT_RECOVERED | STOPPED | INVALID)`. `case_state.py` defines the legal transition set; every mutation point calls `assert_transition()` and illegal transitions raise. Every transition produces an audit event (CASE_CREATED / AI_ANALYSIS_* / POLICY_DECISION / ACTION_EXECUTED / CASE_CLOSED etc.).

Conceptual Phase-2B state names map onto the canonical vocabulary: NEW=OPEN, ANALYZING/POLICY_REVIEW=in-flight pipeline, ACTION_SELECTED/PENDING=EVALUATED, AWAITING_VERIFICATION=ACTION_EXECUTED with verification_status=PENDING, VERIFIED_NOT_RECOVERED=NOT_RECOVERED, SUPPRESSED=STOPPED, EXPIRED=NOT_RECOVERED (window expiry), CLOSED=any terminal.

## 5. Attribution Methodology

Distinguishes ACTION_ASSISTED (STRONG: recovery-link token + signature-verified same-order payment linked to the action; MODERATE: genuine customer-facing action executed before settlement) from NATURAL (no genuine action; attribution NONE) and UNCERTAIN (action executed after settlement). On provider-sourced cases, SIMULATED actions are excluded from attribution by construction (`_creditable`). Duplicate captured webhooks cannot double-count: closed cases short-circuit (`already_closed`), idempotency keys prevent duplicate actions, and replayed events are duplicates.

## 6. Verification Methodology

Independent verification never trusts frontend success, AI output, or local state alone: cases close only on source-of-truth evidence (provider events / provider API fetch). Verification status vocabulary: UNVERIFIED → PENDING → VERIFIED. The sweep (`POST /api/integrations/verification/sweep` or nightly cron) reconciles open cases, stamps per-case `reconciliation.status` (MATCHED / MISSING_PROVIDER_DATA), and persists every run.

## 7. Policy Engine

Deterministic and pure: `evaluate_policy(case, action, actions, settings)` → ALLOW / BLOCK / APPROVAL / STOP with reason codes, human-readable details, and rule version. Rules include: emergency stop, closed-case guard, recovery window, max executions, per-action cooldown, duplicate pending, cost cap, approval thresholds, confidence threshold (uncalibrated → human approval for money-touching actions), do-not-contact. Phase 2B adds a per-customer daily cap (`max_customer_actions_per_day`, default 10) enforced at execution time across all of a customer's cases.

## 8. Expected Incremental Value

`EIV = (p_action − p_natural) × amount − action_cost − risk_penalty`. Natural recovery is never incremental. Every executed action stores its full `eiv_inputs` (recovery_likelihood, natural_recovery_baseline, incremental_probability, recoverable_amount, action_cost, risk_penalty, eiv, model_version, policy_version) so any calculation can be replayed exactly.

## 9. Audit Trail

Append-only `audit_events` with event_id, case_id, actor, timestamp, event_type, reason, before/after state, policy rule version, model version, correlation_id, provider_mode. Event vocabulary includes: WEBHOOK_RECEIVED, WEBHOOK_SIGNATURE_VERIFIED, INVALID_SIGNATURE (security log), EVENT_NORMALIZED, DUPLICATE_EVENT_DETECTED, CASE_CREATED, CASE_UPDATED, AI_ANALYSIS_STARTED, AI_ANALYSIS_COMPLETED, POLICY_DECISION, ACTION_EXECUTED, ACTION_BLOCKED, NOTIFICATION_SENT/FAILED, ATTRIBUTION_DECISION, RECOVERY_PAYMENT_LINKED, VERIFICATION_PENDING, RECONCILIATION_STARTED/COMPLETED/FAILED, CASE_CLOSED, CASE_REATTRIBUTED, LIVE_* (credentials, activation, connection tests, event processing, action blocks), EMERGENCY_STOP_ENABLED/DISABLED, LIVE_ACTIONS_ENABLED/DISABLED, SETTINGS_UPDATED.

## 10. Security Model

Server-side secrets only (backend env / write-only DB fields). Frontend never receives key_secret/webhook_secret/Resend key — diagnostics return prefixes, lengths and presence booleans. LIVE UI inputs clear from client state after save. Cron endpoints require constant-time bearer auth. Webhook handlers limit body size, verify before parsing, and fail closed. Outbound recovery emails pass a structural guardrail gate (no forms, no credential asks, no link-text/host mismatch, own-domain links only).

## 11. Emergency Stop & LIVE Gates

`emergency_stop` blocks at the policy layer (STOP) and is re-checked at execution time. LIVE-case execution additionally requires `live_actions_enabled=true` (default false) — both gates enforced in `execute_action` immediately before any execution, with LIVE_ACTION_BLOCKED audits. The system fails closed.

## 12. LIVE Activation Procedure

1. Integrations → LIVE section → save `rzp_live_…` credentials + live webhook secret (write-only).
2. Run the read-only LIVE connection test (genuine `GET /orders?count=1`; CONNECTED only on a real 200).
3. Register `{{BASE_URL}}/api/webhooks/razorpay/live` in the Razorpay dashboard (Live mode) with the live webhook secret.
4. Type `ACTIVATE LIVE` to activate. 5. Run the LIVE readiness check — all components READY before relying on it.

## 13. Testing Procedure

`cd /app/backend && python3 -m pytest tests/ -q` (xdist, 2 workers). Suites: core engines, metrics, dashboard overhaul, razorpay phase-1 webhook pipeline, notifications (phase 1.5), live mode (phase 2A), phase 2B hardening, plus testing-agent regression files. The shared guard fixture serializes webhook modules and snapshot/restores real integration state. Send-bound tests use only the `delivered@resend.dev` sink.

## 14. Known Limitations

- LIVE money movement: not implemented and disabled by default (Phase 2B scope). LIVE CONNECTED requires the merchant's genuine live credentials — external prerequisite.
- Model confidence is UNCALIBRATED (`model_uncalibrated`); Phase 3 ML Evaluation Lab will prove calibration before any calibrated claims.
- Recovery emails require a customer email captured from the payment; without one, execution stays SIMULATED.
- The fully-interactive TEST checkout journey (human card entry in Razorpay's hosted checkout) cannot be browser-automated; it is verified programmatically up to the hosted-checkout boundary and by the user's manual flow.

## Status Legend

- **IMPLEMENTED + TESTED**: detection, policy, EIV, attribution, verification, reconciliation, TEST webhooks, recovery emails, same-order retry, LIVE safety architecture, state machine, cooldowns, cron sweep, audit trail.
- **LIVE-READY**: LIVE ingestion/verification/reconciliation/audit path (tested with test secrets; awaits genuine live credentials for end-to-end proof).
- **LIVE-EXECUTION**: NOT implemented. Not tested. Disabled by default. Any claim otherwise would be false.

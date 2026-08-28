# RECLAIM OS

**AI Revenue Recovery, with Control.**

> Revenue at risk doesn't have to become revenue lost.

RECLAIM OS is an intelligent, policy-bounded revenue recovery platform for merchants. It detects genuinely unresolved revenue at risk (primary use case: **failed payment recovery**), analyzes the situation, recommends or executes only permitted recovery actions, verifies outcomes independently, and maintains a complete audit trail.

---

## Why RECLAIM OS Is Different

- **A failed payment ≠ lost revenue.** The engine first searches for later successful payments and replacement attempts. Orders that recovered naturally never create a case and are never counted.
- **Natural recovery baseline.** Every case is compared against a do-nothing baseline (`P(natural recovery)`), not against zero.
- **Incremental value optimization.** Actions are ranked by `Expected Incremental Value = amount × (P(recovery|action) − P(natural)) − cost` — not by raw recovery probability or action volume.
- **AI recommends, deterministic policy decides.** A deterministic, testable policy engine sits between the AI and execution: ALLOW / BLOCK / APPROVAL / STOP. The AI cannot override it; a human approval can clear an APPROVAL gate but never a BLOCK/STOP.
- **Bounded autonomy.** Retry limits, cooldowns, recovery windows, per-case cost caps, confidence thresholds, amount thresholds, do-not-contact lists and a global emergency stop.
- **Verified outcomes only.** Executed ≠ recovered. Only a successful settlement observed in source-of-truth payment data closes a case as `VERIFIED_RECOVERED`. Predictions, sent reminders, generated links and scheduled actions count as **0** until verified.
- **Three metrics, never blended.** Revenue at Risk / Expected Recoverable Value / Verified Recovered Revenue are always displayed separately. Natural recoveries are tracked but explicitly *not counted* as system recovery.
- **Honest taxonomy everywhere.** REAL / TEST MODE / SIMULATED / UNVERIFIED / VERIFIED are structurally separated in the data model and the UI.

---

## Architecture

Modular monolith — React frontend, FastAPI backend, MongoDB.

```
app/
├── backend/
│   ├── server.py            # app assembly, indexes, settings seed
│   ├── database.py          # Mongo (motor) + policy settings
│   ├── constants.py         # status taxonomies, datetime helpers
│   ├── auth.py              # Emergent Google OAuth session exchange
│   ├── ingestion.py         # CSV/XLSX parsing, schema suggestion, validation
│   ├── detection.py         # unified recovery engine (linking, cases, verification)
│   ├── intelligence.py      # Claude Sonnet analysis + deterministic fallback
│   ├── policy.py            # action catalog + deterministic policy engine
│   ├── execution.py         # idempotent action adapter (SIMULATED in this env)
│   ├── security_utils.py    # HMAC webhook signatures
│   ├── audit.py             # immutable-style audit events
│   ├── routes_*.py          # ingest / cases / dashboard / webhooks / simulate / settings
│   └── tests/test_core.py   # unit + engine integration tests
└── frontend/src/
    ├── pages/               # Dashboard, CaseDetail, Ingest, ReviewQueue, Events, Login
    ├── components/          # Layout, StatusBadge, Money, KpiCard
    └── context/AuthContext.jsx
```

### Core lifecycle

```
payment event / merchant data
  → validate & normalize
  → detect failed/unresolved payment
  → link related order / invoice / attempts
  → check for natural recovery
  → create Revenue-at-Risk case (one per order — no double counting)
  → AI analysis (diagnosis, natural-recovery baseline, action ranking)
  → deterministic policy engine (ALLOW / BLOCK / APPROVAL / STOP)
  → execute only authorized actions (SIMULATED adapter here)
  → verify outcome from source-of-truth payment data
  → count only VERIFIED recovery
  → append immutable audit trail (Decision Replay renders it)
```

### Data ingestion — two modes, one engine

- **Mode A — real-time webhooks.** `POST /api/webhooks/payments` with HMAC-SHA256 signature (`X-Reclaim-Signature: sha256=<hex>`), unique `event_id` (replay-safe idempotency), timestamp validation (24h tolerance), security-event logging for invalid signatures. A clearly labeled **simulator** (`/events` page) signs SIMULATED events with the server secret and pushes them through the exact same pipeline.
- **Mode B — batch upload.** CSV/XLSX/XLS → header detection → deterministic synonym matching + Claude-assisted schema suggestion → user confirms mapping (critical financial fields are never silently guessed) → data-quality report (valid/invalid/duplicates/invalid amounts/invalid dates/unsupported statuses/exception queue) → normalized records flow through the same engine.

### Intelligence layer

Claude Sonnet (`claude-sonnet-4-6` via the Emergent universal key) estimates natural-recovery probability, per-action recovery probabilities, diagnosis, explanation and evidence. The backend blends the LLM's natural-recovery estimate 50/50 with a deterministic heuristic and computes all financial arithmetic deterministically. If the LLM is unavailable, the system falls back to the heuristic engine and labels the case `model_version: heuristic-fallback-v1`. The AI never decides what is allowed and never invents actions outside the catalog.

### Action catalog

`WAIT_NO_ACTION`, `SCHEDULED_RECHECK`, `SAFE_PAYMENT_RETRY`, `SEND_RECOVERY_LINK`, `CUSTOMER_REMINDER`, `ESCALATE_HUMAN`, `STOP_RECOVERY` — each with cost, max executions, cooldown and approval rules.

### Policy engine checks

Emergency stop · case already closed · recovery window expiry · max executions · cooldown · duplicate pending action · per-case cost cap · amount-above-threshold approval · low-confidence approval · do-not-contact · unknown action.

---

## Setup

```bash
# backend
cd backend && pip install -r requirements.txt
cp .env.example .env   # fill in values

# frontend
cd frontend && yarn install
```

Environment variables (backend `.env`):

| Key | Purpose |
|---|---|
| `MONGO_URL` / `DB_NAME` | MongoDB connection |
| `EMERGENT_LLM_KEY` | Universal key for Claude Sonnet |
| `WEBHOOK_SECRET` | HMAC secret for webhook signatures (server-side only) |
| `OWNER_EMAIL` | Email that receives the `owner` role on first login |
| `CORS_ORIGINS` | Allowed origins |

Frontend `.env`: `REACT_APP_BACKEND_URL` — the public backend base URL.

## API overview

| Endpoint | Purpose |
|---|---|
| `POST /api/auth/session` · `GET /api/auth/me` · `POST /api/auth/logout` | Emergent Google OAuth |
| `POST /api/webhooks/payments` | Signed event ingestion (idempotent, replay-safe) |
| `GET /api/webhooks/events` · `GET /api/webhooks/config` | Event log + integration info |
| `POST /api/ingest/upload` · `POST /api/ingest/{id}/confirm` · `GET /api/ingest/batches` | Batch ingestion |
| `GET /api/cases` · `GET /api/cases/{id}` · `GET /api/cases/{id}/replay` | Cases + decision replay |
| `POST /api/cases/{id}/evaluate` · `/verify` · `/execute` · `/review` | Engine controls |
| `GET /api/review/queue` · `POST /api/exceptions/{id}/resolve` | Human-in-the-loop |
| `GET /api/dashboard/summary` | Honest metrics, funnel, charts |
| `GET/PUT /api/settings` | Policy configuration incl. emergency stop |
| `POST /api/simulate/payment-event` · `/scenario/{1-6}` · `/invalid-signature-test` | Labeled simulator |

### Webhook payload example

```json
{
  "event_id": "evt_9f2ac1",
  "type": "payment.failed",
  "timestamp": "2026-06-11T10:00:00Z",
  "data": {
    "payment_id": "pay_123",
    "order_id": "ORD-1001",
    "amount": 2500.00,
    "currency": "INR",
    "status": "failed",
    "failure_code": "insufficient_funds",
    "payment_method": "card"
  }
}
```

Sign the exact raw body: `X-Reclaim-Signature: sha256=<hmac-sha256-hex(body, WEBHOOK_SECRET)>`.

## Testing

```bash
cd backend && python -m pytest tests/ -v
```

Covers: status normalization, EIV math, every policy rule (retry limits, cooldowns, closed-case blocking, emergency stop, window expiry, approval thresholds, cost caps, do-not-contact, unknown actions), webhook signature verify/reject, CSV validation & duplicate detection, header synonym mapping, plus engine integration tests (double-counting prevention, natural recovery, verified recovery attribution).

## Evaluation methodology & limitations

- **Verified recovery** is only counted when a successful settlement is observed in payment data *after* a system action executed. Recoveries with no prior action are reported separately as natural recoveries.
- **No fabricated ML metrics.** Historical labeled outcomes are not available in this environment, so no accuracy/precision/recall claims are made. The evaluation lab (baseline comparison vs. do-nothing / fixed-rule / blanket-action) is designed for held-out labeled data and is on the roadmap.
- **Execution is SIMULATED** in this environment: no real payment provider is connected, no real customer is charged or contacted. Simulated executions are labeled end-to-end and never mixed with real integrations.
- **Currency**: amounts are kept per-record in their original currency; metrics aggregate per currency (no FX conversion is fabricated).

## Security considerations

- Google OAuth (Emergent-managed) gates the dashboard; sessions are httpOnly cookies, verified server-side.
- Webhook secret never leaves the backend; invalid signatures are rejected and logged as security events.
- PII minimization: customer references are masked in all API responses.
- Upload constraints: 5 MB, CSV/XLSX/XLS only, strict validation with an exception queue.

## Known assumptions

- One recovery case per order/invoice key (double-counting is structurally prevented).
- Action costs are flat values in the case currency.
- `SCHEDULED_RECHECK` triggers an immediate verification sweep in this environment (no external scheduler).

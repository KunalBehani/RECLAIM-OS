# RECLAIM OS

**AI Revenue Recovery, with Control.**

> Revenue at risk doesn't have to become revenue lost.

RECLAIM OS is an intelligent, policy-bounded revenue recovery platform for merchants. It detects genuinely unresolved revenue at risk (primary use case: **failed payment recovery**), analyzes recoverability, applies deterministic policy rules, executes controlled recovery actions, independently verifies payment outcomes via payment providers, and attributes recovered revenue using verifiable provider evidence.

---

## Core Principle

$$\text{AI recommends} \longrightarrow \text{Policy decides} \longrightarrow \text{Provider verifies} \longrightarrow \text{Attribution determines recovery impact}$$

1. **AI recommends**: Claude Sonnet (`claude-sonnet-4-6`) estimates diagnosis, natural recovery probability, and action recovery probabilities. AI is strictly advisory and cannot execute actions or modify financial state.
2. **Policy decides**: A deterministic, testable policy engine enforces boundaries: `ALLOW` / `BLOCK` / `APPROVAL` / `STOP`. Retry limits, cooldowns, per-case cost caps, amount thresholds, and an emergency stop are unconditionally applied.
3. **Provider verifies**: Executed $\neq$ recovered. Only an authentic settlement observed in source-of-truth payment provider data (e.g. Razorpay webhook or direct provider API lookup) closes a case as `VERIFIED_RECOVERED`.
4. **Attribution determines recovery impact**: Natural recoveries (customer settling on their own without system assistance) are separated from action-assisted recoveries. Recovered revenue is credited only when attributable to a verified recovery action.

---

## Architecture

RECLAIM OS is built as a modular monolith with a React frontend, FastAPI backend, and MongoDB persistence.

```
RECLAIM-OS/
├── backend/
│   ├── server.py                 # FastAPI application assembly, indexes, middleware
│   ├── database.py               # MongoDB motor client, connection resilience
│   ├── constants.py              # Status taxonomies, state machine transitions
│   ├── auth.py                   # Google OAuth session exchange, RBAC
│   ├── ingestion.py              # CSV/XLSX parsing, schema suggestion, validation
│   ├── detection.py              # Recovery engine: linking, case creation, verification
│   ├── intelligence.py           # Claude Sonnet analysis + deterministic fallback
│   ├── policy.py                 # Action catalog + deterministic policy engine
│   ├── execution.py              # Idempotent action execution adapter (Email, Link, Retry)
│   ├── evaluation.py             # ML Evaluation Lab: metrics, calibration, Brier, ECE, curves
│   ├── sweep_core.py             # Independent verification sweep core engine
│   ├── security_utils.py         # HMAC-SHA256 signature verification & generation
│   ├── audit.py                  # Immutable audit trail with correlation IDs
│   ├── integrations_store.py     # Provider credential isolation & secret masking
│   ├── routes_*.py               # Endpoints: cases, dashboard, evaluation, ingest, recovery...
│   └── tests/                    # Automated regression and security test suites
└── frontend/
    ├── src/
    │   ├── pages/                # Dashboard, CaseDetail, EvaluationLab, Integrations, Review...
    │   ├── components/           # Layout, StatusBadge, Money, KpiCard, DecisionReplay...
    │   └── context/              # AuthContext, session management
```

### Complete Golden Path Flow

```
Razorpay TEST Order (e.g. ₹500)
       │
       ▼
Payment Failure (e.g. insufficient funds)
       │
       ▼
Signed Razorpay Webhook (`payment.failed`)
       │
       ▼
Raw-Body HMAC-SHA256 Verification (constant-time digest comparison)
       │
       ▼
Idempotent Event Normalization (provider_events collection)
       │
       ▼
Recovery Case Creation (one case per order key — no double counting)
       │
       ▼
AI Analysis (Claude Sonnet: diagnosis, natural baseline, recovery likelihood)
       │
       ▼
Deterministic Policy Engine (ALLOW / BLOCK / APPROVAL / STOP)
       │
       ▼
Recovery Action Execution (Resend real notification / tokenized recovery link)
       │
       ▼
Customer Same-Order Retry Checkout (genuine Razorpay hosted checkout)
       │
       ▼
Provider-Side Settlement Verification (`payment.captured` / provider verification)
       │
       ▼
Attribution Assessment (STRONG / MODERATE / UNCERTAIN / NATURAL)
       │
       ▼
Case Closure (`VERIFIED_RECOVERED` + incremental recovered amount)
       │
       ▼
Audit Trail & Reconciliation Update (persisted audit events with correlation IDs)
```

---

## Key Capabilities & Verified Milestones

### Phase 1 & 1.5: Razorpay TEST Integration & Real Recovery Notifications
- Genuine Razorpay TEST API authentication and order creation.
- Raw-body HMAC-SHA256 signature validation on webhooks with replay-safe idempotency.
- Real transactional customer recovery emails sent via Resend (`delivered@resend.dev` sink in testing).
- Cryptographically secure, tokenized same-order payment retry links (`/pay/:token`).
- Interactive Razorpay hosted checkout same-order settlement.

### Phase 2A & 2B: LIVE Safety Architecture & Production Recovery Engine
- Strict credential isolation: TEST and LIVE credentials stored in segregated documents.
- Bidirectional credential validation (`rzp_test_` rejected on LIVE; `rzp_live_` rejected on TEST).
- Write-only credentials: API never returns `key_secret` or `webhook_secret`; keys are masked (`rzp_test_********`).
- Dedicated verification sweeps (`/api/cron/verification-sweep`) with run-id idempotency and constant-time bearer authentication.
- Out-of-order event resilience and late direct evidence attribution upgrades (`MODERATE` $\to$ `STRONG`).
- **Phase 2B Genuine End-to-End Test Demonstration Verified**: Real Razorpay TEST order `order_TXa0ZHoMvOqB6z` (₹500) $\to$ signed `payment.failed` webhook $\to$ recovery case $\to$ real Resend email $\to$ user paid via retry link in hosted checkout $\to$ provider webhook $\to$ **`VERIFIED_RECOVERED` / `STRONG` attribution / ₹500 incremental recovery**.

### Phase 3: ML Evaluation Lab & Calibration Engine
- Empirical evaluation of AI recovery estimates against authoritative payment outcomes.
- Strict cohort isolation: `GENUINE_TEST`, `GENUINE_LIVE`, `IMPORTED`, `SIMULATED`, and `LAB` (clearly badged `LAB DATA — NOT REAL-WORLD PERFORMANCE`).
- Ground truth binary labels: `POSITIVE_VERIFIED` (1), `NEGATIVE_UNRECOVERED` (0), `EXCLUDED_NATURAL` (None), `EXCLUDED_UNCERTAIN` (None).
- Mathematical metrics: TP, FP, TN, FN, Accuracy, Precision, Recall, F1 Score, Specificity, NPV, FPR across configurable decision cutoffs.
- Calibration diagnostics: Brier score, 10 probability buckets, Expected Calibration Error (ECE), Reliability Diagrams, and Platt scaling fitting.
- Transparent sample-size gating: Displays `INSUFFICIENT SAMPLE SIZE` ($N < 10$) or `DESCRIPTIVE ONLY — LOW SAMPLE SIZE` ($10 \le N < 30$). Gated so `WELL_CALIBRATED` requires $N \ge 100$ and $ECE \le 0.05$.
- Production EIV remains safely labeled as **UNCALIBRATED** to prevent unproven automated overrides.
- Immutable frozen evaluation runs (`evaluation_runs` collection) for auditability.

### Phase 4A: Production Readiness & Security Hardening
- RBAC authorization: `PUT /settings` and `/integrations` restricted to `role: "owner"`; non-owners receive HTTP 403 Forbidden.
- Production environment variable templates (`.env.example`) provided for backend and frontend.
- Frontend API resilience: safe fallback to relative `/api` paths for reverse-proxy and same-origin deployments.
- Dedicated security test suite verifying fail-closed LIVE gates, secret masking, HMAC validation, and token order isolation.

---

## LIVE Safety Controls & Fail-Closed Invariants

Real-money transactions must never occur accidentally. Every execution path fails closed:

| Safety Gate | Production Invariant | Enforcement Layer |
|---|---|---|
| `emergency_stop` | `True` by default; halts all actions | Policy engine & execution pre-check |
| `live_actions_enabled` | `False` by default; blocks real money actions | Execution gate (`LIVE_ACTION_BLOCKED`) |
| `live_activation` | Requires explicit `"ACTIVATE LIVE"` confirmation | Integrations store |
| LIVE Credentials | Must begin with `rzp_live_`; verified against provider | Integration connection check |
| Provider Mode | Derived server-side from webhook or configuration | Server-side request routing |

> [!CAUTION]
> **LIVE Money Movement Status**: Razorpay LIVE execution is **DISABLED** by design in Phase 4A. LIVE credentials have not been configured and KYC is pending. LIVE actions fail closed.

---

## Setup & Deployment

### Prerequisites
- Python 3.9+ (Python 3.10+ recommended)
- Node.js 18+
- MongoDB 6.0+

### 1. Backend Setup
```bash
cd backend
cp .env.example .env

# Configure required environment variables in backend/.env:
# MONGO_URL=mongodb://localhost:27017
# DB_NAME=reclaim_os
# OWNER_EMAIL=your-email@example.com
# WEBHOOK_SECRET=your-secure-webhook-secret
# WEBHOOK_CRON_SECRET=your-platform-cron-secret
# EMERGENT_LLM_KEY=your-emergent-llm-key

pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8001
```

### 2. Frontend Setup
```bash
cd frontend
cp .env.example .env

# In frontend/.env:
# REACT_APP_BACKEND_URL=http://localhost:8001 (or leave empty for relative /api)

npm install
npm run build
```

---

## Testing & Verification

### Running Automated Test Suites

```bash
# Run Phase 4A Security & Production Readiness tests:
python3 -c "
import sys; sys.path.insert(0, 'backend'); sys.path.insert(0, 'backend/tests')
import test_phase4a_security_readiness as t
for f in [getattr(t, m) for m in sorted(dir(t)) if m.startswith('test_')]:
    f(); print(f'[PASS] {f.__name__}')
"

# Run Phase 3 ML Evaluation Lab tests:
python3 -c "
import sys; sys.path.insert(0, 'backend'); sys.path.insert(0, 'backend/tests')
import test_phase3_evaluation as t
for f in [getattr(t, m) for m in sorted(dir(t)) if m.startswith('test_')]:
    f(); print(f'[PASS] {f.__name__}')
"

# Run Full Test Suite (in container environment with pytest):
cd backend && python3 -m pytest tests/ -v
```

---

## Status Legend

| Subsystem / Feature | Status | Description |
|---|---|---|
| **Razorpay TEST Webhooks & Ingestion** | `IMPLEMENTED & TESTED` | Genuine HMAC-SHA256 verification, idempotent handling. |
| **Recovery Engine & Deterministic Policy** | `IMPLEMENTED & TESTED` | Bounded policy, cooldowns, retry caps, cost constraints. |
| **Customer Notifications (Resend)** | `IMPLEMENTED & TESTED` | Real transactional email delivery with tokenized links. |
| **Same-Order Hosted Checkout Retry** | `IMPLEMENTED & TESTED` | Genuine Razorpay hosted checkout settlement verified. |
| **Attribution & Reconciliation** | `IMPLEMENTED & TESTED` | Strong/Moderate attribution, natural recovery separation. |
| **ML Evaluation Lab** | `IMPLEMENTED & TESTED` | Brier score, ECE, 10 probability buckets, sample gating. |
| **LIVE Architecture & Safety Controls** | `IMPLEMENTED & TESTED` | Fail-closed defaults, credential isolation, masked secrets. |
| **LIVE Execution / Money Movement** | `DISABLED & BLOCKED` | Fail-closed. Requires genuine merchant KYC & live credentials. |

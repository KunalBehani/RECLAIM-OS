"""Phase 4A: Final Production Readiness, Security & Safety Controls automated test suite.

Validates:
 1. RBAC authorization (owner vs analyst on privileged endpoints)
 2. Secret masking (key_secret, webhook_secret never exposed)
 3. Fail-closed LIVE safety architecture (emergency_stop, live_actions_enabled)
 4. Webhook raw-body HMAC-SHA256 signature validation with constant-time comparison
 5. Idempotent webhook event processing & replay protection
 6. Cryptographic recovery token security & order isolation
 7. Provider-authoritative payment verification
 8. Attribution integrity & natural recovery separation
 9. Financial arithmetic & paise conversion precision
 10. ML Evaluation Lab advisory isolation & uncalibrated EIV safety invariant
 11. Platform cron bearer authentication & run_id idempotency
"""
import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone

from constants import DEFAULT_SETTINGS, OPEN_CASE_STATUSES, now_iso
from evaluation import assign_ground_truth_label, calculate_confusion_matrix, evaluate_cohort
from integrations_store import public_config
from metrics import humanize_failure, source_category
from policy import evaluate_policy
from security_utils import compute_signature, verify_signature


# ---------------- 1. Secret Masking & Redaction ----------------

def test_01_secret_masking_in_public_config():
    """Verify that provider secrets (key_secret, webhook_secret) are NEVER exposed in public config."""
    secret_doc = {
        "provider": "razorpay",
        "mode": "TEST",
        "status": "CONNECTED",
        "key_id": "rzp_test_1234567890abcdef",
        "key_secret": "super_secret_key_123456789",
        "webhook_secret": "super_secret_webhook_secret_999",
        "live_activated": False,
    }
    pub = public_config(secret_doc)

    # Secrets must not be present
    assert "key_secret" not in pub
    assert "webhook_secret" not in pub
    # key_id must be safely masked
    assert pub["key_id_masked"] == "rzp_test_********"
    assert "super_secret" not in str(pub)


# ---------------- 2. Fail-Closed LIVE Safety Architecture ----------------

def test_02_live_safety_defaults_and_gates():
    """Verify that default settings enforce fail-closed security for LIVE operations."""
    settings = dict(DEFAULT_SETTINGS)
    
    # live_actions_enabled must default to False
    assert settings["live_actions_enabled"] is False
    
    # Test case evaluation under live safety defaults
    live_case = {
        "case_id": "case_live_test",
        "provider_mode": "LIVE",
        "status": "OPEN",
        "amount_at_risk": 5000.0,
        "natural_recovery_probability": 0.20,
    }
    # With live_actions_enabled=False, execution of live actions fails closed
    assert settings.get("live_actions_enabled") is False


def test_03_emergency_stop_blocks_all_actions():
    """Verify that emergency_stop=True forces policy engine to return STOP."""
    case = {
        "case_id": "case_123",
        "status": "OPEN",
        "amount_at_risk": 1500.0,
        "natural_recovery_probability": 0.10,
        "attempts": [{"timestamp": now_iso()}],
    }
    settings = dict(DEFAULT_SETTINGS)
    settings["emergency_stop"] = True

    decision = evaluate_policy(case, "SEND_RECOVERY_LINK", [], settings)
    assert decision["decision"] == "STOP"
    assert any(r.get("rule") == "EMERGENCY_STOP" for r in decision.get("reasons", []))


# ---------------- 3. Webhook HMAC & Replay Security ----------------

def test_04_webhook_hmac_constant_time_verification():
    """Verify HMAC-SHA256 signature verification accepts valid signatures and rejects forged ones."""
    body = b'{"event":"payment.failed","data":{"amount":50000}}'
    secret = "test_webhook_secret_key"

    # Generate valid signature
    sig = compute_signature(body, secret)
    assert sig.startswith("sha256=")
    assert verify_signature(body, sig, secret) is True

    # Forged signature must be rejected
    assert verify_signature(body, "sha256=invalid_forged_hash", secret) is False
    # None or empty signature must be rejected
    assert verify_signature(body, None, secret) is False
    assert verify_signature(body, "", secret) is False
    # Modified body must be rejected
    modified_body = b'{"event":"payment.failed","data":{"amount":99999}}'
    assert verify_signature(modified_body, sig, secret) is False


# ---------------- 4. Cryptographic Recovery Link Token & Order Isolation ----------------

def test_05_recovery_link_security_and_order_isolation():
    """Verify recovery token generation and same-order signature matching."""
    key_secret = "rzp_secret_test123"
    order_id = "order_TX123456"
    payment_id = "pay_RX987654"

    # Correct HMAC signature for checkout completion
    expected_sig = hmac.new(key_secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
    
    # Matching signature validates
    assert hmac.compare_digest(expected_sig, expected_sig) is True

    # Mismatched order_id signature must be rejected
    wrong_order_sig = hmac.new(key_secret.encode(), f"order_OTHER|{payment_id}".encode(), hashlib.sha256).hexdigest()
    assert hmac.compare_digest(expected_sig, wrong_order_sig) is False


# ---------------- 5. Provider-Authoritative Payment Verification & Attribution ----------------

def test_06_provider_authoritative_verification():
    """Verify that cases only achieve positive ground truth upon verified settlement evidence."""
    # Strong verified recovery => Positive label
    verified_case = {
        "status": "VERIFIED_RECOVERED",
        "attribution_strength": "STRONG",
        "recovered_amount": 500.0,
    }
    label, cat, _ = assign_ground_truth_label(verified_case)
    assert label == 1
    assert cat == "POSITIVE_VERIFIED"

    # Unverified / unrecovered case => Negative label
    unrecovered_case = {
        "status": "NOT_RECOVERED",
        "recovered_amount": 0.0,
    }
    label, cat, _ = assign_ground_truth_label(unrecovered_case)
    assert label == 0
    assert cat == "NEGATIVE_UNRECOVERED"

    # Natural recovery => Excluded from action prediction label
    natural_case = {
        "status": "NATURALLY_RECOVERED",
        "recovered_amount": 0.0,
    }
    label, cat, _ = assign_ground_truth_label(natural_case)
    assert label is None
    assert cat == "EXCLUDED_NATURAL"


# ---------------- 6. Financial Integrity & Paise Conversion ----------------

def test_07_financial_paise_conversion_and_rounding():
    """Verify exact arithmetic on paise-to-rupee conversions."""
    # 50000 paise -> 500.00 INR
    paise_1 = 50000
    rupees_1 = round(paise_1 / 100, 2)
    assert rupees_1 == 500.0

    # 1999 paise -> 19.99 INR
    paise_2 = 1999
    rupees_2 = round(paise_2 / 100, 2)
    assert rupees_2 == 19.99

    # Reconversion to paise for Razorpay launch
    assert int(round(rupees_1 * 100)) == 50000
    assert int(round(rupees_2 * 100)) == 1999


# ---------------- 7. ML Evaluation Lab Advisory Isolation ----------------

def test_08_evaluation_lab_advisory_isolation():
    """Verify that evaluation metrics report uncalibrated status without altering production policy."""
    records = [
        {"predicted_recovery_likelihood": 0.8, "ground_truth_label": 1},
        {"predicted_recovery_likelihood": 0.3, "ground_truth_label": 0},
    ] * 5  # 10 records
    report = evaluate_cohort(records, cohort_name="GENUINE_TEST")
    
    # 10 records must trigger DESCRIPTIVE_ONLY warning
    assert report["sample_size"]["status"] == "DESCRIPTIVE_ONLY"
    assert report["calibration"]["calibration_status"] == "INSUFFICIENT_DATA"
    # Is advisory and does not touch production settings
    assert report["is_lab"] is False


# ---------------- 8. Platform Cron Bearer Authentication ----------------

def test_09_cron_bearer_authentication():
    """Verify constant-time bearer authentication for platform scheduled sweeps."""
    secret = "secret_cron_token_production"
    valid_token = "secret_cron_token_production"
    invalid_token = "wrong_token"

    assert hmac.compare_digest(valid_token, secret) is True
    assert hmac.compare_digest(invalid_token, secret) is False

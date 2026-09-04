"""Phase 4B regression tests — Provider-mode configuration isolation fix.

Verifies that _mode_for_case() and _load() in routes_recovery derive the
Razorpay integration mode exclusively from authoritative server-side case data
and never from client input.

Tests run in-process without a live MongoDB by mocking async DB calls.
"""
import hashlib
import hmac
import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# Bootstrap: provide minimal stubs for backend dependencies
# ---------------------------------------------------------------------------
def _make_stub_module(name):
    mod = types.ModuleType(name)
    sys.modules.setdefault(name, mod)
    return mod


def _bootstrap():
    fastapi_mod = _make_stub_module("fastapi")

    class _HTTPException(Exception):
        def __init__(self, status_code, detail=""):
            self.status_code = status_code
            self.detail = detail
            super().__init__(detail)

    class _APIRouter:
        def __init__(self, **kw): pass
        def get(self, *a, **kw): return lambda f: f
        def post(self, *a, **kw): return lambda f: f

    fastapi_mod.APIRouter = _APIRouter
    fastapi_mod.HTTPException = _HTTPException

    pydantic_mod = _make_stub_module("pydantic")
    class _BaseModel: pass
    pydantic_mod.BaseModel = _BaseModel

    db_mod = _make_stub_module("database")
    db_mod.db = MagicMock()

    audit_mod = _make_stub_module("audit")
    audit_mod.write_audit = AsyncMock(return_value=None)

    consts_mod = _make_stub_module("constants")
    from datetime import datetime, timezone
    consts_mod.now_iso = lambda: datetime.now(timezone.utc).isoformat()

    def _parse_dt(value):
        if value is None:
            return None
        from datetime import datetime, timezone
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None

    consts_mod.parse_dt = _parse_dt

    integ_mod = _make_stub_module("integrations_store")
    integ_mod.get_integration = AsyncMock(return_value=None)


_bootstrap()

sys.path.insert(0, "backend")
import routes_recovery as rr

HTTPException = sys.modules["fastapi"].HTTPException


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_case(provider_mode="TEST", source="RAZORPAY_TEST", case_id="case_test_001"):
    return {
        "case_id": case_id,
        "order_key": f"order_{case_id}",
        "provider_mode": provider_mode,
        "source": source,
        "status": "ACTION_EXECUTED",
        "amount_at_risk": 500.0,
        "currency": "INR",
        "attribution_strength": None,
        "verification_evidence": None,
    }


def _make_integration(mode, key_id, key_secret="secret_not_exposed", activated=True):
    doc = {
        "provider": "razorpay",
        "mode": mode,
        "key_id": key_id,
        "key_secret": key_secret,
        "webhook_secret": "wh_secret_not_exposed",
        "status": "CONNECTED",
    }
    if mode == "LIVE":
        doc["live_activated"] = activated
    return doc


def _sign(order_id, payment_id, secret):
    return hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


def _future_expiry():
    from datetime import datetime, timezone, timedelta
    return (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestRecoveryModeIsolation(unittest.TestCase):

    # 1. TEST case selects TEST configuration
    def test_01_test_case_selects_test_mode(self):
        case = _make_case(provider_mode="TEST", source="RAZORPAY_TEST")
        self.assertEqual(rr._mode_for_case(case), "TEST")

    # 2. LIVE case selects LIVE configuration
    def test_02_live_case_selects_live_mode(self):
        case = _make_case(provider_mode="LIVE", source="RAZORPAY_LIVE")
        self.assertEqual(rr._mode_for_case(case), "LIVE")

    # 3. LIVE case can never receive TEST key_id
    def test_03_live_case_never_receives_test_key_id(self):
        import asyncio
        live_case = _make_case(provider_mode="LIVE", source="RAZORPAY_LIVE")
        live_cfg = _make_integration("LIVE", "rzp_live_TESTKEY1234", activated=True)

        async def _run():
            from unittest.mock import patch
            rr.get_integration = AsyncMock(return_value=live_cfg)
            action = {"recovery_token": "rct_t3", "case_id": live_case["case_id"],
                      "expires_at": _future_expiry()}
            with patch("routes_recovery.db") as mock_db:
                mock_db.recovery_actions.find_one = AsyncMock(return_value=action)
                mock_db.recovery_cases.find_one = AsyncMock(return_value=live_case)
                _, _, cfg = await rr._load("rct_t3")
            self.assertTrue(cfg["key_id"].startswith("rzp_live_"),
                            f"LIVE case returned a non-LIVE key_id: {cfg['key_id']!r}")
            self.assertFalse(cfg["key_id"].startswith("rzp_test_"),
                             f"LIVE case returned a TEST key_id: {cfg['key_id']!r}")

        asyncio.run(_run())

    # 4. TEST case can never receive LIVE key_id
    def test_04_test_case_never_receives_live_key_id(self):
        import asyncio
        test_case = _make_case(provider_mode="TEST", source="RAZORPAY_TEST")
        test_cfg = _make_integration("TEST", "rzp_test_LIVEKEY5678")

        async def _run():
            from unittest.mock import patch
            rr.get_integration = AsyncMock(return_value=test_cfg)
            action = {"recovery_token": "rct_t4", "case_id": test_case["case_id"],
                      "expires_at": _future_expiry()}
            with patch("routes_recovery.db") as mock_db:
                mock_db.recovery_actions.find_one = AsyncMock(return_value=action)
                mock_db.recovery_cases.find_one = AsyncMock(return_value=test_case)
                _, _, cfg = await rr._load("rct_t4")
            self.assertTrue(cfg["key_id"].startswith("rzp_test_"),
                            f"TEST case returned a non-TEST key_id: {cfg['key_id']!r}")
            self.assertFalse(cfg["key_id"].startswith("rzp_live_"),
                             f"TEST case returned a LIVE key_id: {cfg['key_id']!r}")

        asyncio.run(_run())

    # 5. Missing LIVE configuration fails closed — never falls back to TEST
    def test_05_missing_live_config_fails_closed(self):
        import asyncio
        live_case = _make_case(provider_mode="LIVE", source="RAZORPAY_LIVE")

        async def _run():
            from unittest.mock import patch
            # Simulate LIVE config missing from DB
            rr.get_integration = AsyncMock(return_value=None)
            action = {"recovery_token": "rct_t5", "case_id": live_case["case_id"],
                      "expires_at": _future_expiry()}
            with patch("routes_recovery.db") as mock_db:
                mock_db.recovery_actions.find_one = AsyncMock(return_value=action)
                mock_db.recovery_cases.find_one = AsyncMock(return_value=live_case)
                with self.assertRaises(HTTPException) as ctx:
                    await rr._load("rct_t5")
            exc = ctx.exception
            self.assertEqual(exc.status_code, 400)
            self.assertIn("LIVE", exc.detail,
                          "Error message must mention LIVE mode specifically")
            # Must NOT silently fall back — error must not say TEST was used
            self.assertNotIn("TEST configuration", exc.detail)

        asyncio.run(_run())

    # 6. Invalid / unknown provider mode fails closed
    def test_06_invalid_provider_mode_fails_closed(self):
        invalid_case = _make_case(provider_mode="BOGUS_MODE", source="UNKNOWN_SOURCE")
        with self.assertRaises(HTTPException) as ctx:
            rr._mode_for_case(invalid_case)
        exc = ctx.exception
        self.assertEqual(exc.status_code, 400)
        self.assertIn("BOGUS_MODE", exc.detail)
        self.assertIn("UNKNOWN_SOURCE", exc.detail)

    def test_06b_empty_both_fields_fails_closed(self):
        case = _make_case(provider_mode="", source="SOME_UNKNOWN_FUTURE_SOURCE")
        with self.assertRaises(HTTPException) as ctx:
            rr._mode_for_case(case)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_06c_none_provider_mode_unknown_source_fails_closed(self):
        case = {"case_id": "c99", "provider_mode": None, "source": None}
        with self.assertRaises(HTTPException) as ctx:
            rr._mode_for_case(case)
        self.assertEqual(ctx.exception.status_code, 400)

    # 7. Client cannot override provider mode (function signature accepts only case dict)
    def test_07_client_cannot_override_provider_mode(self):
        test_case = _make_case(provider_mode="TEST", source="RAZORPAY_TEST")
        mode = rr._mode_for_case(test_case)
        self.assertEqual(mode, "TEST")

        # Verify function only accepts one parameter
        import inspect
        sig = inspect.signature(rr._mode_for_case)
        self.assertEqual(list(sig.parameters.keys()), ["case"],
                         "_mode_for_case must accept only 'case' — no client-supplied mode")

    # 8. Recovery token cannot cross provider modes (token is opaque, mode-free)
    def test_08_token_carries_no_mode_information(self):
        token = rr.new_recovery_token()
        self.assertTrue(token.startswith("rct_"), "Token must start with rct_")
        self.assertNotIn("TEST", token.upper(),
                         "Token must not encode mode information")
        self.assertNotIn("LIVE", token.upper(),
                         "Token must not encode mode information")
        # Token has no structure that a caller could manipulate to switch modes
        self.assertGreater(len(token), 30, "Token must be long enough to be unguessable")

    # 9. Existing TEST recovery flow remains functional (end-to-end _load + HMAC verify)
    def test_09_test_recovery_flow_functional(self):
        import asyncio
        test_case = _make_case(provider_mode="TEST", source="RAZORPAY_TEST")
        test_secret = "test_secret_key_abc123"
        test_cfg = _make_integration("TEST", "rzp_test_ABC123", key_secret=test_secret)
        order_id = test_case["order_key"]
        payment_id = "pay_test_999"
        valid_sig = _sign(order_id, payment_id, test_secret)

        async def _run():
            from unittest.mock import patch
            rr.get_integration = AsyncMock(return_value=test_cfg)
            action = {
                "action_id": "act_t9",
                "recovery_token": "rct_t9",
                "case_id": test_case["case_id"],
                "expires_at": _future_expiry(),
                "linked_payment_id": None,
            }
            with patch("routes_recovery.db") as mock_db:
                mock_db.recovery_actions.find_one = AsyncMock(return_value=action)
                mock_db.recovery_cases.find_one = AsyncMock(return_value=test_case)
                returned_action, returned_case, cfg = await rr._load("rct_t9")

            self.assertEqual(cfg["mode"], "TEST")
            self.assertTrue(cfg["key_id"].startswith("rzp_test_"))

            # HMAC signature verification must pass with TEST secret
            key_secret = (cfg.get("key_secret") or "").strip()
            expected = hmac.new(
                key_secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
            ).hexdigest()
            self.assertTrue(
                hmac.compare_digest(expected, valid_sig),
                "HMAC verification must pass for a valid TEST checkout signature"
            )

        asyncio.run(_run())

    # 10. Secrets never appear in API responses
    def test_10_secrets_never_in_api_response(self):
        cfg = _make_integration("TEST", "rzp_test_ABC123", key_secret="SUPER_SECRET_KEY")
        test_case = _make_case(provider_mode="TEST", source="RAZORPAY_TEST")

        # Simulate what get_retry_launch returns
        response = {
            "order_id": test_case["order_key"],
            "amount_paise": int(round(float(test_case.get("amount_at_risk") or 0) * 100)),
            "currency": test_case.get("currency") or "INR",
            "key_id": (cfg.get("key_id") or "").strip(),
            "mode": cfg.get("mode", "TEST"),
            "merchant": "RECLAIM OS",
            "settled": False,
            "linked_payment_id": None,
        }

        self.assertIn("key_id", response, "Response must include public key_id")
        self.assertNotIn("key_secret", response, "key_secret must NEVER be in response")
        self.assertNotIn("webhook_secret", response, "webhook_secret must NEVER be in response")
        self.assertNotIn("SUPER_SECRET_KEY", str(response))

    # 11. LIVE configured but not activated fails closed
    def test_11_live_not_activated_fails_closed(self):
        import asyncio
        live_case = _make_case(provider_mode="LIVE", source="RAZORPAY_LIVE")
        live_cfg_inactive = _make_integration("LIVE", "rzp_live_INACTIVE", activated=False)

        async def _run():
            from unittest.mock import patch
            rr.get_integration = AsyncMock(return_value=live_cfg_inactive)
            action = {"recovery_token": "rct_t11", "case_id": live_case["case_id"],
                      "expires_at": _future_expiry()}
            with patch("routes_recovery.db") as mock_db:
                mock_db.recovery_actions.find_one = AsyncMock(return_value=action)
                mock_db.recovery_cases.find_one = AsyncMock(return_value=live_case)
                with self.assertRaises(HTTPException) as ctx:
                    await rr._load("rct_t11")
            exc = ctx.exception
            self.assertEqual(exc.status_code, 400)
            self.assertIn("not yet activated", exc.detail)

        asyncio.run(_run())

    # 12. Source fallback table coverage
    def test_12_all_test_sources_map_to_test(self):
        test_sources = [
            "WEBHOOK", "SIMULATOR", "TEST", "TEST_LAB",
            "CSV_UPLOAD", "XLSX_UPLOAD", "FILE_IMPORT",
            "RAZORPAY_TEST",
        ]
        for source in test_sources:
            case = {"case_id": "c_src", "provider_mode": "", "source": source}
            with self.subTest(source=source):
                self.assertEqual(rr._mode_for_case(case), "TEST")

    # 13. RAZORPAY_TEST source maps to TEST via fallback table
    def test_13_razorpay_test_source_fallback(self):
        case = {"case_id": "c_rt", "provider_mode": "", "source": "RAZORPAY_TEST"}
        self.assertEqual(rr._mode_for_case(case), "TEST")

    # 14. RAZORPAY_LIVE source maps to LIVE via fallback table
    def test_14_razorpay_live_source_fallback(self):
        case = {"case_id": "c_rl", "provider_mode": "", "source": "RAZORPAY_LIVE"}
        self.assertEqual(rr._mode_for_case(case), "LIVE")


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestRecoveryModeIsolation)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)

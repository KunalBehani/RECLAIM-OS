import hashlib
import hmac
from datetime import datetime, timezone

import requests

from providers.base import IntegrationError, ProviderAdapter

API_BASE = "https://api.razorpay.com/v1"
SUPPORTED_EVENTS = {"payment.authorized", "payment.captured", "payment.failed", "order.paid"}
EVENT_STATUS = {"payment.authorized": "pending", "payment.captured": "success", "payment.failed": "failed"}
REQUEST_TIMEOUT = 10


def _iso_from_epoch(value):
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
    except Exception:
        return None


class RazorpayAdapter(ProviderAdapter):
    """Razorpay TEST/LIVE adapter. TEST and LIVE use separate key sets; this
    phase only accepts TEST credentials."""

    provider = "razorpay"
    display_name = "Razorpay"

    def __init__(self, config: dict):
        self.config = config or {}
        self.mode = self.config.get("mode", "TEST")

    @property
    def source(self) -> str:
        return "RAZORPAY_LIVE" if self.mode == "LIVE" else "RAZORPAY_TEST"

    @staticmethod
    def verify_signature(raw_body: bytes, signature, secret: str) -> bool:
        """Official Razorpay scheme: hex HMAC-SHA256 of the RAW request body
        with the webhook secret, constant-time comparison. Never re-serialize."""
        if not signature or not secret:
            return False
        expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, str(signature).strip())

    def normalize_event(self, payload: dict, provider_event_id=None) -> dict:
        """Razorpay event JSON -> provider-independent normalized event.
        Amounts arrive in paise (minor units) and are converted to major units."""
        event_type = payload.get("event")
        if event_type not in SUPPORTED_EVENTS:
            return {"kind": "unsupported", "event_type": event_type}

        if event_type == "order.paid":
            entity = ((payload.get("payload") or {}).get("order") or {}).get("entity") or {}
            return {
                "kind": "order",
                "provider": self.provider,
                "provider_event_id": provider_event_id,
                "event_type": event_type,
                "order_id": entity.get("id"),
                "amount": round((entity.get("amount") or 0) / 100, 2),
                "amount_paid": round((entity.get("amount_paid") or 0) / 100, 2),
                "amount_due": round((entity.get("amount_due") or 0) / 100, 2),
                "currency": entity.get("currency"),
                "status": entity.get("status"),
                "receipt": entity.get("receipt"),
                "created_at": _iso_from_epoch(entity.get("created_at")),
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "source": self.source,
                "source_mode": self.mode,
            }

        entity = ((payload.get("payload") or {}).get("payment") or {}).get("entity") or {}
        created_iso = _iso_from_epoch(entity.get("created_at"))
        attempt = {
            "payment_id": entity.get("id"),
            "order_id": entity.get("order_id"),
            "invoice_id": entity.get("invoice_id"),
            "customer_reference": entity.get("email") or entity.get("contact"),
            "amount": round((entity.get("amount") or 0) / 100, 2),
            "currency": entity.get("currency"),
            "status": EVENT_STATUS[event_type],
            "failure_code": entity.get("error_code"),
            "failure_reason": entity.get("error_description"),
            "payment_method": entity.get("method"),
            "timestamp": created_iso or datetime.now(timezone.utc).isoformat(),
            "captured_at": created_iso if event_type == "payment.captured" else None,
            "provider": self.provider,
            "provider_event_ids": [provider_event_id] if provider_event_id else [],
            "source": self.source,
            "source_event_id": provider_event_id,
            "simulated": False,
            "ingestion_confidence": 1.0,
            "payment_id_generated": False,
            "timestamp_estimated": created_iso is None,
            "raw_data_reference": f"razorpay:{provider_event_id}",
            "batch_id": None,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        }
        return {
            "kind": "payment",
            "provider": self.provider,
            "provider_event_id": provider_event_id,
            "event_type": event_type,
            "attempt": attempt,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "source": self.source,
            "source_mode": self.mode,
        }

    def _request(self, method: str, path: str) -> dict:
        key_id = self.config.get("key_id")
        key_secret = self.config.get("key_secret")
        if not key_id or not key_secret:
            raise IntegrationError("Razorpay API credentials are not configured")
        try:
            resp = requests.request(
                method,
                f"{API_BASE}{path}",
                auth=(key_id, key_secret),
                timeout=REQUEST_TIMEOUT,
            )
        except requests.Timeout:
            raise IntegrationError("Provider API timeout")
        except requests.RequestException:
            raise IntegrationError("Provider API connection error")
        if resp.status_code == 401:
            raise IntegrationError("Provider rejected the configured credentials (401)")
        if resp.status_code == 404:
            raise IntegrationError("Resource not found at provider (404)")
        if resp.status_code >= 500:
            raise IntegrationError(f"Provider API error (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            raise IntegrationError(f"Provider API rejected the request (HTTP {resp.status_code})")
        return resp.json()

    def test_connection(self) -> dict:
        data = self._request("GET", "/orders?count=1")
        return {"ok": True, "mode": self.mode, "orders_visible": data.get("count", 0)}

    def fetch_order(self, order_id: str) -> dict:
        return self._request("GET", f"/orders/{order_id}")

    def fetch_payment(self, payment_id: str) -> dict:
        return self._request("GET", f"/payments/{payment_id}")

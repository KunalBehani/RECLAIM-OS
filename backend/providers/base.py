class IntegrationError(Exception):
    """Provider API failure. Message must never contain credentials or secrets."""


class ProviderAdapter:
    """Provider abstraction. The core recovery engine only ever sees
    normalized events produced by an adapter — never provider-specific JSON."""

    provider = "base"
    display_name = "Provider"

    def verify_signature(self, raw_body: bytes, signature, secret: str) -> bool:
        raise NotImplementedError

    def normalize_event(self, payload: dict, provider_event_id=None) -> dict:
        raise NotImplementedError

    def test_connection(self) -> dict:
        raise NotImplementedError

    def fetch_order(self, order_id: str) -> dict:
        raise NotImplementedError

    def fetch_payment(self, payment_id: str) -> dict:
        raise NotImplementedError

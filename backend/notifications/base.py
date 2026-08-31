"""Provider-agnostic notification adapter interface.

The core recovery engine depends ONLY on this interface — never on a concrete
provider (Resend, SES, ...). Secrets live inside concrete adapters and are
never logged or returned.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass


class NotificationError(Exception):
    """Sanitized delivery failure — never contains secrets or raw payloads."""


@dataclass
class NotificationResult:
    channel: str
    status: str            # SENT
    provider_reference: str
    recipient: str


class NotificationAdapter(ABC):
    channel: str = "email"

    @abstractmethod
    async def send_recovery_email(self, *, recipient: str, subject: str, html: str) -> NotificationResult:
        """Send one genuine customer-facing recovery email."""

    @abstractmethod
    async def test_connection(self, *, recipient: str) -> NotificationResult:
        """Send a genuine test email to a server-side recipient to verify the channel end-to-end."""


def mask_email(email: str) -> str:
    """Display-safe masking for UI/audit (never a security boundary)."""
    if not email or "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    return f"{local[:2]}***@{domain}"

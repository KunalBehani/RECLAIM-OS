"""Resend-backed notification adapter via Emergent's managed email proxy.

The API key is server-side only (backend .env), never logged, never returned
to the frontend. Every send passes the structural guardrail gate.
"""
import ipaddress
import logging
import os
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from notifications.base import NotificationAdapter, NotificationError, NotificationResult

logger = logging.getLogger(__name__)

EMAIL_BASE_URL = "https://integrations.emergentagent.com"
EMAIL_KEY = os.environ.get("EMERGENT_EMAIL_KEY", "")
EMAIL_FROM_NAME = os.environ.get("EMAIL_FROM_NAME", "")
EMAIL_REPLY_TO = os.environ.get("EMAIL_REPLY_TO")

_SHORTENERS = ("bit.ly", "tinyurl.com", "t.co", "is.gd", "cutt.ly", "goo.gl", "rebrand.ly")
_CRED_ASK = ("reply with your password", "reply with the code", "send your password", "cvv",
             "send us your password", "enter your password below", "confirm your card number",
             "your full card number", "seed phrase", "recovery phrase", "verify your card",
             "social security number", "confirm your bank details")
_HOSTISH = re.compile(r"\b(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,})", re.I)


def _host_ok(host: str) -> bool:
    if not host or "xn--" in host:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    return not any(host == s or host.endswith("." + s) for s in _SHORTENERS)


def _same_site(shown: str, real: str) -> bool:
    return shown == real or real.endswith("." + shown) or shown.endswith("." + real)


class _EmailScan(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.urls, self.anchors = set(), [], []
        self._href, self._text = None, []

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag.lower())
        self.urls += [v for k, v in attrs if k.lower() in ("href", "src") and v]
        if tag.lower() == "a":
            self._href = dict((k.lower(), v) for k, v in attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            self.anchors.append((self._href, "".join(self._text)))
            self._href, self._text = None, []


def _assert_safe_email(subject: str, html: str) -> None:
    scan = _EmailScan()
    scan.feed(html)
    if scan.tags & {"form", "input", "textarea", "select"}:
        raise ValueError("No forms or input fields in email (G2)")
    body = f"{subject}\n{html}".lower()
    for p in _CRED_ASK:
        if p in body:
            raise ValueError(f"Email asks the recipient for credentials: {p!r} (G2)")
    for url in scan.urls:
        low = url.strip().lower()
        if low.startswith(("mailto:", "tel:", "cid:", "#")):
            continue
        if not low.startswith("https://"):
            raise ValueError(f"Email links/assets must be absolute https: {url!r} (G3)")
        host = urlparse(low).hostname or ""
        if not _host_ok(host) or urlparse(low).username is not None:
            raise ValueError(f"Shortened, numeric-host or credential-bearing URL: {url!r} (G3)")
    for href, text in scan.anchors:
        real = urlparse(href.strip().lower()).hostname or ""
        if not real:
            continue
        for m in _HOSTISH.finditer(text):
            if not _same_site(m.group(1).lower(), real):
                raise ValueError(f"Anchor text {m.group(1)!r} != real link host {real!r} (G3)")


class ResendNotificationAdapter(NotificationAdapter):
    channel = "email"

    def __init__(self):
        if not EMAIL_KEY or not EMAIL_FROM_NAME:
            raise NotificationError("Notification channel is not configured")

    async def _send(self, *, to: str, subject: str, html: str) -> NotificationResult:
        _assert_safe_email(subject, html)
        payload = {"to": [to], "subject": subject, "html": html, "from_name": EMAIL_FROM_NAME}
        if EMAIL_REPLY_TO:
            payload["contact_email"] = EMAIL_REPLY_TO
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{EMAIL_BASE_URL}/api/v1/email/send",
                    headers={"X-Email-Key": EMAIL_KEY},
                    json=payload,
                )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error("Email send failed: HTTP %s", e.response.status_code)
            raise NotificationError(f"Provider rejected the send (HTTP {e.response.status_code})")
        except Exception:
            logger.error("Email send error (transport)")
            raise NotificationError("Provider API connection error")
        return NotificationResult(
            channel=self.channel, status="SENT",
            provider_reference=resp.json().get("id") or "unknown", recipient=to,
        )

    async def send_recovery_email(self, *, recipient: str, subject: str, html: str) -> NotificationResult:
        return await self._send(to=recipient, subject=subject, html=html)

    async def test_connection(self, *, recipient: str) -> NotificationResult:
        from notifications.templates import build_test_email
        subject, html = build_test_email()
        return await self._send(to=recipient, subject=subject, html=html)


def masked_diagnostics(enabled: bool) -> dict:
    """Safe diagnostic metadata — never the key itself."""
    return {
        "provider": "resend",
        "channel": "email",
        "enabled": enabled,
        "api_key_present": bool(EMAIL_KEY),
        "api_key_prefix": EMAIL_KEY[:3] if EMAIL_KEY else None,
        "api_key_length": len(EMAIL_KEY),
        "from_name": EMAIL_FROM_NAME or None,
        "reply_to_present": bool(EMAIL_REPLY_TO),
        "credential_source": "backend_env",
        "endpoint": EMAIL_BASE_URL,
        "auth_method": "x-email-key header",
    }

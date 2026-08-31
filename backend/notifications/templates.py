"""Fixed server-side email templates (G4: callers pass IDs, never markup).

All links point at this app's own public URL. No forms, no credential asks;
the footer names the sending brand (G1/G2/G3).
"""
import os
from html import escape

PUBLIC_APP_URL = os.environ.get("PUBLIC_APP_URL", "").rstrip("/")
BRAND = os.environ.get("EMAIL_FROM_NAME", "RECLAIM OS")


def recovery_pay_url(token: str) -> str:
    return f"{PUBLIC_APP_URL}/pay/{token}"


def build_recovery_email(*, amount_inr: float, order_id: str, pay_url: str) -> tuple[str, str]:
    subject = f"Action needed: complete your payment of ₹{amount_inr:,.2f}"
    html = (
        '<table role="presentation" width="100%"><tr><td style="padding:24px;'
        'font-family:Arial,sans-serif;color:#0f172a">'
        "<p>Hello,</p>"
        f"<p>Your recent payment of <strong>₹{amount_inr:,.2f}</strong> "
        f"(order <strong>{escape(order_id)}</strong>) did not go through. "
        "You can safely retry the same order using the secure button below.</p>"
        '<p style="margin:24px 0">'
        f'<a href="{escape(pay_url)}" style="background:#072654;color:#ffffff;padding:12px 24px;'
        'border-radius:8px;text-decoration:none;font-weight:bold">Complete your payment</a></p>'
        "<p style=\"font-size:12px;color:#64748b\">This link opens our secure checkout page for "
        "this order only. If you already completed this payment, please ignore this email.</p>"
        f'<p style="font-size:12px;color:#94a3b8">Sent by {escape(BRAND)}. '
        "We never ask for your password, OTP or card details by email.</p>"
        "</td></tr></table>"
    )
    return subject, html


def build_test_email() -> tuple[str, str]:
    subject = f"{BRAND}: notification channel test"
    html = (
        '<table role="presentation" width="100%"><tr><td style="padding:24px;'
        'font-family:Arial,sans-serif;color:#0f172a">'
        f"<p>This is a genuine end-to-end test email from {escape(BRAND)} "
        "confirming the customer notification channel is operational.</p>"
        f'<p style="font-size:12px;color:#94a3b8">Sent by {escape(BRAND)}. '
        "We never ask for your password, OTP or card details by email.</p>"
        "</td></tr></table>"
    )
    return subject, html

import hashlib
import hmac
import os


def webhook_secret() -> str:
    return os.environ.get("WEBHOOK_SECRET", "")


def compute_signature(body: bytes, secret: str | None = None) -> str:
    secret = secret if secret is not None else webhook_secret()
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(body: bytes, signature: str | None, secret: str | None = None) -> bool:
    if not signature:
        return False
    expected = compute_signature(body, secret)
    return hmac.compare_digest(expected, signature)

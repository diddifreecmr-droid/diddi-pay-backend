"""Verification of callbacks sent by DiddiPay to DiddiFund."""

import hashlib
import hmac


def verify_diddipay_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    if not signature or not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)

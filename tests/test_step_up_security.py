from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from payfund_app.core.errors import StepUpProofExpired, StepUpProofInvalid
from payfund_app.core.security import decode_step_up_token


class _SigningKey:
    def __init__(self, key):
        self.key = key


class _JwksClient:
    def __init__(self, key):
        self.key = key

    def get_signing_key_from_jwt(self, token):
        return _SigningKey(self.key)


def _token(private_key, *, user_id, purpose="wallet.pin.set", expires_in=300):
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iss": "diddifree-id",
            "purpose": purpose,
            "jti": str(uuid.uuid4()),
            "iat": now,
            "exp": now + timedelta(seconds=expires_in),
        },
        private_key,
        algorithm="RS256",
    )


@pytest.fixture
def signing_keys(monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(
        "payfund_app.core.security._client",
        lambda: _JwksClient(private_key.public_key()),
    )
    return private_key


def test_step_up_proof_verifies_signature_subject_and_purpose(signing_keys):
    user_id = uuid.uuid4()

    proof = decode_step_up_token(
        _token(signing_keys, user_id=user_id),
        expected_user_id=user_id,
        expected_purpose="wallet.pin.set",
    )

    assert proof.user_id == user_id
    assert proof.purpose == "wallet.pin.set"


def test_step_up_proof_rejects_wrong_purpose(signing_keys):
    user_id = uuid.uuid4()

    with pytest.raises(StepUpProofInvalid):
        decode_step_up_token(
            _token(signing_keys, user_id=user_id, purpose="wallet.pin.recover"),
            expected_user_id=user_id,
            expected_purpose="wallet.pin.set",
        )


def test_step_up_proof_rejects_expired_token(signing_keys):
    user_id = uuid.uuid4()

    with pytest.raises(StepUpProofExpired):
        decode_step_up_token(
            _token(signing_keys, user_id=user_id, expires_in=-1),
            expected_user_id=user_id,
            expected_purpose="wallet.pin.set",
        )

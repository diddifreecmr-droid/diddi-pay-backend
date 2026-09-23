from types import SimpleNamespace

import jwt
import pytest

from payfund_app.core import security
from payfund_app.core.errors import Forbidden, Unauthenticated


def _claims(**overrides):
    claims = {
        "sub": "service:pilotage",
        "client_id": "pilotage-staging-diddipay",
        "token_type": "service",
        "role": "service",
        "status": "active",
        "scope": "diddipay:payment-summary:read",
    }
    claims.update(overrides)
    return claims


def _configure(monkeypatch, claims):
    monkeypatch.setattr(
        security,
        "_client",
        lambda: SimpleNamespace(
            get_signing_key_from_jwt=lambda _: SimpleNamespace(key="key")
        ),
    )
    monkeypatch.setattr(jwt, "decode", lambda *args, **kwargs: claims)


def test_service_token_returns_scoped_principal(monkeypatch):
    _configure(monkeypatch, _claims())
    principal = security.decode_service_token(
        "token",
        audience="diddipay",
        client_id_header="pilotage-staging-diddipay",
        required_scopes={"diddipay:payment-summary:read"},
        allowed_client_ids={"pilotage-staging-diddipay"},
    )
    assert principal.subject == "service:pilotage"
    assert principal.scopes == frozenset({"diddipay:payment-summary:read"})


@pytest.mark.parametrize(
    "overrides",
    [
        {"token_type": "access"},
        {"role": "user"},
        {"status": "disabled"},
        {"sub": "not-a-service"},
        {"client_id": "other-client"},
    ],
)
def test_service_token_rejects_invalid_trust_claims(monkeypatch, overrides):
    _configure(monkeypatch, _claims(**overrides))
    with pytest.raises(Unauthenticated):
        security.decode_service_token(
            "token",
            audience="diddipay",
            client_id_header="pilotage-staging-diddipay",
        )


def test_service_token_separates_client_and_scope_forbidden(monkeypatch):
    _configure(monkeypatch, _claims(scope="other:read"))
    with pytest.raises(Forbidden) as scope_error:
        security.decode_service_token(
            "token",
            audience="diddipay",
            client_id_header="pilotage-staging-diddipay",
            required_scopes={"diddipay:payment-summary:read"},
        )
    assert scope_error.value.code == "SERVICE_SCOPE_REQUIRED"

    with pytest.raises(Forbidden) as client_error:
        security.decode_service_token(
            "token",
            audience="diddipay",
            client_id_header="pilotage-staging-diddipay",
            allowed_client_ids={"backoffice-staging-diddipay"},
        )
    assert client_error.value.code == "SERVICE_CLIENT_FORBIDDEN"

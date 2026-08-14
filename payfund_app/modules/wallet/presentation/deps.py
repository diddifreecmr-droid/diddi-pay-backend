"""Dépendances FastAPI communes à `wallet` et `fund`."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from payfund_app.core.database import get_session
from payfund_app.core.errors import Unauthenticated
from payfund_app.core.security import CurrentUser
from payfund_app.modules.wallet.domain.errors import IdempotencyKeyRequired
from payfund_app.shared_kernel.contracts.identity_provider import JwksIdentityVerifier

_verifier = JwksIdentityVerifier()
_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise Unauthenticated("En-tête `Authorization: Bearer <access_token>` attendu.")
    return _verifier.verify(credentials.credentials)


def get_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    """Contrat API §0 : en-tête obligatoire sur toute route qui déplace des fonds."""
    if not idempotency_key or not idempotency_key.strip():
        raise IdempotencyKeyRequired()
    return idempotency_key.strip()


SessionDep = Annotated[Session, Depends(get_session)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
IdempotencyKeyDep = Annotated[str, Depends(get_idempotency_key)]

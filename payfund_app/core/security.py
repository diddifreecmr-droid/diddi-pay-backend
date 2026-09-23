"""Vérification **locale** du JWT DiddiFreeID.

DiddiFreeID_Contrat_API.md §0 et §2 : chaque module récupère le JWKS (mis en cache) et vérifie
lui-même la signature RS256. Aucun appel HTTP par requête pour valider un token.

Le JWT ne porte que `sub`, `role`, `status`, `iat`, `exp` (§2) — il n'y a donc ni `iss` ni `aud`
à vérifier.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Collection
from uuid import UUID

import jwt
from jwt import PyJWKClient

from payfund_app.core.config import get_settings
from payfund_app.core.errors import (
    Forbidden,
    StepUpProofExpired,
    StepUpProofInvalid,
    TokenExpired,
    Unauthenticated,
)


@dataclass(frozen=True)
class CurrentUser:
    user_id: UUID
    role: str
    status: str


@dataclass(frozen=True)
class StepUpProof:
    jti: UUID
    user_id: UUID
    purpose: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ServicePrincipal:
    client_id: str
    subject: str
    scopes: frozenset[str]


_jwk_client: PyJWKClient | None = None


def _client() -> PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        # PyJWKClient met le JWKS en cache et sélectionne la clé par le `kid` du header,
        # ce qui couvre la rotation à deux clés décrite au §2 du contrat DiddiFreeID.
        _jwk_client = PyJWKClient(get_settings().diddifreeid_jwks_url, cache_keys=True)
    return _jwk_client


def decode_access_token(token: str) -> CurrentUser:
    try:
        signing_key = _client().get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            options={"require": ["sub", "exp"], "verify_aud": False},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpired() from exc
    except jwt.PyJWTError as exc:
        raise Unauthenticated() from exc

    status = claims.get("status", "")
    if status != "active":
        # §2 : un module qui reçoit un token avec status != "active" doit refuser l'action,
        # même si la signature est valide.
        raise Forbidden(
            "Compte non actif.", details={"status": status}, code="ACCOUNT_NOT_ACTIVE"
        )

    try:
        user_id = UUID(str(claims["sub"]))
    except (ValueError, KeyError) as exc:
        raise Unauthenticated("Claim `sub` invalide.") from exc

    return CurrentUser(user_id=user_id, role=str(claims.get("role", "user")), status=status)


def decode_service_token(
    token: str,
    *,
    audience: str,
    client_id_header: str | None,
    required_scopes: Collection[str] = (),
    allowed_client_ids: Collection[str] = (),
) -> ServicePrincipal:
    """Verify a DiddiFreeID service JWT locally and enforce route-level trust."""
    if not token or not client_id_header:
        raise Unauthenticated("Token de service et X-Client-ID requis.")
    try:
        signing_key = _client().get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            issuer=get_settings().diddifreeid_issuer,
            audience=audience,
            options={
                "require": ["sub", "iss", "aud", "iat", "exp", "client_id", "token_type"],
            },
        )
    except (jwt.PyJWTError, ValueError) as exc:
        raise Unauthenticated("Token de service invalide.") from exc

    client_id = str(claims.get("client_id") or "")
    subject = str(claims.get("sub") or "")
    if (
        claims.get("token_type") != "service"
        or claims.get("role") != "service"
        or claims.get("status") != "active"
        or not subject.startswith("service:")
        or client_id != client_id_header
    ):
        raise Unauthenticated("Claims du token de service invalides.")

    allowed = frozenset(allowed_client_ids)
    if allowed and client_id not in allowed:
        raise Forbidden(
            "Service appelant non autorise.",
            details={"client_id": client_id},
            code="SERVICE_CLIENT_FORBIDDEN",
        )

    raw_scope = claims.get("scope")
    scopes = frozenset(raw_scope.split()) if isinstance(raw_scope, str) else frozenset()
    required = frozenset(required_scopes)
    if required and scopes.isdisjoint(required):
        raise Forbidden(
            "Scope de service insuffisant.",
            details={"required_scopes": sorted(required)},
            code="SERVICE_SCOPE_REQUIRED",
        )
    return ServicePrincipal(client_id=client_id, subject=subject, scopes=scopes)


def decode_step_up_token(
    token: str, *, expected_user_id: UUID, expected_purpose: str
) -> StepUpProof:
    """Verify a short-lived, purpose-bound DiddiFreeID proof locally through JWKS."""
    try:
        signing_key = _client().get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            issuer=get_settings().diddifreeid_issuer,
            options={
                "require": ["sub", "iss", "purpose", "jti", "iat", "exp"],
                "verify_aud": False,
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise StepUpProofExpired() from exc
    except jwt.PyJWTError as exc:
        raise StepUpProofInvalid() from exc

    try:
        user_id = UUID(str(claims["sub"]))
        jti = UUID(str(claims["jti"]))
        issued_at = datetime.fromtimestamp(int(claims["iat"]), tz=timezone.utc)
        expires_at = datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise StepUpProofInvalid() from exc

    purpose = str(claims.get("purpose", ""))
    if user_id != expected_user_id or purpose != expected_purpose:
        raise StepUpProofInvalid()
    proof_ttl = (expires_at - issued_at).total_seconds()
    if proof_ttl > get_settings().diddifreeid_step_up_max_ttl_seconds:
        raise StepUpProofInvalid("Durée de validité de la preuve excessive.")

    return StepUpProof(
        jti=jti,
        user_id=user_id,
        purpose=purpose,
        expires_at=expires_at,
    )

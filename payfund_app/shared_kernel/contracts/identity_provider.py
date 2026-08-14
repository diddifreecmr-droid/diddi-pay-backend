"""IdentityVerifierPort — consomme DiddiFreeID.

La vérification de token est locale (JWKS + RS256, cf. `core/security.py`). Ce port n'existe que
pour isoler les modules du *mécanisme* de vérification, pas pour introduire un appel réseau.
"""

from typing import Protocol

from uuid import UUID

from payfund_app.core.security import (
    CurrentUser,
    StepUpProof,
    decode_access_token,
    decode_step_up_token,
)


class IdentityVerifierPort(Protocol):
    def verify(self, access_token: str) -> CurrentUser:
        """Retourne l'utilisateur porté par le token, ou lève une AppError 401/403."""
        ...


class JwksIdentityVerifier:
    """Implémentation par défaut : vérification locale de signature RS256."""

    def verify(self, access_token: str) -> CurrentUser:
        return decode_access_token(access_token)


class StepUpProofVerifierPort(Protocol):
    def verify(
        self, token: str, *, expected_user_id: UUID, expected_purpose: str
    ) -> StepUpProof: ...


class JwksStepUpProofVerifier:
    def verify(
        self, token: str, *, expected_user_id: UUID, expected_purpose: str
    ) -> StepUpProof:
        return decode_step_up_token(
            token,
            expected_user_id=expected_user_id,
            expected_purpose=expected_purpose,
        )

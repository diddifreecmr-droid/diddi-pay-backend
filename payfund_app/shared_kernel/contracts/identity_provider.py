"""IdentityVerifierPort — consomme DiddiFreeID.

La vérification de token est locale (JWKS + RS256, cf. `core/security.py`). Ce port n'existe que
pour isoler les modules du *mécanisme* de vérification, pas pour introduire un appel réseau.
"""

from typing import Protocol

from payfund_app.core.security import CurrentUser, decode_access_token


class IdentityVerifierPort(Protocol):
    def verify(self, access_token: str) -> CurrentUser:
        """Retourne l'utilisateur porté par le token, ou lève une AppError 401/403."""
        ...


class JwksIdentityVerifier:
    """Implémentation par défaut : vérification locale de signature RS256."""

    def verify(self, access_token: str) -> CurrentUser:
        return decode_access_token(access_token)

from decimal import Decimal
from functools import lru_cache
import re
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://payfund:payfund@localhost:5432/payfund"

    # DiddiFreeID : on ne récupère que le JWKS. Aucun appel HTTP pour vérifier un token
    # (DiddiFreeID_Contrat_API.md §0 : « Ne jamais appeler DiddiFreeID en HTTP pour
    # simplement vérifier qu'un token est valide »).
    diddifreeid_jwks_url: str = (
        "https://auth-staging.diddifree.com/identity/v1/.well-known/jwks.json"
    )

    redis_url: str = "redis://localhost:6379/0"
    event_bus_channel: str = "diddifree.events"

    payment_gateway_mode: str = "stub"
    # `stub` = passerelle générique simulée. `sandbox_orange_money` = sandbox Orange Money
    # explicite pour valider le rail Orange avant branchage du vrai provider.
    # Passer à `true` en développement pour que dépôts et retraits se confirment
    # immédiatement, sans attendre l'appel de confirmation.
    payment_gateway_autoconfirm: bool = False
    paystack_secret_key: str = ""
    paystack_base_url: str = "https://api.paystack.co"
    paystack_webhook_secret: str = ""

    # Taux d'intérêt annuel appliqué tant que le module de scoring IA n'expose pas d'interface
    # (Architecture §6). Valeur par défaut = celle de l'exemple du contrat API §2.
    default_interest_rate: Decimal = Decimal("6.5")

    # Clé de signature HMAC des QR codes de paiement (Contrat API §3 : format non spécifié,
    # à définir avec Frontend/Mobile — voir `domain/qr.py`). À écraser en production : un QR
    # signé avec la valeur par défaut serait forgeable par quiconque lit ce fichier.
    qr_signing_secret: str = "change-me-in-production"

    sql_echo: bool = False

    # Origines autorisées en CORS (frontend Flutter Web, essentiellement — les clients mobiles
    # natifs ne sont pas soumis à CORS). Liste séparée par des virgules ; "*" par défaut pour ne
    # pas bloquer le dev local. À restreindre aux domaines réels avant toute mise en production.
    cors_origins: str = "*"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def cors_origin_regex(self) -> str:
        """Autorise localhost sur n'importe quel port, plus les domaines DiddiFree et Vercel.

        On garde `allow_origins` pour les origines éventuellement injectées explicitement dans
        `CORS_ORIGINS`, et on ajoute un regex pour couvrir les environnements de dev courants sans
        avoir à lister chaque port.
        """

        origins = self.cors_origins_list
        if "*" in origins:
            return r"https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$|https?://(?:[^/]+\.)?(?:diddifree\.com|vercel\.com)(?::\d+)?$"

        patterns = []
        for origin in origins:
            parsed = urlparse(origin)
            if parsed.scheme and parsed.netloc:
                patterns.append(f"{re.escape(parsed.scheme)}://{re.escape(parsed.netloc)}")
        if not patterns:
            return r"^$"
        return r"^(?:" + "|".join(patterns) + r")$"


@lru_cache
def get_settings() -> Settings:
    return Settings()

from decimal import Decimal
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://payfund:payfund@localhost:5432/payfund"

    # DiddiFreeID : on ne récupère que le JWKS. Aucun appel HTTP pour vérifier un token
    # (DiddiFreeID_Contrat_API.md §0 : « Ne jamais appeler DiddiFreeID en HTTP pour
    # simplement vérifier qu'un token est valide »).
    diddifreeid_jwks_url: str = (
        "https://api-dev.diddifree.app/identity/v1/.well-known/jwks.json"
    )

    redis_url: str = "redis://localhost:6379/0"
    event_bus_channel: str = "diddifree.events"

    payment_gateway_mode: str = "stub"
    # Le stub renvoie `pending` comme le ferait un vrai opérateur. Passer à `true` en
    # développement pour que dépôts et retraits se confirment immédiatement, sans attendre
    # l'appel de confirmation.
    payment_gateway_autoconfirm: bool = False

    # Taux d'intérêt annuel appliqué tant que le module de scoring IA n'expose pas d'interface
    # (Architecture §6). Valeur par défaut = celle de l'exemple du contrat API §2.
    default_interest_rate: Decimal = Decimal("6.5")

    # Clé de signature HMAC des QR codes de paiement (Contrat API §3 : format non spécifié,
    # à définir avec Frontend/Mobile — voir `domain/qr.py`). À écraser en production : un QR
    # signé avec la valeur par défaut serait forgeable par quiconque lit ce fichier.
    qr_signing_secret: str = "change-me-in-production"

    sql_echo: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()

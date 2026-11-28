import re
from decimal import Decimal
from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import AliasChoices, AnyHttpUrl, BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PaymentCallbackSettings(BaseModel):
    url: AnyHttpUrl
    secret: str = Field(min_length=16)

    @field_validator("url")
    @classmethod
    def require_secure_callback(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        internal_hosts = {"localhost", "127.0.0.1", "app", "payfund-app"}
        if value.scheme != "https" and value.host not in internal_hosts:
            raise ValueError("payment callback URL must use HTTPS outside the internal network")
        if value.username or value.password:
            raise ValueError("payment callback URL must not contain user information")
        return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True, populate_by_name=True)

    database_url: str = "postgresql+psycopg://payfund:payfund@localhost:5432/payfund"
    deployment_environment: Literal["local", "test", "staging", "production"] = "local"

    # Observabilite runtime. `release_sha` permet de relier une erreur au commit deploye.
    observability_enabled: bool = True
    observability_environment: Literal["local", "development", "test", "staging", "production"] = Field(default="development", validation_alias=AliasChoices("ENVIRONMENT", "OBSERVABILITY_ENVIRONMENT"))
    observability_service_name: Literal["diddipay"] = Field(default="diddipay", validation_alias=AliasChoices("OTEL_SERVICE_NAME", "OBSERVABILITY_SERVICE_NAME"))
    observability_release_sha: str = Field(default="unknown", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    observability_deployment_id: str = Field(validation_alias=AliasChoices("DEPLOYMENT_ID", "OBSERVABILITY_DEPLOYMENT_ID"), default="local", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    observability_component: Literal["api", "worker", "scheduler"] = "api"
    worker_metrics_port: int = Field(default=9001, ge=1024, le=65535)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    observability_log_file: str = ""
    metrics_enabled: bool = False
    metrics_token: str = Field(default="", validation_alias=AliasChoices("METRICS_BEARER_TOKEN", "METRICS_TOKEN"))
    metrics_allowed_cidrs: str = ""

    @field_validator("metrics_allowed_cidrs")
    @classmethod
    def validate_metrics_networks(cls, value: str) -> str:
        from ipaddress import ip_network
        for item in value.split(","):
            if item.strip():
                ip_network(item.strip())
        return value
    otel_logs_enabled: bool = False
    otel_exporter_otlp_logs_endpoint: str = ""
    otel_exporter_otlp_protocol: Literal["http/protobuf"] = "http/protobuf"
    tracing_enabled: bool = True
    tracing_otlp_endpoint: str = Field(default="", validation_alias=AliasChoices("OTEL_EXPORTER_OTLP_ENDPOINT", "TRACING_OTLP_ENDPOINT"))
    tracing_otlp_traces_endpoint: str = Field(default="", validation_alias=AliasChoices("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "TRACING_OTLP_TRACES_ENDPOINT"))
    tracing_allow_insecure_local: bool = False
    tracing_sample_rate: float = Field(default=0.1, ge=0.0, le=1.0)
    payment_worker_heartbeat_path: str = "/tmp/diddipay-payment-worker-heartbeat.json"
    payment_worker_heartbeat_max_age_seconds: int = Field(default=90, ge=30, le=600)

    @field_validator("metrics_token")
    @classmethod
    def require_strong_metrics_token(cls, value: str) -> str:
        if value and len(value) < 32:
            raise ValueError("metrics token must contain at least 32 characters")
        return value

    # DiddiFreeID : on ne récupère que le JWKS. Aucun appel HTTP pour vérifier un token
    # (DiddiFreeID_Contrat_API.md §0 : « Ne jamais appeler DiddiFreeID en HTTP pour
    # simplement vérifier qu'un token est valide »).
    diddifreeid_jwks_url: str = (
        "https://auth-staging.diddifree.com/identity/v1/.well-known/jwks.json"
    )
    diddifreeid_issuer: str = "diddifree-id"
    payment_summary_audience: str = "diddipay"
    payment_summary_scope: str = "diddipay:payment-summary:read"
    payment_summary_legacy_scope: str = "payment-summary:read"
    payment_summary_client_id: str = ""
    backoffice_audience: str = "diddipay"
    backoffice_client_ids: str = ""
    backoffice_read_scope: str = "diddipay:operations:read"
    backoffice_command_scope: str = "diddipay:operations:write"
    support_audience: str = "diddipay"
    support_client_ids: str = ""
    support_context_scope: str = "diddipay:support-context:read"
    support_refund_scope: str = "diddipay:support-actions:refund-request"
    diddifreeid_step_up_max_ttl_seconds: int = Field(default=300, ge=60, le=900)

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
    paystack_environment: Literal["test", "live"] = "test"

    # PawaPay (Mobile Money XOF). Sélectionnable via PAYMENT_GATEWAY_MODE=pawapay
    # (voir wallet/infra/pawapay_gateway.py et gateways.get_gateway()).
    pawapay_api_token: str = ""
    pawapay_base_url: str = "https://api.sandbox.pawapay.io"
    # PawaPay callback URLs must match the public URLs configured in the PawaPay dashboard.
    # The legacy single URL remains the payout fallback for existing staging deployments.
    pawapay_callback_url: str = ""
    pawapay_payout_callback_url: str = ""
    pawapay_deposit_callback_url: str = ""
    pawapay_refund_callback_url: str = ""
    pawapay_callback_signature_required: bool = False
    pawapay_public_keys_url: str = ""
    # Payment Page country when the caller sends no customer_phone (ticket 108): the
    # customer then types the wallet number on PawaPay's page. ISO 3166-1 alpha-3.
    pawapay_default_country: str = "CIV"

    # CinetPay API v1 mobile money (api.cinetpay.net sandbox / api.cinetpay.co prod).
    # Auth = api_key (sk_test_/sk_live_) + api_password -> JWT. Les DEUX sont requis ;
    # une clé seule ne peut pas s'authentifier. base_url vide = auto-détecté depuis le
    # préfixe de la clé. Enregistré uniquement si les deux secrets sont présents.
    cinetpay_api_key: str = ""
    cinetpay_api_password: str = ""
    cinetpay_base_url: str = ""
    cinetpay_country: str = "CI"
    cinetpay_webhook_url: str = ""

    # Jèko (jeko.africa) Partner API -- hosted payment page -> REDIRECT, + mobile-money
    # payouts incl. Wave. Auth = TWO headers: api_key (`jeko_...`) + api_key_id (its UUID);
    # no login/JWT. Every call also needs a store_id (from GET /partner_api/stores). No
    # sandbox: calls hit production; isolate via a dedicated test store. Registered only if
    # api_key AND api_key_id AND store_id are all present. webhook_secret is the HMAC-SHA256
    # signing key, wired for later (webhooks gated on BCEAO, SCRUM-555). See
    # docs/evaluation-jeko.md.
    jeko_api_key: str = ""
    jeko_api_key_id: str = ""
    jeko_store_id: str = ""
    jeko_base_url: str = "https://api.jeko.africa"
    jeko_webhook_secret: str = ""

    # Format: "diddigo:key-1,diddifund:key-2". Empty means that no module can call
    # the PaymentIntent API until operations configures service credentials.
    payment_service_keys: str = ""
    payment_service_key_fallback_enabled: bool = True
    payment_service_audience: str = "diddipay"
    payment_service_client_ids: str = ""
    payment_intent_read_scope: str = "diddipay:payment-intents:read"
    payment_intent_write_scope: str = "diddipay:payment-intents:write"
    payment_intent_verify_scope: str = "diddipay:payment-intents:verify"
    # Maps the verifier service client to the owner client IDs it may verify.
    # Empty means fail closed: no cross-module verification is allowed.
    payment_intent_verify_owner_clients: dict[str, list[str]] = Field(default_factory=dict)
    payment_refund_scope: str = "diddipay:payment-intents:refund"
    payment_payout_read_scope: str = "diddipay:payouts:read"
    payment_payout_write_scope: str = "diddipay:payouts:write"
    payment_processor_mode: str = "sandbox"
    payment_callback_targets: dict[str, PaymentCallbackSettings] = Field(
        default_factory=dict
    )
    payment_event_transport: Literal["http_callback", "redis_stream"] = "http_callback"
    payment_redis_stream: str = "diddipay.payment.events.v1"
    payment_checkout_return_targets: dict[str, AnyHttpUrl] = Field(default_factory=dict)
    payment_callback_url_fallback_enabled: bool = True
    # Per-S2S-client allowlist of legacy callback_url origin+path prefixes
    # (SCRUM-462). Keyed by client_id -> list of allowed "https://host/path"
    # prefixes. Empty for a client = legacy callback allowed but audited
    # (transitional); configure in Portainer to enforce. Non-HTTPS / userinfo /
    # malformed URLs are always refused regardless of this setting.
    payment_callback_allowlist: dict[str, list[str]] = Field(default_factory=dict)
    diddifund_diddipay_callback_secret: str = ""

    @property
    def payment_service_key_map(self) -> dict[str, str]:
        entries: dict[str, str] = {}
        for item in self.payment_service_keys.split(","):
            client_id, separator, key = item.strip().partition(":")
            if separator and client_id and key:
                entries[client_id] = key
        return entries

    @property
    def payment_service_client_id_set(self) -> set[str]:
        configured = {
            client_id.strip()
            for client_id in self.payment_service_client_ids.split(",")
            if client_id.strip()
        }
        return configured or set(self.payment_service_key_map)

    def payment_intent_verify_owner_set(self, verifier_client_id: str) -> frozenset[str]:
        return frozenset(
            owner.strip()
            for owner in self.payment_intent_verify_owner_clients.get(verifier_client_id, [])
            if owner.strip()
        )

    @property
    def backoffice_client_id_set(self) -> set[str]:
        return {
            client_id.strip()
            for client_id in self.backoffice_client_ids.split(",")
            if client_id.strip()
        }

    @property
    def support_client_id_set(self) -> set[str]:
        return {
            client_id.strip()
            for client_id in self.support_client_ids.split(",")
            if client_id.strip()
        }

    # Politique de risque DiddiPay. Le seuil reste une decision serveur et peut etre ajuste par
    # environnement sans modifier le code ni demander au frontend de connaitre la regle.
    wallet_step_up_threshold_xof: int = Field(default=50_000, ge=1)

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

        base_patterns = [
            r"https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?",
            r"https?://(?:[^/]+\.)?(?:diddifree\.com|vercel\.com)(?::\d+)?",
        ]
        origins = self.cors_origins_list
        if "*" in origins:
            return r"^(?:" + "|".join(base_patterns) + r")$"

        patterns = list(base_patterns)
        for origin in origins:
            parsed = urlparse(origin)
            if parsed.scheme and parsed.netloc:
                patterns.append(re.escape(f"{parsed.scheme}://{parsed.netloc}"))
        return r"^(?:" + "|".join(patterns) + r")$"


@lru_cache
def get_settings() -> Settings:
    return Settings()

"""Fail-closed configuration checks for real-money production traffic."""

from urllib.parse import urlparse

from payfund_app.core.config import Settings


def production_configuration_errors(settings: Settings) -> list[str]:
    if settings.deployment_environment != "production":
        return []

    errors: list[str] = []
    if settings.payment_processor_mode != "paystack":
        errors.append("PAYMENT_PROCESSOR_MODE must be paystack")
    if settings.paystack_environment != "live":
        errors.append("PAYSTACK_ENVIRONMENT must be live")
    if not settings.paystack_secret_key.startswith("sk_live_"):
        errors.append("PAYSTACK_SECRET_KEY must be a live secret key")
    base_url = urlparse(settings.paystack_base_url)
    if base_url.scheme != "https" or base_url.hostname != "api.paystack.co":
        errors.append("PAYSTACK_BASE_URL must be https://api.paystack.co")
    if settings.payment_service_key_fallback_enabled:
        errors.append("PAYMENT_SERVICE_KEY_FALLBACK_ENABLED must be false")
    if settings.payment_callback_url_fallback_enabled:
        errors.append("PAYMENT_CALLBACK_URL_FALLBACK_ENABLED must be false")
    if not settings.payment_service_client_id_set:
        errors.append("PAYMENT_SERVICE_CLIENT_IDS must not be empty")
    if settings.payment_gateway_autoconfirm:
        errors.append("PAYMENT_GATEWAY_AUTOCONFIRM must be false")
    if settings.cors_origins.strip() == "*":
        errors.append("CORS_ORIGINS must be explicit")
    if settings.qr_signing_secret == "change-me-in-production":
        errors.append("QR_SIGNING_SECRET must be rotated")

    for key, url in settings.payment_checkout_return_targets.items():
        if url.scheme != "https":
            errors.append(f"checkout return target {key} must use HTTPS")
    for client_id, target in settings.payment_callback_targets.items():
        if target.url.scheme != "https":
            errors.append(f"payment callback target {client_id} must use HTTPS")
    return errors

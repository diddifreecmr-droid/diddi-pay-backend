"""Resolve browser return URLs without trusting caller-provided destinations."""

from payfund_app.core.config import Settings
from payfund_app.core.errors import UnprocessableEntity


def resolve_checkout_return_url(
    *,
    settings: Settings,
    client_id: str,
    return_target: str | None,
    legacy_callback_url: str | None,
) -> str | None:
    if return_target and legacy_callback_url:
        raise UnprocessableEntity(
            "Utilisez return_target sans callback_url.",
            code="PAYMENT_RETURN_TARGET_CONFLICT",
        )
    if return_target:
        key = f"{client_id}:{return_target}"
        configured = settings.payment_checkout_return_targets.get(key)
        if configured is None:
            raise UnprocessableEntity(
                "Destination de retour checkout non autorisee.",
                code="PAYMENT_RETURN_TARGET_FORBIDDEN",
                details={"return_target": return_target},
            )
        return str(configured)
    if legacy_callback_url:
        if not settings.payment_callback_url_fallback_enabled:
            raise UnprocessableEntity(
                "callback_url n'est plus accepte; utilisez return_target.",
                code="PAYMENT_CALLBACK_URL_DISABLED",
            )
        return legacy_callback_url
    return None

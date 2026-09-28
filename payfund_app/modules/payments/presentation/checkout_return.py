"""Resolve browser return URLs without trusting caller-provided destinations.

Two ways a service can tell DiddiPay where to send the user back after
checkout:

- ``return_target``: an opaque key resolved to a server-configured HTTPS URL
  (``payment_checkout_return_targets``). Never trusts a caller URL. Preferred.
- ``callback_url``: a legacy raw URL. Gated by
  ``payment_callback_url_fallback_enabled`` and, when still allowed, validated
  against a per-S2S-client allowlist of origin+path prefixes so a caller cannot
  point the browser at an arbitrary or attacker-controlled destination
  (SCRUM-462). The opaque path/query beyond the prefix is preserved.
"""

from urllib.parse import urlsplit

from payfund_app.core.config import Settings
from payfund_app.core.errors import UnprocessableEntity
from payfund_app.shared_kernel.logging import emit


def _reject(client_id: str, host: str | None, reason: str) -> None:
    # Audit the refusal with client_id + host + reason only -- never the full
    # URL (it can carry the owner backend's opaque context).
    emit(
        "warning",
        "payment.callback.rejected",
        client_id=client_id,
        callback_host=host or "?",
        reason=reason,
    )
    raise UnprocessableEntity(
        "URL de retour non autorisee.",
        code="PAYMENT_CALLBACK_URL_FORBIDDEN",
        details={"reason": reason},
    )


def _prefix_matches(url: str, prefix: str) -> bool:
    u, p = urlsplit(url), urlsplit(prefix)
    if u.scheme != p.scheme or u.netloc != p.netloc:
        return False
    base = p.path.rstrip("/")
    return u.path == p.path or u.path == base or u.path.startswith(base + "/")


def _validate_legacy_callback(settings: Settings, client_id: str, url: str) -> str:
    parts = urlsplit(url)
    # Clear attacks are refused regardless of allowlist configuration.
    if parts.scheme != "https":
        _reject(client_id, parts.hostname, "scheme_not_https")
    if parts.username or parts.password or "@" in parts.netloc:
        _reject(client_id, parts.hostname, "userinfo_present")
    if not parts.hostname:
        _reject(client_id, None, "missing_host")
    if "\\" in url or any(ord(ch) < 0x20 for ch in url) or ".." in parts.path:
        _reject(client_id, parts.hostname, "ambiguous_url")

    prefixes = settings.payment_callback_allowlist.get(client_id) or []
    if prefixes:
        if not any(_prefix_matches(url, prefix) for prefix in prefixes):
            _reject(client_id, parts.hostname, "not_in_allowlist")
        return url

    # No allowlist configured for this client yet: allow during the migration
    # to return_target, but audit loudly so the gap is visible and closeable
    # by configuring payment_callback_allowlist in Portainer.
    emit(
        "warning",
        "payment.callback.unvalidated",
        client_id=client_id,
        callback_host=parts.hostname,
        reason="no_allowlist_configured",
    )
    return url


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
        return _validate_legacy_callback(settings, client_id, legacy_callback_url)
    return None

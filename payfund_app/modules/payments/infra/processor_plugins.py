"""Self-describing payment processor plugins (ticket 110, step 2).

Each plugin says, in one place, which settings it needs, how to build its adapter for normal
operations and for each provider callback kind, and its registry priority. The registry and
the webhook dependencies loop over `PLUGINS`; nothing else needs a per-processor if/else.

Adding a processor = write `infra/<name>_processor.py`, add its settings to `core/config.py`,
append one `ProcessorPlugin` here. (Webhook routes become generic in step 3.)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from payfund_app.core.config import Settings
from payfund_app.core.errors import ServiceUnavailable
from payfund_app.modules.payments.application.ports import WebhookKind
from payfund_app.modules.payments.infra.cinetpay_processor import CinetpayPaymentProcessor
from payfund_app.modules.payments.infra.jeko_processor import JekoPaymentProcessor
from payfund_app.modules.payments.infra.pawapay_processor import PawapayPaymentProcessor
from payfund_app.modules.payments.infra.paystack_processor import PaystackPaymentProcessor
from payfund_app.modules.payments.infra.sandbox_processor import SandboxPaymentProcessor


class ProcessorNotConfigured(ServiceUnavailable, ValueError):
    """The selected processor lacks required settings.

    Also a ValueError so callers that relied on the historical Paystack startup error
    (`ValueError("... PAYSTACK_SECRET_KEY is empty.")`) keep working.
    """


@dataclass(frozen=True, slots=True)
class ProcessorPlugin:
    name: str
    # (settings attribute, env var name) pairs that must all be non-empty to register.
    required: tuple[tuple[str, str], ...]
    build: Callable[[Settings], Any]
    priority: int = 100
    # Builds the adapter used to validate one callback kind. None = no callbacks.
    build_webhook: Callable[[Settings, WebhookKind], Any] | None = None
    # Callback validation may need different settings than API calls (Paystack accepts a
    # dedicated webhook secret). Defaults to `is_configured`.
    webhook_configured: Callable[[Settings], bool] | None = None

    def missing(self, settings: Settings) -> list[str]:
        return [
            env_name
            for attribute, env_name in self.required
            if not str(getattr(settings, attribute, "") or "").strip()
        ]

    def is_configured(self, settings: Settings) -> bool:
        return not self.missing(settings)

    def can_build_webhook(self, settings: Settings) -> bool:
        if self.build_webhook is None:
            return False
        check = self.webhook_configured or self.is_configured
        return check(settings)


# -- builders --------------------------------------------------------------------------


def _paystack(settings: Settings) -> PaystackPaymentProcessor:
    return PaystackPaymentProcessor(
        secret_key=settings.paystack_secret_key,
        base_url=settings.paystack_base_url,
        webhook_secret=settings.paystack_webhook_secret or None,
    )


def _paystack_webhook(settings: Settings, kind: WebhookKind) -> PaystackPaymentProcessor:
    secret = settings.paystack_webhook_secret or settings.paystack_secret_key
    return PaystackPaymentProcessor(
        secret_key=settings.paystack_secret_key or secret,
        base_url=settings.paystack_base_url,
        webhook_secret=secret,
    )


def _pawapay(settings: Settings) -> PawapayPaymentProcessor:
    return PawapayPaymentProcessor(
        api_token=settings.pawapay_api_token,
        base_url=settings.pawapay_base_url,
    )


def _pawapay_callback_url(settings: Settings, kind: WebhookKind) -> str | None:
    specific = {
        WebhookKind.PAYMENT: settings.pawapay_deposit_callback_url,
        WebhookKind.REFUND: settings.pawapay_refund_callback_url,
        WebhookKind.PAYOUT: settings.pawapay_payout_callback_url,
    }[kind]
    return specific or settings.pawapay_callback_url or None


def _pawapay_webhook(settings: Settings, kind: WebhookKind) -> PawapayPaymentProcessor:
    # PawaPay signs the exact URL configured in its dashboard, one per callback kind.
    return PawapayPaymentProcessor(
        api_token=settings.pawapay_api_token,
        base_url=settings.pawapay_base_url,
        callback_url=_pawapay_callback_url(settings, kind),
        callback_signature_required=settings.pawapay_callback_signature_required,
        public_keys_url=settings.pawapay_public_keys_url or None,
    )


def _cinetpay(settings: Settings) -> CinetpayPaymentProcessor:
    return CinetpayPaymentProcessor(
        api_key=settings.cinetpay_api_key,
        api_password=settings.cinetpay_api_password,
        country=settings.cinetpay_country,
        base_url=settings.cinetpay_base_url or None,
        webhook_url=settings.cinetpay_webhook_url or None,
    )


def _jeko(settings: Settings) -> JekoPaymentProcessor:
    return JekoPaymentProcessor(
        api_key=settings.jeko_api_key,
        api_key_id=settings.jeko_api_key_id,
        store_id=settings.jeko_store_id,
        base_url=settings.jeko_base_url,
        webhook_secret=settings.jeko_webhook_secret or None,
    )


# Priorities only matter for registry fallback callers; API operations always pass the
# configured PAYMENT_PROCESSOR_MODE or an explicit request-level processor.
PLUGINS: tuple[ProcessorPlugin, ...] = (
    # Free, no credentials: always registered so reconciliation can resolve any attempt
    # stamped "sandbox", whatever the current mode (SCRUM-511).
    ProcessorPlugin(name="sandbox", required=(), build=lambda settings: SandboxPaymentProcessor()),
    ProcessorPlugin(
        name="paystack",
        required=(("paystack_secret_key", "PAYSTACK_SECRET_KEY"),),
        build=_paystack,
        build_webhook=_paystack_webhook,
        webhook_configured=lambda s: bool(s.paystack_webhook_secret or s.paystack_secret_key),
    ),
    # Shares the PawaPay account with the legacy wallet adapter (wallet/infra/pawapay_gateway.py).
    ProcessorPlugin(
        name="pawapay",
        required=(("pawapay_api_token", "PAWAPAY_API_TOKEN"),),
        build=_pawapay,
        priority=50,
        build_webhook=_pawapay_webhook,
    ),
    # Needs BOTH api_key and api_password; an api_key alone cannot authenticate.
    ProcessorPlugin(
        name="cinetpay",
        required=(
            ("cinetpay_api_key", "CINETPAY_API_KEY"),
            ("cinetpay_api_password", "CINETPAY_API_PASSWORD"),
        ),
        build=_cinetpay,
        priority=110,
        build_webhook=lambda settings, kind: _cinetpay(settings),
    ),
    # Needs api_key + api_key_id + store_id; no call works without a store_id.
    ProcessorPlugin(
        name="jeko",
        required=(
            ("jeko_api_key", "JEKO_API_KEY"),
            ("jeko_api_key_id", "JEKO_API_KEY_ID"),
            ("jeko_store_id", "JEKO_STORE_ID"),
        ),
        build=_jeko,
        priority=120,
    ),
)

_BY_NAME = {plugin.name: plugin for plugin in PLUGINS}


def plugin_named(name: str) -> ProcessorPlugin | None:
    return _BY_NAME.get(name.strip().lower())


def validate_selected_processor(settings: Settings) -> None:
    """Fail clearly when PAYMENT_PROCESSOR_MODE names a processor that is not configured."""
    plugin = plugin_named(settings.payment_processor_mode)
    if plugin is None:
        return  # unknown modes are reported by the routing layer, as before
    missing = plugin.missing(settings)
    if missing:
        raise ProcessorNotConfigured(
            f"PAYMENT_PROCESSOR_MODE={plugin.name} but {', '.join(missing)} is empty.",
            code="PROCESSOR_NOT_CONFIGURED",
            details={"processor": plugin.name, "missing": missing},
        )


def build_registry(settings: Settings, registry) -> None:
    """Register every configured processor (not only the current mode, see SCRUM-511)."""
    validate_selected_processor(settings)
    for plugin in PLUGINS:
        if plugin.is_configured(settings):
            registry.register(plugin.build(settings), priority=plugin.priority)


def build_webhook_processor(name: str, kind: WebhookKind, settings: Settings):
    """Adapter that validates `kind` callbacks for processor `name`, or 503 if unusable."""
    plugin = plugin_named(name)
    if plugin is None or not plugin.can_build_webhook(settings):
        raise ServiceUnavailable(
            f"Webhook {name} non configure.",
            code="PROCESSOR_NOT_CONFIGURED",
            details={"provider": name},
        )
    return plugin.build_webhook(settings, kind)

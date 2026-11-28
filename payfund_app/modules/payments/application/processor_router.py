"""Capability-based selection of payment processor adapters."""

from __future__ import annotations

from payfund_app.modules.payments.application.ports import (
    PaymentDirection,
    PaymentProcessorPort,
    WebhookKind,
)


class ProcessorRoutingError(LookupError):
    pass


class ProcessorCapabilityMissing(ProcessorRoutingError):
    """The processor is registered but does not declare the requested capability."""

    def __init__(self, processor: str, capability: str) -> None:
        super().__init__(f"processor {processor!r} does not support {capability}")
        self.processor = processor
        self.capability = capability


# Processors whose outcomes never reach a real PSP -- fine for local/test, never for an
# environment where a client might believe money actually moved. Checked by name rather than a
# capability flag because the whole point is that these report success without telling the
# registry they're not real (see SandboxPaymentProcessor).
SIMULATED_PROCESSOR_NAMES = frozenset({"sandbox"})


class ProcessorRegistry:
    """Selects a processor before an attempt starts; it never retries an attempt."""

    def __init__(self) -> None:
        self._processors: dict[str, tuple[int, PaymentProcessorPort]] = {}

    def register(self, processor: PaymentProcessorPort, *, priority: int = 100) -> None:
        if processor.name in self._processors:
            raise ValueError(f"processor {processor.name!r} is already registered")
        self._processors[processor.name] = (priority, processor)

    def get(self, name: str) -> PaymentProcessorPort:
        try:
            return self._processors[name][1]
        except KeyError as exc:
            raise ProcessorRoutingError(f"processor {name!r} is not registered") from exc

    def get_capable(self, name: str, direction: PaymentDirection) -> PaymentProcessorPort:
        """Return the named processor only if it declares `direction` (e.g. REFUND)."""
        processor = self.get(name)
        if not processor.capabilities.can(direction):
            raise ProcessorCapabilityMissing(name, f"{direction} operations")
        return processor

    def get_receiver(self, name: str, kind: WebhookKind) -> PaymentProcessorPort:
        """Return the named processor only if it validates `kind` callbacks."""
        processor = self.get(name)
        require_webhook(processor, kind)
        return processor

    def ordered(self) -> tuple[PaymentProcessorPort, ...]:
        """All registered processors sorted by (priority, name) -- the same order `select()`
        falls back to with no explicit preference. Exposed so callers can reason about which
        processor would win on its own merits, e.g. to find a more specific match than the
        globally-configured default (see PaymentUseCases.create())."""
        return tuple(
            processor
            for _, processor in sorted(
                self._processors.values(), key=lambda item: (item[0], item[1].name)
            )
        )

    def select(
        self,
        *,
        currency: str,
        direction: PaymentDirection,
        channel: str | None = None,
        network: str | None = None,
        preferred_processor: str | None = None,
    ) -> PaymentProcessorPort:
        candidates = [self.get(preferred_processor)] if preferred_processor else self.ordered()

        for processor in candidates:
            if processor.capabilities.supports(
                currency=currency,
                direction=direction,
                channel=channel,
                network=network,
            ):
                return processor
        raise ProcessorRoutingError(
            "no processor supports "
            f"direction={direction} currency={currency} channel={channel} network={network}"
        )

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._processors))


def require_webhook(processor: PaymentProcessorPort, kind: WebhookKind) -> None:
    """Guard for webhook use cases that receive a processor built outside the registry."""
    if not processor.capabilities.receives(kind):
        raise ProcessorCapabilityMissing(processor.name, f"{kind} callbacks")

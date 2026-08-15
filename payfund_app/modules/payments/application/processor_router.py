"""Capability-based selection of payment processor adapters."""

from __future__ import annotations

from payfund_app.modules.payments.application.ports import (
    PaymentDirection,
    PaymentProcessorPort,
)


class ProcessorRoutingError(LookupError):
    pass


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

    def select(
        self,
        *,
        currency: str,
        direction: PaymentDirection,
        channel: str | None = None,
        network: str | None = None,
        preferred_processor: str | None = None,
    ) -> PaymentProcessorPort:
        if preferred_processor:
            candidates = [self.get(preferred_processor)]
        else:
            candidates = [
                processor
                for _, processor in sorted(
                    self._processors.values(), key=lambda item: (item[0], item[1].name)
                )
            ]

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

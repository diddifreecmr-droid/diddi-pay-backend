"""Errors raised by the provider-neutral payment domain."""

from __future__ import annotations

from enum import StrEnum


class PaymentDomainError(ValueError):
    """Base class for deterministic payment business-rule failures."""


class InvalidAmount(PaymentDomainError):
    pass


class InvalidCurrency(PaymentDomainError):
    pass


class InvalidStateTransition(PaymentDomainError):
    def __init__(self, entity: str, current: StrEnum, target: StrEnum) -> None:
        super().__init__(f"{entity} cannot transition from {current} to {target}")
        self.entity = entity
        self.current = current
        self.target = target

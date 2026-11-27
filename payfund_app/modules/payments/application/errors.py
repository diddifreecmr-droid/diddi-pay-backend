"""Application-level payment orchestration errors."""


class PaymentApplicationError(Exception):
    pass


class PaymentNotFound(PaymentApplicationError):
    pass


class PaymentVerificationFailed(PaymentApplicationError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class IdempotencyConflict(PaymentApplicationError):
    pass


class PaymentOperationConflict(PaymentApplicationError):
    pass


class PersistenceConflict(PaymentApplicationError):
    pass


class ProcessorRequestRejected(PaymentApplicationError):
    pass


class ProcessorCallUncertain(PaymentApplicationError):
    def __init__(self, provider_reference: str, message: str) -> None:
        super().__init__(message)
        self.provider_reference = provider_reference


class ProcessorWebhookRejected(PaymentApplicationError):
    pass


class CallbackDeliveryFailed(PaymentApplicationError):
    """The receiving module did not durably accept an outbox event."""

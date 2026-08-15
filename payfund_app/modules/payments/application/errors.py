"""Application-level payment orchestration errors."""


class PaymentApplicationError(Exception):
    pass


class PaymentNotFound(PaymentApplicationError):
    pass


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

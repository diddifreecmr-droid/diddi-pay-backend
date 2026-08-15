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

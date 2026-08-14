"""Erreurs métier de `wallet`, avec les codes exacts du contrat API."""

from payfund_app.core.errors import AppError


class AccountNotFound(AppError):
    status_code = 404
    code = "ACCOUNT_NOT_FOUND"
    message = "Compte introuvable."


class RecipientNotFound(AppError):
    status_code = 404
    code = "RECIPIENT_NOT_FOUND"
    message = "Destinataire introuvable."


class MerchantNotFound(AppError):
    status_code = 404
    code = "MERCHANT_NOT_FOUND"
    message = "Marchand introuvable."


class TransactionNotFound(AppError):
    status_code = 404
    code = "TRANSACTION_NOT_FOUND"
    message = "Transaction introuvable."


class InsufficientBalance(AppError):
    status_code = 409
    code = "INSUFFICIENT_BALANCE"
    message = "Solde insuffisant."


class AccountNotActive(AppError):
    status_code = 409
    code = "ACCOUNT_NOT_ACTIVE"
    message = "Compte gelé ou clôturé : opération refusée."


class CannotTransferToSelf(AppError):
    status_code = 422
    code = "CANNOT_TRANSFER_TO_SELF"
    message = "Un transfert vers soi-même est impossible."


class InvalidAmountError(AppError):
    status_code = 422
    code = "INVALID_AMOUNT"
    message = "Montant invalide."


class GatewayUnavailable(AppError):
    status_code = 502
    code = "GATEWAY_UNAVAILABLE"
    message = "Passerelle Mobile Money indisponible."


class ExchangeRateUnavailable(AppError):
    """Aucune cotation connue : on refuse la conversion plutôt que d'inventer un taux."""

    status_code = 503
    code = "EXCHANGE_RATE_UNAVAILABLE"
    message = "Aucun taux de change disponible pour cette paire de devises."


class SameCurrencyConversion(AppError):
    status_code = 422
    code = "SAME_CURRENCY_CONVERSION"
    message = "Les deux comptes sont dans la même devise : aucune conversion nécessaire."


class CurrencyMismatch(AppError):
    status_code = 422
    code = "CURRENCY_MISMATCH"
    message = "Comptes de devises différentes : passer par une conversion."


class IdempotencyKeyRequired(AppError):
    status_code = 400
    code = "IDEMPOTENCY_KEY_REQUIRED"
    message = "En-tête `Idempotency-Key` obligatoire sur cette route."


class InvalidQrCodeError(AppError):
    status_code = 422
    code = "INVALID_QR_CODE"
    message = "QR code invalide ou altéré."


class QrCodeExpiredError(AppError):
    status_code = 410
    code = "QR_CODE_EXPIRED"
    message = "Ce QR code a expiré."


class NotMerchantAccountOwner(AppError):
    status_code = 403
    code = "NOT_MERCHANT_ACCOUNT_OWNER"
    message = "Seul le propriétaire du compte marchand peut générer son QR code."


class PinRequired(AppError):
    status_code = 409
    code = "PIN_REQUIRED"
    message = "Un PIN transactionnel est requis."


class InvalidPin(AppError):
    status_code = 403
    code = "INVALID_PIN"
    message = "PIN transactionnel invalide."


class PinLocked(AppError):
    status_code = 423
    code = "PIN_LOCKED"
    message = "PIN temporairement bloqué."


class PinAlreadySet(AppError):
    status_code = 409
    code = "PIN_ALREADY_SET"
    message = "Un PIN existe déjà. Utilisez la route de changement ou de récupération."


class StepUpProofAlreadyUsed(AppError):
    status_code = 409
    code = "STEP_UP_PROOF_ALREADY_USED"
    message = "Cette preuve de ré-authentification a déjà été utilisée."


class RecoveryCodeInvalid(AppError):
    status_code = 400
    code = "RECOVERY_CODE_INVALID"
    message = "Code de récupération invalide."


class StepUpOtpRequired(AppError):
    status_code = 409
    code = "STEP_UP_OTP_REQUIRED"
    message = "Une vérification OTP supplémentaire est requise."


class InvalidStepUpOtp(AppError):
    status_code = 403
    code = "INVALID_STEP_UP_OTP"
    message = "OTP de vérification invalide."


class StepUpOtpExpired(AppError):
    status_code = 410
    code = "STEP_UP_OTP_EXPIRED"
    message = "OTP de vérification expiré."

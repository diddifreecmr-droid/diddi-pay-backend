"""Génération et vérification du QR code de paiement marchand (Contrat API §1, §3).

Ni l'une ni l'autre opération ne déplace de fonds : ce sont des lectures/signatures, pas des
routes soumises à `Idempotency-Key` (§0). Le paiement effectif reste `POST /wallet/pay/merchant`,
inchangé — ce service ne fait qu'encoder et décoder ce qui alimente son `merchant_account_id`
(et, pour un QR à montant fixe, son `amount`).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from payfund_app.core.config import get_settings
from payfund_app.modules.wallet.application.use_cases import to_money
from payfund_app.modules.wallet.domain.entities import AccountStatus, AccountType
from payfund_app.modules.wallet.domain.errors import (
    AccountNotActive,
    InvalidAmountError,
    InvalidQrCodeError,
    MerchantNotFound,
    NotMerchantAccountOwner,
    QrCodeExpiredError,
)
from payfund_app.modules.wallet.domain.qr import (
    InvalidQrCode,
    QrCodeExpired,
    QrPayload,
    sign,
    verify,
)
from payfund_app.modules.wallet.infra.models import Account
from payfund_app.modules.wallet.infra.repositories import AccountRepository

# Plafond d'expiration d'un QR à montant fixe, indépendant de ce que le client demande.
MAX_DYNAMIC_QR_TTL_SECONDS = 24 * 3600


class QrService:
    def __init__(self, session: Session) -> None:
        self.accounts = AccountRepository(session)
        self.secret = get_settings().qr_signing_secret

    def _merchant_actif(self, merchant_account_id: uuid.UUID) -> Account:
        merchant = self.accounts.get(merchant_account_id)
        if merchant is None or merchant.account_type != str(AccountType.MERCHANT):
            raise MerchantNotFound()
        if merchant.status != str(AccountStatus.ACTIVE):
            raise AccountNotActive("Ce compte marchand n'accepte pas de paiement.")
        return merchant

    def generer(
        self,
        *,
        requester_user_id: uuid.UUID,
        merchant_account_id: uuid.UUID,
        amount: int | None,
        currency: str,
        origin_module: str | None,
        expires_in_seconds: int | None,
    ) -> tuple[str, QrPayload]:
        """Génère le QR d'un compte marchand. Seul son propriétaire peut le générer — sans quoi
        n'importe qui pourrait imprimer un QR pointant vers le compte d'un tiers."""
        merchant = self._merchant_actif(merchant_account_id)
        if merchant.user_id != requester_user_id:
            raise NotMerchantAccountOwner()

        montant = None
        if amount is not None:
            montant = to_money(amount, currency)
            if not montant.is_positive():
                raise InvalidAmountError("Le montant doit être strictement positif.")

        if expires_in_seconds is not None and montant is None:
            raise InvalidQrCodeError(
                "Une expiration nécessite un montant fixe (QR à montant fixe, pas statique)."
            )

        expires_at = None
        if expires_in_seconds is not None:
            ttl = min(expires_in_seconds, MAX_DYNAMIC_QR_TTL_SECONDS)
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)

        payload = QrPayload(
            merchant_account_id=merchant.id,
            currency=montant.currency if montant else currency,
            amount=montant.amount if montant else None,
            origin_module=origin_module,
            expires_at=expires_at,
            nonce=str(uuid.uuid4()),
        )
        token = sign(payload, self.secret)
        return token, payload

    def verifier(self, token: str) -> tuple[QrPayload, Account]:
        """Décode un QR scanné et vérifie que le compte visé est toujours un marchand actif —
        un compte peut avoir été gelé après l'impression du QR."""
        try:
            payload = verify(token, self.secret)
        except QrCodeExpired as exc:
            raise QrCodeExpiredError(str(exc)) from exc
        except InvalidQrCode as exc:
            raise InvalidQrCodeError(str(exc)) from exc

        merchant = self._merchant_actif(payload.merchant_account_id)
        return payload, merchant

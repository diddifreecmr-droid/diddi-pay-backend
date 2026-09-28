"""Adaptateurs des passerelles Mobile Money.

Architecture §7, étape 2 : « Implémenter `wallet` seul d'abord (compte, dépôt, retrait, ledger)
avec un provider Mobile Money simulé (stub) avant de brancher Orange Money/MTN réels. »

Ce que ce fichier ne décide **pas** : par quel canal l'opérateur nous notifie de l'issue d'une
opération (webhook HTTP entrant, ou job de polling de notre côté). Aucun des documents ne le
spécifie, et le contrat API n'expose aucune route de callback. La confirmation est donc pilotée
par les use cases `ConfirmerOperationPasserelle` / `EchouerOperationPasserelle`, qu'il suffira de
brancher sur le canal retenu au moment d'intégrer Orange Money et MTN pour de vrai.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import httpx

from payfund_app.core.config import get_settings

PROVIDERS = ("paystack", "orange_money", "mtn_momo", "wave", "moov", "card_gateway")
MODES = ("stub", "sandbox_orange_money", "sandbox_wave", "paystack", "pawapay")


class GatewayStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class GatewayOperation:
    provider_reference: str
    status: GatewayStatus
    authorization_url: str | None = None
    access_code: str | None = None
    amount: int | None = None
    currency: str | None = None


class PaymentGatewayPort(Protocol):
    # Doit correspondre à une entrée de MODES : c'est cette valeur qui est persistée sur
    # `Transaction.gateway_mode` et qui permet à `gateway_for_mode()` de retrouver le bon
    # adaptateur, plus tard, indépendamment du mode global courant.
    mode: str

    def supports_withdrawal(self, provider: str) -> bool: ...

    def initier_depot(
        self, *, provider: str, phone: str, email: str | None = None, montant: int, reference: str
    ) -> GatewayOperation: ...

    def initier_retrait(
        self, *, provider: str, phone: str, email: str | None = None, montant: int, reference: str
    ) -> GatewayOperation: ...

    def verifier_depot(self, reference: str) -> GatewayOperation: ...

    def verifier_retrait(self, reference: str) -> GatewayOperation: ...


class StubGateway:
    """Passerelle simulée générique.

    Par défaut elle renvoie `pending`, comme le ferait un vrai opérateur en attente de callback.
    `PAYMENT_GATEWAY_AUTOCONFIRM=true` la fait répondre `completed` tout de suite, pour travailler
    en local sans simuler le retour de l'opérateur.
    """

    mode = "stub"

    def __init__(self, autoconfirm: bool | None = None) -> None:
        self.autoconfirm = (
            get_settings().payment_gateway_autoconfirm if autoconfirm is None else autoconfirm
        )

    def _operation(self) -> GatewayOperation:
        return GatewayOperation(
            provider_reference=f"stub-{uuid.uuid4()}",
            status=GatewayStatus.COMPLETED if self.autoconfirm else GatewayStatus.PENDING,
        )

    def supports_withdrawal(self, provider: str) -> bool:
        return provider in PROVIDERS

    def initier_depot(
        self, *, provider: str, phone: str, email: str | None = None, montant: int, reference: str
    ) -> GatewayOperation:
        return self._operation()

    def initier_retrait(
        self, *, provider: str, phone: str, email: str | None = None, montant: int, reference: str
    ) -> GatewayOperation:
        return self._operation()

    def verifier_depot(self, reference: str) -> GatewayOperation:
        return self._operation()

    def verifier_retrait(self, reference: str) -> GatewayOperation:
        return self._operation()


class OrangeMoneySandboxGateway(StubGateway):
    """Sandbox explicite pour Orange Money.

    Ce mode garde les mêmes statuts que le stub, mais il rend visible le rail testé afin que les
    futurs appels réels Orange Money puissent se brancher sans changer les use cases du wallet.
    """

    mode = "sandbox_orange_money"
    provider_name = "orange_money"

    def supports_withdrawal(self, provider: str) -> bool:
        return provider == self.provider_name

    def _ensure_provider(self, provider: str) -> None:
        if provider != self.provider_name:
            raise NotImplementedError(
                f"Sandbox Orange Money non disponible pour le provider {provider!r}."
            )

    def initier_depot(
        self, *, provider: str, phone: str, email: str | None = None, montant: int, reference: str
    ) -> GatewayOperation:
        self._ensure_provider(provider)
        return GatewayOperation(
            provider_reference=f"orange-money-sandbox-deposit-{uuid.uuid4()}",
            status=GatewayStatus.COMPLETED if self.autoconfirm else GatewayStatus.PENDING,
        )

    def initier_retrait(
        self, *, provider: str, phone: str, montant: int, reference: str
    ) -> GatewayOperation:
        self._ensure_provider(provider)
        return GatewayOperation(
            provider_reference=f"orange-money-sandbox-withdraw-{uuid.uuid4()}",
            status=GatewayStatus.COMPLETED if self.autoconfirm else GatewayStatus.PENDING,
        )

    def verifier_depot(self, reference: str) -> GatewayOperation:
        return GatewayOperation(
            provider_reference=reference,
            status=GatewayStatus.PENDING if not self.autoconfirm else GatewayStatus.COMPLETED,
        )

    def verifier_retrait(self, reference: str) -> GatewayOperation:
        return GatewayOperation(
            provider_reference=reference,
            status=GatewayStatus.PENDING if not self.autoconfirm else GatewayStatus.COMPLETED,
        )


class WaveSandboxGateway(StubGateway):
    """Sandbox explicite pour Wave.

    On garde la même mécanique que le stub, mais avec un rail visible pour préparer l'intégration
    réelle sans changer les use cases du wallet.
    """

    mode = "sandbox_wave"
    provider_name = "wave"

    def supports_withdrawal(self, provider: str) -> bool:
        return provider == self.provider_name

    def _ensure_provider(self, provider: str) -> None:
        if provider != self.provider_name:
            raise NotImplementedError(f"Sandbox Wave non disponible pour le provider {provider!r}.")

    def initier_depot(
        self, *, provider: str, phone: str, email: str | None = None, montant: int, reference: str
    ) -> GatewayOperation:
        self._ensure_provider(provider)
        return GatewayOperation(
            provider_reference=f"wave-sandbox-deposit-{uuid.uuid4()}",
            status=GatewayStatus.COMPLETED if self.autoconfirm else GatewayStatus.PENDING,
        )

    def initier_retrait(
        self, *, provider: str, phone: str, email: str | None = None, montant: int, reference: str
    ) -> GatewayOperation:
        self._ensure_provider(provider)
        return GatewayOperation(
            provider_reference=f"wave-sandbox-withdraw-{uuid.uuid4()}",
            status=GatewayStatus.COMPLETED if self.autoconfirm else GatewayStatus.PENDING,
        )

    def verifier_depot(self, reference: str) -> GatewayOperation:
        return GatewayOperation(
            provider_reference=reference,
            status=GatewayStatus.PENDING if not self.autoconfirm else GatewayStatus.COMPLETED,
        )

    def verifier_retrait(self, reference: str) -> GatewayOperation:
        return GatewayOperation(
            provider_reference=reference,
            status=GatewayStatus.PENDING if not self.autoconfirm else GatewayStatus.COMPLETED,
        )


class PaystackGateway:
    """Adapter Paystack pour les dépôts wallet.

    Paystack initialise le paiement côté backend, puis la confirmation définitive revient par
    webhook signé. On garde le provider agnostique dans le wallet : la transaction DiddiPay reste
    la source de vérité.
    """

    mode = "paystack"

    def __init__(self) -> None:
        settings = get_settings()
        self.secret_key = settings.paystack_secret_key.strip()
        self.base_url = settings.paystack_base_url.rstrip("/")
        if not self.secret_key:
            raise RuntimeError("PAYSTACK_SECRET_KEY manquant.")

    def supports_withdrawal(self, provider: str) -> bool:
        return False

    def initier_depot(
        self,
        *,
        provider: str,
        phone: str,
        email: str | None = None,
        montant: int,
        reference: str,
    ) -> GatewayOperation:
        payload = {
            "email": email or f"{reference}@diddipay.local",
            # Paystack expects XOF multiplied by 100 even though XOF has no subunit.
            "amount": str(self.to_provider_xof(montant)),
            "currency": "XOF",
            "reference": reference,
            "metadata": {"phone": phone, "provider": provider, "wallet_reference": reference},
        }
        with httpx.Client(timeout=20.0) as client:
            response = client.post(
                f"{self.base_url}/transaction/initialize",
                headers={"Authorization": f"Bearer {self.secret_key}"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        if not data.get("status"):
            raise RuntimeError(data.get("message") or "Paystack initialize failed.")
        body = data["data"]
        return GatewayOperation(
            provider_reference=body["reference"],
            status=GatewayStatus.PENDING,
            authorization_url=body.get("authorization_url"),
            access_code=body.get("access_code"),
        )

    def initier_retrait(
        self, *, provider: str, phone: str, email: str | None = None, montant: int, reference: str
    ) -> GatewayOperation:
        raise NotImplementedError("Paystack withdraw not implemented yet.")

    def verifier_retrait(self, reference: str) -> GatewayOperation:
        raise NotImplementedError("Paystack withdraw verification not implemented yet.")

    def verifier_depot(self, reference: str) -> GatewayOperation:
        with httpx.Client(timeout=20.0) as client:
            response = client.get(
                f"{self.base_url}/transaction/verify/{reference}",
                headers={"Authorization": f"Bearer {self.secret_key}"},
            )
            response.raise_for_status()
            data = response.json()

        if not data.get("status"):
            raise RuntimeError(data.get("message") or "Paystack verify failed.")
        body = data["data"]
        status = str(body.get("status") or "").lower()
        if status == "success":
            gw_status = GatewayStatus.COMPLETED
        elif status in {"failed", "abandoned"}:
            gw_status = GatewayStatus.FAILED
        else:
            gw_status = GatewayStatus.PENDING
        return GatewayOperation(
            provider_reference=body["reference"],
            status=gw_status,
            authorization_url=body.get("authorization_url"),
            access_code=body.get("access_code"),
            amount=self.from_provider_xof(body.get("amount")),
            currency=str(body.get("currency") or "").upper() or None,
        )

    @staticmethod
    def to_provider_xof(amount: int) -> int:
        return amount * 100

    @staticmethod
    def from_provider_xof(value: object) -> int | None:
        if value is None:
            return None
        amount, remainder = divmod(int(value), 100)
        if remainder:
            raise ValueError("Paystack returned a fractional XOF payment amount")
        return amount


def gateway_for_mode(mode: str) -> PaymentGatewayPort:
    """Résout un adaptateur par mode, indépendamment du réglage global courant.

    Point de généralisation clé : utilisé aussi bien pour choisir la passerelle *active*
    (`get_gateway()`, ci-dessous) que pour retrouver, plus tard, l'adaptateur qui a traité une
    transaction *donnée* via `Transaction.gateway_mode` — même si le mode global a changé entre
    temps (voir `WalletUseCases.reconcile_transaction`). Ajouter un processeur = ajouter une classe
    et une branche ici ; rien d'autre n'a besoin de connaître la liste des passerelles.
    """
    if mode == "stub":
        return StubGateway()
    if mode == "sandbox_orange_money":
        return OrangeMoneySandboxGateway()
    if mode == "sandbox_wave":
        return WaveSandboxGateway()
    if mode == "paystack":
        return PaystackGateway()
    if mode == "pawapay":
        # Import différé : pawapay_gateway importe GatewayOperation/GatewayStatus depuis ce
        # module, un import en tête de fichier créerait un cycle.
        from payfund_app.modules.wallet.infra.pawapay_gateway import PawapayGateway

        return PawapayGateway()
    # Les adaptateurs réels (MTN, Wave, Moov, cartes) viendront ici.
    raise NotImplementedError(f"Passerelle non implémentée : {mode!r}")


def get_gateway() -> PaymentGatewayPort:
    return gateway_for_mode(get_settings().payment_gateway_mode)

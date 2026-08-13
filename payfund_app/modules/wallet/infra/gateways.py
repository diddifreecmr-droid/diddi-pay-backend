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

from payfund_app.core.config import get_settings

PROVIDERS = ("orange_money", "mtn_momo", "wave", "moov", "card_gateway")
MODES = ("stub", "sandbox_orange_money")


class GatewayStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class GatewayOperation:
    provider_reference: str
    status: GatewayStatus


class PaymentGatewayPort(Protocol):
    def initier_depot(
        self, *, provider: str, phone: str, montant: int, reference: str
    ) -> GatewayOperation: ...

    def initier_retrait(
        self, *, provider: str, phone: str, montant: int, reference: str
    ) -> GatewayOperation: ...


class StubGateway:
    """Passerelle simulée générique.

    Par défaut elle renvoie `pending`, comme le ferait un vrai opérateur en attente de callback.
    `PAYMENT_GATEWAY_AUTOCONFIRM=true` la fait répondre `completed` tout de suite, pour travailler
    en local sans simuler le retour de l'opérateur.
    """

    def __init__(self, autoconfirm: bool | None = None) -> None:
        self.autoconfirm = (
            get_settings().payment_gateway_autoconfirm if autoconfirm is None else autoconfirm
        )

    def _operation(self) -> GatewayOperation:
        return GatewayOperation(
            provider_reference=f"stub-{uuid.uuid4()}",
            status=GatewayStatus.COMPLETED if self.autoconfirm else GatewayStatus.PENDING,
        )

    def initier_depot(
        self, *, provider: str, phone: str, montant: int, reference: str
    ) -> GatewayOperation:
        return self._operation()

    def initier_retrait(
        self, *, provider: str, phone: str, montant: int, reference: str
    ) -> GatewayOperation:
        return self._operation()


class OrangeMoneySandboxGateway(StubGateway):
    """Sandbox explicite pour Orange Money.

    Ce mode garde les mêmes statuts que le stub, mais il rend visible le rail testé afin que les
    futurs appels réels Orange Money puissent se brancher sans changer les use cases du wallet.
    """

    provider_name = "orange_money"

    def _ensure_provider(self, provider: str) -> None:
        if provider != self.provider_name:
            raise NotImplementedError(
                f"Sandbox Orange Money non disponible pour le provider {provider!r}."
            )

    def initier_depot(
        self, *, provider: str, phone: str, montant: int, reference: str
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


def get_gateway() -> PaymentGatewayPort:
    mode = get_settings().payment_gateway_mode
    if mode == "stub":
        return StubGateway()
    if mode == "sandbox_orange_money":
        return OrangeMoneySandboxGateway()
    # Les adaptateurs réels (Orange Money, MTN, Wave, Moov, cartes) viendront ici.
    raise NotImplementedError(f"Passerelle non implémentée : {mode!r}")

"""Adaptateur PawaPay (Mobile Money XOF) pour le wallet legacy.

Suit le même contrat (`PaymentGatewayPort`) que `PaystackGateway` dans `gateways.py`.
Sélectionnable via `PAYMENT_GATEWAY_MODE=pawapay` (voir `get_gateway()`), et exige
`PAWAPAY_API_TOKEN`.

PawaPay confirme les dépôts et retraits de façon asynchrone : la réponse à l'appel
d'initiation ne donne que `ACCEPTED`/`REJECTED`/`DUPLICATE_IGNORED` ; le statut définitif
(`COMPLETED`/`FAILED`) arrive ensuite par callback HTTP ou par `verifier_depot`. Comme pour
Paystack, le wallet reste agnostique : la transaction DiddiPay demeure la source de vérité.

PawaPay identifie l'opérateur par un code pays+provider (ex. `MTN_MOMO_CIV`), alors que le
wallet ne connaît qu'un provider générique (`mtn_momo`, `orange_money`, `moov`). Le pays est
donc déduit de l'indicatif téléphonique du payeur/bénéficiaire.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import httpx

from payfund_app.core.config import get_settings
from payfund_app.modules.wallet.infra.gateways import GatewayOperation, GatewayStatus

# Provider générique wallet -> famille d'opérateur PawaPay.
_PROVIDER_FAMILIES = {
    "mtn_momo": "MTN_MOMO",
    "orange_money": "ORANGE",
    "moov": "MOOV",
}

# Indicatif téléphonique (sans "+") -> code pays PawaPay.
_COUNTRY_BY_DIAL_CODE = {
    "225": "CIV",  # Côte d'Ivoire
    "229": "BEN",  # Bénin
    "226": "BFA",  # Burkina Faso
    "221": "SEN",  # Sénégal
}

# Combinaisons famille+pays effectivement actives sur le compte PawaPay (vérifié via
# GET /v2/active-conf). A tenir à jour si de nouveaux rails sont activés côté dashboard.
_SUPPORTED_OPERATORS = {
    "MOOV_BEN",
    "MTN_MOMO_BEN",
    "MOOV_BFA",
    "MOOV_CIV",
    "MTN_MOMO_CIV",
    "ORANGE_CIV",
    "ORANGE_SEN",
}


class PawapayGateway:
    """Adapter PawaPay pour les dépôts et retraits wallet (Mobile Money XOF)."""

    mode = "pawapay"

    def __init__(self) -> None:
        settings = get_settings()
        self.api_token = settings.pawapay_api_token.strip()
        self.base_url = settings.pawapay_base_url.rstrip("/")
        if not self.api_token:
            raise RuntimeError("PAWAPAY_API_TOKEN manquant.")

    def supports_withdrawal(self, provider: str) -> bool:
        return provider in _PROVIDER_FAMILIES

    def _resolve_operator(self, *, provider: str, phone: str) -> str:
        family = _PROVIDER_FAMILIES.get(provider)
        if family is None:
            raise NotImplementedError(f"PawaPay non disponible pour le provider {provider!r}.")

        digits = phone.lstrip("+").replace(" ", "")
        country = next(
            (c for code, c in _COUNTRY_BY_DIAL_CODE.items() if digits.startswith(code)),
            None,
        )
        if country is None:
            raise ValueError(f"Indicatif pays non reconnu pour PawaPay dans le numéro {phone!r}.")

        operator = f"{family}_{country}"
        if operator not in _SUPPORTED_OPERATORS:
            raise NotImplementedError(
                f"PawaPay : opérateur {operator!r} non activé sur ce compte "
                "(vérifier /v2/active-conf ou le dashboard PawaPay)."
            )
        return operator

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

    def initier_depot(
        self, *, provider: str, phone: str, email: str | None = None, montant: int, reference: str
    ) -> GatewayOperation:
        operator = self._resolve_operator(provider=provider, phone=phone)
        deposit_id = str(uuid.uuid4())
        payload = {
            "depositId": deposit_id,
            "payer": {
                "type": "MMO",
                "accountDetails": {"phoneNumber": digits_only(phone), "provider": operator},
            },
            # PawaPay attend un montant XOF entier, sans sous-unité (decimalsInAmount=NONE),
            # contrairement à Paystack qui multiplie artificiellement par 100.
            "amount": str(montant),
            "currency": "XOF",
            "clientReferenceId": reference,
        }
        with httpx.Client(timeout=20.0) as client:
            response = client.post(
                f"{self.base_url}/v2/deposits", headers=self._headers(), json=payload
            )
            response.raise_for_status()
            data = response.json()

        return GatewayOperation(
            provider_reference=deposit_id,
            status=self._map_initiate_status(data.get("status")),
        )

    def initier_retrait(
        self, *, provider: str, phone: str, email: str | None = None, montant: int, reference: str
    ) -> GatewayOperation:
        operator = self._resolve_operator(provider=provider, phone=phone)
        payout_id = str(uuid.uuid4())
        payload = {
            "payoutId": payout_id,
            "recipient": {
                "type": "MMO",
                "accountDetails": {"phoneNumber": digits_only(phone), "provider": operator},
            },
            "amount": str(montant),
            "currency": "XOF",
            "clientReferenceId": reference,
        }
        with httpx.Client(timeout=20.0) as client:
            response = client.post(
                f"{self.base_url}/v2/payouts", headers=self._headers(), json=payload
            )
            response.raise_for_status()
            data = response.json()

        return GatewayOperation(
            provider_reference=payout_id,
            status=self._map_initiate_status(data.get("status")),
        )

    def verifier_depot(self, reference: str) -> GatewayOperation:
        with httpx.Client(timeout=20.0) as client:
            response = client.get(
                f"{self.base_url}/v2/deposits/{reference}", headers=self._headers()
            )
            response.raise_for_status()
            data = response.json()

        if data.get("status") != "FOUND":
            raise RuntimeError(f"PawaPay : dépôt {reference!r} introuvable.")

        body = data["data"]
        return GatewayOperation(
            provider_reference=body["depositId"],
            status=self._map_final_status(body.get("status")),
            amount=self._parse_amount(body.get("amount")),
            currency=str(body.get("currency") or "").upper() or None,
        )

    def verifier_retrait(self, reference: str) -> GatewayOperation:
        with httpx.Client(timeout=20.0) as client:
            response = client.get(
                f"{self.base_url}/v2/payouts/{reference}", headers=self._headers()
            )
            response.raise_for_status()
            data = response.json()

        if data.get("status") != "FOUND":
            raise RuntimeError(f"PawaPay : retrait {reference!r} introuvable.")

        body = data["data"]
        return GatewayOperation(
            provider_reference=body["payoutId"],
            status=self._map_final_status(body.get("status")),
            amount=self._parse_amount(body.get("amount")),
            currency=str(body.get("currency") or "").upper() or None,
        )

    @staticmethod
    def _map_initiate_status(status: str | None) -> GatewayStatus:
        # ACCEPTED / DUPLICATE_IGNORED = la demande est prise en compte, le statut définitif
        # arrive plus tard (callback ou verifier_depot). REJECTED = refus immédiat (montant,
        # opérateur non joignable...).
        if status == "REJECTED":
            return GatewayStatus.FAILED
        return GatewayStatus.PENDING

    @staticmethod
    def _map_final_status(status: str | None) -> GatewayStatus:
        if status == "COMPLETED":
            return GatewayStatus.COMPLETED
        if status == "FAILED":
            return GatewayStatus.FAILED
        # SUBMITTED, PROCESSING, IN_RECONCILIATION, ACCEPTED -> toujours en attente.
        return GatewayStatus.PENDING

    @staticmethod
    def _parse_amount(value: object) -> int | None:
        if value is None:
            return None
        # PawaPay renvoie un montant décimal ("100.00") bien que le XOF n'ait pas de sous-unité.
        amount = Decimal(str(value))
        if amount != amount.to_integral_value():
            raise ValueError("PawaPay a renvoyé un montant XOF fractionnaire")
        return int(amount)


def digits_only(phone: str) -> str:
    return phone.lstrip("+").replace(" ", "")

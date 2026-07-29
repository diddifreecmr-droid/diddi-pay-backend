"""QR code de paiement marchand — payload signé.

Contrat API §1 : « paiement marchand par QR Code (le frontend scanne, obtient un
`merchant_account_id` encodé dans le QR) ». Le format exact est explicitement laissé ouvert par
le contrat (§3 : « à spécifier avec Frontend/Mobile une fois le composant scanner choisi »). Ce
module fixe un format concret.

Le payload est un jeton compact signé HMAC-SHA256 : `base64url(json).base64url(signature)`. La
signature empêche qu'un QR soit forgé ou altéré pour rediriger un paiement vers un autre compte,
sans qu'il soit nécessaire de le chiffrer — rien de confidentiel n'y transite, seulement une
référence de compte et, optionnellement, un montant.

`amount` absent = QR **statique** : c'est le cas décrit par le contrat, le payeur saisit le
montant dans l'app. `amount` présent = QR à montant fixe (facture), avec expiration optionnelle —
une **extension au-delà du contrat**, signalée comme telle. Ce n'est pas un QR à usage unique :
rien n'empêche qu'il serve pour plusieurs paiements avant expiration ; un vrai usage unique
demanderait de persister et consommer le jeton, non spécifié à ce stade.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

QR_VERSION = 1


class InvalidQrCode(Exception):
    """Format illisible ou signature invalide — QR forgé, altéré, ou d'un autre système."""


class QrCodeExpired(Exception):
    pass


@dataclass(frozen=True)
class QrPayload:
    merchant_account_id: uuid.UUID
    currency: str = "XOF"
    amount: int | None = None
    origin_module: str | None = None
    expires_at: datetime | None = None
    nonce: str = ""

    def _to_dict(self) -> dict:
        return {
            "v": QR_VERSION,
            "merchant_account_id": str(self.merchant_account_id),
            "currency": self.currency,
            "amount": self.amount,
            "origin_module": self.origin_module,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "nonce": self.nonce,
        }

    @classmethod
    def _from_dict(cls, data: dict) -> QrPayload:
        try:
            return cls(
                merchant_account_id=uuid.UUID(data["merchant_account_id"]),
                currency=data["currency"],
                amount=data.get("amount"),
                origin_module=data.get("origin_module"),
                expires_at=(
                    datetime.fromisoformat(data["expires_at"])
                    if data.get("expires_at")
                    else None
                ),
                nonce=data.get("nonce", ""),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise InvalidQrCode("Champs du QR code manquants ou illisibles.") from exc


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def sign(payload: QrPayload, secret: str) -> str:
    """Encode et signe un payload. C'est cette chaîne qui est encodée dans l'image du QR."""
    body = json.dumps(
        payload._to_dict(), separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    body_b64 = _b64encode(body)
    signature = hmac.new(
        secret.encode("utf-8"), body_b64.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{body_b64}.{_b64encode(signature)}"


def verify(token: str, secret: str) -> QrPayload:
    """Décode et vérifie un jeton scanné. Lève `InvalidQrCode` ou `QrCodeExpired`."""
    try:
        body_b64, signature_b64 = token.split(".", 1)
    except ValueError as exc:
        raise InvalidQrCode("Format de QR code invalide.") from exc

    expected = hmac.new(
        secret.encode("utf-8"), body_b64.encode("ascii"), hashlib.sha256
    ).digest()
    try:
        provided = _b64decode(signature_b64)
    except Exception as exc:
        raise InvalidQrCode("Signature illisible.") from exc

    # Comparaison à temps constant : éviter qu'un attaquant déduise la signature attendue
    # octet par octet en mesurant le temps de réponse.
    if not hmac.compare_digest(expected, provided):
        raise InvalidQrCode(
            "Signature invalide : le QR code a été altéré ou n'est pas authentique."
        )

    try:
        data = json.loads(_b64decode(body_b64))
    except Exception as exc:
        raise InvalidQrCode("Contenu du QR code illisible.") from exc

    if data.get("v") != QR_VERSION:
        raise InvalidQrCode(f"Version de QR code non supportée : {data.get('v')!r}.")

    payload = QrPayload._from_dict(data)
    if payload.expires_at is not None and payload.expires_at < datetime.now(timezone.utc):
        raise QrCodeExpired("Ce QR code a expiré.")
    return payload

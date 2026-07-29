"""Abonnements de `wallet` aux événements DiddiFreeID (contrat DiddiFreeID §4).

| Événement         | Effet côté wallet                                              |
|-------------------|----------------------------------------------------------------|
| `user.registered` | créer le compte wallet + indexer le téléphone                   |
| `user.updated`    | ré-indexer le téléphone s'il a changé                           |
| `user.suspended`  | passer le compte en `frozen` (gel des transactions sortantes)   |
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from payfund_app.core.database import SessionLocal
from payfund_app.modules.wallet.application.use_cases import WalletUseCases
from payfund_app.modules.wallet.domain.entities import AccountStatus
from payfund_app.modules.wallet.infra.repositories import (
    AccountRepository,
    UserPhoneRepository,
)
from payfund_app.shared_kernel.events.bus import EventBusPort
from payfund_app.shared_kernel.events.types import (
    USER_REGISTERED,
    USER_SUSPENDED,
    USER_UPDATED,
)

logger = logging.getLogger(__name__)


def _user_id(message: dict[str, Any]) -> uuid.UUID | None:
    raw = message.get("user_id")
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError):
        logger.warning("Événement %s sans user_id exploitable", message.get("event"))
        return None


def on_user_registered(message: dict[str, Any]) -> None:
    user_id = _user_id(message)
    if user_id is None:
        return
    with SessionLocal() as session:
        use_cases = WalletUseCases(session)
        use_cases.provisionner_compte(user_id)
        phone = message.get("phone")
        if phone:
            UserPhoneRepository(session).upsert(user_id, str(phone))
        session.commit()
        logger.info("Compte wallet provisionné pour %s", user_id)


def on_user_updated(message: dict[str, Any]) -> None:
    user_id = _user_id(message)
    phone = message.get("phone")
    if user_id is None or not phone:
        return
    with SessionLocal() as session:
        UserPhoneRepository(session).upsert(user_id, str(phone))
        session.commit()


def on_user_suspended(message: dict[str, Any]) -> None:
    """Gel immédiat, « sans attendre l'expiration du token en cours » (Architecture §5)."""
    user_id = _user_id(message)
    if user_id is None:
        return
    with SessionLocal() as session:
        accounts = AccountRepository(session)
        account = accounts.get_by_user(user_id)
        if account is None:
            logger.warning("user.suspended pour %s : aucun compte wallet", user_id)
            return
        accounts.set_status(account, AccountStatus.FROZEN)
        session.commit()
        logger.info("Compte wallet gelé pour %s", user_id)


def register(bus: EventBusPort) -> None:
    bus.subscribe(USER_REGISTERED, on_user_registered)
    bus.subscribe(USER_UPDATED, on_user_updated)
    bus.subscribe(USER_SUSPENDED, on_user_suspended)

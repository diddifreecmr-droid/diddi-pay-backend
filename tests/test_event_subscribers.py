"""Abonnements de `wallet` aux événements DiddiFreeID (§4 du contrat DiddiFreeID)."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from payfund_app.modules.wallet.domain.entities import AccountStatus
from payfund_app.modules.wallet.infra import subscribers
from payfund_app.modules.wallet.infra.models import Account
from payfund_app.modules.wallet.infra.repositories import (
    AccountRepository,
    UserPhoneRepository,
)
from payfund_app.shared_kernel.events.types import (
    USER_REGISTERED,
    USER_SUSPENDED,
    Event,
)


def test_user_registered_cree_le_compte_et_indexe_le_telephone(session, bus):
    """« l'utilisateur n'a jamais besoin d'un appel explicite "créer mon wallet" » (§5)."""
    subscribers.register(bus)
    user_id = uuid.uuid4()

    bus.publish(
        Event(USER_REGISTERED, {"user_id": str(user_id), "phone": "+2250700000000", "role": "user"})
    )

    session.expire_all()
    account = AccountRepository(session).get_by_user(user_id)
    assert account is not None
    assert account.status == str(AccountStatus.ACTIVE)
    assert UserPhoneRepository(session).user_id_for("+2250700000000") == user_id


def test_user_registered_redelivre_ne_cree_pas_un_second_compte(session, bus):
    subscribers.register(bus)
    user_id = uuid.uuid4()
    message = Event(USER_REGISTERED, {"user_id": str(user_id), "phone": "+2250700000000"})

    bus.publish(message)
    bus.publish(message)

    session.expire_all()
    comptes = list(session.scalars(select(Account).where(Account.user_id == user_id)))
    assert len(comptes) == 1


def test_user_suspended_gele_le_compte(session, bus, make_user):
    """Gel immédiat, sans attendre l'expiration du JWT en cours (§5)."""
    subscribers.register(bus)
    user_id, account_id = make_user()

    bus.publish(Event(USER_SUSPENDED, {"user_id": str(user_id), "reason": "ticket #883"}))

    session.expire_all()
    account = AccountRepository(session).get(account_id)
    assert account.status == str(AccountStatus.FROZEN)


def test_evenement_sans_user_id_est_ignore_sans_planter(session, bus):
    subscribers.register(bus)
    bus.publish(Event(USER_REGISTERED, {"phone": "+2250700000000"}))
    # Aucune exception ne doit remonter : un abonné en échec ne casse pas le bus.

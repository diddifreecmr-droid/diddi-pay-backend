"""Client du `WalletServicePort` côté `fund`.

Architecture §5 : implémentation **in-process** aujourd'hui — un appel de fonction direct vers
`wallet`, ce qui permet de partager la transaction DB et de rendre l'investissement atomique avec
son mouvement de fonds (Contrat API §2 : « les deux dans la même transaction DB »).

Le jour où `fund` devient un service séparé, **seul ce fichier change** (client HTTP) :
`fund/application` et `fund/domain` n'importent jamais `payfund_app.modules.wallet`.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from payfund_app.modules.wallet.infra.wallet_service import WalletService
from payfund_app.shared_kernel.contracts.wallet_provider import WalletServicePort


def get_wallet_service(session: Session) -> WalletServicePort:
    return WalletService(session)

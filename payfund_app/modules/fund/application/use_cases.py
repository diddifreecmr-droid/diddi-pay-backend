"""Use cases du module `fund`.

Périmètre implémenté : création, liste et détail de campagne, investissement.
Prêts (`simulate`, création, échéancier, remboursement) non implémentés — voir la note en fin de
fichier.

Ce module n'importe **jamais** `payfund_app.modules.wallet` : tout passe par le
`WalletServicePort` reçu en injection (Architecture §1).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from payfund_app.modules.fund.domain.entities import (
    CampaignStatus,
    InstallmentStatus,
    LoanStatus,
)
from payfund_app.modules.fund.domain.errors import (
    CampaignGoalAlreadyReached,
    CampaignNotActive,
    CampaignNotFound,
    CannotInvestInOwnCampaign,
    InstallmentAlreadyPaid,
    InvalidLoanTermsError,
    LoanAlreadyDisbursed,
    LoanNotDisbursed,
    LoanNotFound,
    NotCampaignOwner,
    RepaymentExceedsInstallment,
)
from payfund_app.modules.fund.domain.loan import (
    InvalidLoanTerms,
    LoanTerms,
    calculer_conditions,
    construire_echeancier,
)
from payfund_app.modules.fund.infra.models import (
    Campaign,
    Investment,
    Loan,
    RepaymentSchedule,
)
from payfund_app.modules.fund.infra.repositories import (
    CampaignRepository,
    InvestmentRepository,
    LoanRepository,
)
from payfund_app.shared_kernel.contracts.scoring_provider import ScoringPort
from payfund_app.shared_kernel.contracts.wallet_provider import WalletServicePort

# Type de transaction wallet portant un investissement. `wallet.transactions.type` accepte
# `fund_disbursement` et `fund_repayment` (§3.1) mais rien pour l'entrée d'un investisseur dans
# le pool — c'est le mouvement inverse d'un décaissement, on réutilise donc ce type avec un
# `origin_module = 'fund'` et une `reference` explicite.
INVESTMENT_TX_TYPE = "fund_disbursement"
ORIGIN_MODULE = "fund"


@dataclass
class InvestmentResult:
    investment: Investment
    wallet_transaction_id: uuid.UUID


class FundUseCases:
    def __init__(self, session: Session, wallet: WalletServicePort) -> None:
        self.session = session
        self.campaigns = CampaignRepository(session)
        self.investments = InvestmentRepository(session)
        self.wallet = wallet

    # --- Campagnes -----------------------------------------------------------

    def creer_campagne(
        self, *, owner_user_id: uuid.UUID, title: str, goal_amount: int, currency: str
    ) -> Campaign:
        """Crée une campagne en `draft` (Contrat API §2).

        Le compte technique du pool est ouvert dès maintenant, pas à l'activation : la campagne
        doit avoir une contrepartie ledger identifiée avant tout mouvement, et cela évite un
        second point de défaillance au moment de la validation back-office.
        """
        wallet_account_id = self.wallet.ouvrir_compte_technique(
            reference=f"fund:campaign:{title[:40]}"
        )
        return self.campaigns.create(
            owner_user_id=owner_user_id,
            title=title,
            goal_amount=goal_amount,
            currency=currency,
            wallet_account_id=wallet_account_id,
            status=str(CampaignStatus.DRAFT),
        )

    def lister_campagnes(
        self, *, status: str | None, page: int, page_size: int
    ) -> tuple[list[Campaign], int]:
        return self.campaigns.list_by_status(status, page, page_size)

    def detail_campagne(
        self, campaign_id: uuid.UUID
    ) -> tuple[Campaign, list[Investment]]:
        campaign = self.campaigns.get(campaign_id)
        if campaign is None:
            raise CampaignNotFound()
        return campaign, self.investments.latest_for_campaign(campaign_id)

    # --- Investissement ------------------------------------------------------

    def investir(
        self,
        *,
        campaign_id: uuid.UUID,
        investor_user_id: uuid.UUID,
        amount: int,
        pin: str,
        idempotency_key: str,
    ) -> InvestmentResult:
        """Débite l'investisseur et crédite le compte de la campagne, atomiquement.

        Contrat API §2 : « atomique avec la création de l'`investment` (les deux dans la même
        transaction DB) ». C'est le cas ici : le port est in-process et partage la session.
        """
        self.wallet.verifier_pin_utilisateur(investor_user_id, pin)
        campaign = self.campaigns.get_for_update(campaign_id)
        if campaign is None:
            raise CampaignNotFound()
        if campaign.status != str(CampaignStatus.ACTIVE):
            raise CampaignNotActive(details={"status": campaign.status})
        if campaign.owner_user_id == investor_user_id:
            raise CannotInvestInOwnCampaign()
        if campaign.raised_amount >= campaign.goal_amount:
            raise CampaignGoalAlreadyReached(
                details={
                    "goal_amount": int(campaign.goal_amount),
                    "raised_amount": int(campaign.raised_amount),
                }
            )

        investor_account_id = self.wallet.compte_de_utilisateur(investor_user_id)

        # `invest` déplace des fonds : le contrat §0 impose l'en-tête `Idempotency-Key` côté
        # appelant, on propage donc **sa** clé au lieu d'en dériver une. La clé dérivée du §4
        # ne concerne que les appels que `fund` initie de lui-même (ex. `DecaisserPret`).
        wallet_transaction_id = self.wallet.debiter(
            compte_id=investor_account_id,
            contrepartie_compte_id=campaign.wallet_account_id,
            montant=amount,
            reference=f"fund:investment:{campaign_id}",
            idempotency_key=idempotency_key,
            type_transaction=INVESTMENT_TX_TYPE,
            origin_module=ORIGIN_MODULE,
        )

        already = self.investments.by_wallet_transaction(wallet_transaction_id)
        if already is not None:
            # Le mouvement wallet a été rejoué : on renvoie l'investissement d'origine plutôt
            # que d'en créer un second sans contrepartie financière.
            return InvestmentResult(already, wallet_transaction_id)

        investment = self.investments.create(
            campaign_id=campaign_id,
            investor_user_id=investor_user_id,
            amount=amount,
            wallet_transaction_id=wallet_transaction_id,
        )
        campaign.raised_amount = campaign.raised_amount + Decimal(amount)
        self.session.flush()

        return InvestmentResult(investment, wallet_transaction_id)


class LoanUseCases:
    """Prêts DiddiFund — crowdlending : le pool d'une campagne finance le prêt de son porteur,
    et les remboursements retournent dans ce même pool."""

    def __init__(
        self, session: Session, wallet: WalletServicePort, scoring: ScoringPort
    ) -> None:
        self.session = session
        self.campaigns = CampaignRepository(session)
        self.loans = LoanRepository(session)
        self.wallet = wallet
        self.scoring = scoring

    # --- Simulation ----------------------------------------------------------

    def simuler(self, *, user_id: uuid.UUID, amount: int, duration_months: int) -> LoanTerms:
        """Calcul pur, ne crée rien (Contrat §2).

        Deux utilisateurs peuvent obtenir des simulations différentes pour le même montant, le
        taux venant du `ScoringPort`.
        """
        try:
            return calculer_conditions(
                principal=amount,
                duration_months=duration_months,
                taux=self.scoring.taux_pour(user_id),
            )
        except InvalidLoanTerms as exc:
            raise InvalidLoanTermsError(str(exc)) from exc

    # --- Demande -------------------------------------------------------------

    def demander(
        self,
        *,
        campaign_id: uuid.UUID,
        borrower_user_id: uuid.UUID,
        amount: int,
        duration_months: int,
    ) -> Loan:
        """Crée la demande en `pending`. Ne décaisse rien : le passage à `disbursed` relève de
        l'étape d'évaluation (Contrat §2, architecture §6)."""
        campaign = self.campaigns.get(campaign_id)
        if campaign is None:
            raise CampaignNotFound()
        if campaign.owner_user_id != borrower_user_id:
            raise NotCampaignOwner()

        terms = self.simuler(
            user_id=borrower_user_id, amount=amount, duration_months=duration_months
        )
        return self.loans.create(
            campaign_id=campaign_id,
            borrower_user_id=borrower_user_id,
            principal_amount=terms.principal,
            duration_months=terms.duration_months,
            interest_rate_applied=terms.interest_rate_applied,
            total_repayable=terms.total_repayable,
            currency=campaign.currency,
            diddi_score_at_grant=self.scoring.score_de(borrower_user_id),
        )

    # --- Décaissement --------------------------------------------------------

    def decaisser(self, loan_id: uuid.UUID, *, aujourd_hui: date | None = None) -> Loan:
        """Verse le capital au porteur, depuis le pool de sa campagne, et pose l'échéancier.

        Pas de route HTTP : comme le passage `draft → active` d'une campagne, le déclencheur de
        `pending → disbursed` relève du back-office et sort du contrat public (§3).
        """
        loan = self.loans.get_for_update(loan_id)
        if loan is None:
            raise LoanNotFound()
        if loan.status != str(LoanStatus.PENDING):
            raise LoanAlreadyDisbursed(details={"status": loan.status})

        campaign = self.campaigns.get(loan.campaign_id)
        if campaign is None:
            raise CampaignNotFound()

        compte_emprunteur = self.wallet.compte_de_utilisateur(loan.borrower_user_id)

        # Clé stable dérivée du prêt (Architecture §4) : un retry après crash ne décaisse pas
        # deux fois le même prêt.
        wallet_transaction_id = self.wallet.crediter(
            compte_id=compte_emprunteur,
            contrepartie_compte_id=campaign.wallet_account_id,
            montant=int(loan.principal_amount),
            reference=f"fund:loan:disbursement:{loan.id}",
            idempotency_key=f"fund:loan:disbursement:{loan.id}",
            type_transaction="fund_disbursement",
            origin_module=ORIGIN_MODULE,
        )

        depart = aujourd_hui or date.today()
        terms = LoanTerms(
            principal=int(loan.principal_amount),
            duration_months=loan.duration_months,
            interest_rate_applied=loan.interest_rate_applied,
            total_repayable=int(loan.total_repayable),
            monthly_installment=int(
                Decimal(loan.total_repayable) / Decimal(loan.duration_months)
            ),
        )
        self.loans.ajouter_echeances(loan, construire_echeancier(terms, depart=depart))

        loan.disbursed_at = datetime.now(timezone.utc)
        loan.wallet_transaction_id = wallet_transaction_id
        self.loans.changer_statut(
            loan,
            str(LoanStatus.DISBURSED),
            {"wallet_transaction_id": str(wallet_transaction_id)},
        )
        self.session.flush()
        return loan

    # --- Lecture -------------------------------------------------------------

    def detail(self, loan_id: uuid.UUID, *, user_id: uuid.UUID) -> tuple[Loan, RepaymentSchedule | None]:
        loan = self.loans.get(loan_id)
        if loan is None or loan.borrower_user_id != user_id:
            raise LoanNotFound()
        prochaine = next(
            (
                e
                for e in self.loans.echeancier(loan_id)
                if e.status != str(InstallmentStatus.PAID)
            ),
            None,
        )
        return loan, prochaine

    def echeancier(self, loan_id: uuid.UUID, *, user_id: uuid.UUID) -> list[RepaymentSchedule]:
        loan = self.loans.get(loan_id)
        if loan is None or loan.borrower_user_id != user_id:
            raise LoanNotFound()
        return self.loans.echeancier(loan_id)

    # --- Remboursement -------------------------------------------------------

    def rembourser(
        self,
        *,
        loan_id: uuid.UUID,
        borrower_user_id: uuid.UUID,
        amount: int,
        pin: str,
        idempotency_key: str,
    ) -> tuple[Loan, RepaymentSchedule]:
        """Règle tout ou partie de la prochaine échéance, et recrédite le pool de la campagne.

        Le remboursement s'impute sur la **première échéance non soldée**. Un montant supérieur à
        ce qu'il reste dû sur celle-ci est refusé : ni le contrat ni l'architecture ne décrivent
        le report d'un excédent sur l'échéance suivante, ni le remboursement anticipé.
        """
        self.wallet.verifier_pin_utilisateur(borrower_user_id, pin)
        loan = self.loans.get_for_update(loan_id)
        if loan is None or loan.borrower_user_id != borrower_user_id:
            raise LoanNotFound()
        if loan.status in (str(LoanStatus.PENDING),):
            raise LoanNotDisbursed(details={"status": loan.status})

        echeance = self.loans.prochaine_echeance(loan_id)
        if echeance is None:
            raise InstallmentAlreadyPaid()

        reste_du = int(echeance.amount_due) - int(echeance.amount_paid)
        if amount > reste_du:
            raise RepaymentExceedsInstallment(
                details={"installment_no": echeance.installment_no, "remaining": reste_du}
            )

        campaign = self.campaigns.get(loan.campaign_id)
        if campaign is None:
            raise CampaignNotFound()

        compte_emprunteur = self.wallet.compte_de_utilisateur(borrower_user_id)
        wallet_transaction_id = self.wallet.debiter(
            compte_id=compte_emprunteur,
            contrepartie_compte_id=campaign.wallet_account_id,
            montant=amount,
            reference=f"fund:loan:repayment:{loan.id}",
            idempotency_key=idempotency_key,
            type_transaction="fund_repayment",
            origin_module=ORIGIN_MODULE,
        )
        loan.wallet_transaction_id = wallet_transaction_id

        echeance.amount_paid = echeance.amount_paid + Decimal(amount)
        if int(echeance.amount_paid) >= int(echeance.amount_due):
            echeance.status = str(InstallmentStatus.PAID)
            echeance.paid_at = datetime.now(timezone.utc)

        if loan.status == str(LoanStatus.DISBURSED):
            self.loans.changer_statut(loan, str(LoanStatus.REPAYING))

        self.session.flush()
        if self.loans.prochaine_echeance(loan_id) is None:
            self.loans.changer_statut(loan, str(LoanStatus.CLOSED))

        self.session.flush()
        return loan, echeance

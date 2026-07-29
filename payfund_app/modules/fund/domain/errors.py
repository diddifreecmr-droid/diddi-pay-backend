"""Erreurs métier de `fund`, avec les codes exacts du contrat API §2."""

from payfund_app.core.errors import AppError


class CampaignNotFound(AppError):
    status_code = 404
    code = "CAMPAIGN_NOT_FOUND"
    message = "Campagne introuvable."


class CampaignNotActive(AppError):
    status_code = 409
    code = "CAMPAIGN_NOT_ACTIVE"
    message = "Cette campagne n'accepte pas d'investissement."


class CampaignGoalAlreadyReached(AppError):
    status_code = 409
    code = "CAMPAIGN_GOAL_ALREADY_REACHED"
    message = "L'objectif de la campagne est déjà atteint."


class CannotInvestInOwnCampaign(AppError):
    status_code = 422
    code = "CANNOT_INVEST_IN_OWN_CAMPAIGN"
    message = "Impossible d'investir dans sa propre campagne."


class LoanNotFound(AppError):
    status_code = 404
    code = "LOAN_NOT_FOUND"
    message = "Prêt introuvable."


class InvalidLoanTermsError(AppError):
    status_code = 422
    code = "INVALID_LOAN_TERMS"
    message = "Conditions de prêt invalides."


class NotCampaignOwner(AppError):
    """En crowdlending, l'emprunteur est le porteur de la campagne qu'il a financée."""

    status_code = 403
    code = "NOT_CAMPAIGN_OWNER"
    message = "Seul le porteur de la campagne peut emprunter sur son pool."


class InstallmentAlreadyPaid(AppError):
    status_code = 409
    code = "INSTALLMENT_ALREADY_PAID"
    message = "Toutes les échéances de ce prêt sont déjà réglées."


class RepaymentExceedsInstallment(AppError):
    status_code = 422
    code = "REPAYMENT_EXCEEDS_INSTALLMENT"
    message = "Le montant dépasse ce qui reste dû sur la prochaine échéance."


class LoanNotDisbursed(AppError):
    status_code = 409
    code = "LOAN_NOT_DISBURSED"
    message = "Ce prêt n'a pas encore été décaissé."


class LoanAlreadyDisbursed(AppError):
    status_code = 409
    code = "LOAN_ALREADY_DISBURSED"
    message = "Ce prêt a déjà été décaissé."

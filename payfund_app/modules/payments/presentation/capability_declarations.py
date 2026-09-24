from typing import Literal

from pydantic import BaseModel, Field


class WalletCapabilityDeclaration(BaseModel):
    service: Literal["diddipay"] = "diddipay"
    capability_type: Literal["wallet"] = "wallet"
    owner: Literal["diddipay"] = "diddipay"
    projection_target: Literal["diddifreeid/pro"] = "diddifreeid/pro"
    projection_authorizes_financial_operations: Literal[False] = False
    financial_operations_allowed: list[str] = Field(default_factory=list)
    operational_statuses: list[str] = Field(
        default_factory=lambda: ["available", "limited", "unavailable", "stale"],
    )
    access_statuses: list[str] = Field(
        default_factory=lambda: ["enabled", "disabled", "suspended", "revoked"],
    )
    forbidden_by_projection: list[str] = Field(
        default_factory=lambda: [
            "balance_read",
            "payment_authorization",
            "transfer",
            "withdrawal",
            "pin_validation",
            "risk_decision",
            "limit_decision",
        ],
    )
    notes: str = (
        "DiddiFreeID exposes only the coarse wallet capability projection. "
        "DiddiPay remains the source of truth for balances, PIN, risk, limits, "
        "transfers, withdrawals, payouts and payment authorization."
    )


def wallet_capability_declarations() -> list[WalletCapabilityDeclaration]:
    return [WalletCapabilityDeclaration()]

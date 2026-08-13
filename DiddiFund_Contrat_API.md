# DiddiFund - Contrat API

**Service:** crowdfunding and loan module for DiddiFree  
**Base URL:** `/payfund/v1`  
**Format:** JSON only  
**Auth:** DiddiFreeID JWT verified locally through JWKS

## 1. Scope

DiddiFund owns:
- campaigns
- investments
- loans
- repayment schedules
- repayments
- fund-specific business rules

DiddiFreeID only authenticates the user.
Fund-specific roles stay inside DiddiFund.

## 2. Public Routes

### `POST /fund/campaigns`

Creates a campaign in `draft`.

### `GET /fund/campaigns`

Lists campaigns with optional filtering by status.

### `GET /fund/campaigns/{campaign_id}`

Returns campaign detail and recent investments.

### `POST /fund/campaigns/{campaign_id}/invest`

Invests in an active campaign.

Behavior:
- checks campaign is active
- checks the caller is not the campaign owner
- debits the investor wallet through DiddiPay
- credits the campaign pool wallet
- creates the investment record atomically

### `POST /fund/loans/simulate`

Returns simulated loan terms.

### `POST /fund/loans`

Creates a loan request for a campaign owner.

### `GET /fund/loans/{loan_id}`

Returns loan detail.

### `GET /fund/loans/{loan_id}/schedule`

Returns repayment schedule.

### `POST /fund/loans/{loan_id}/repay`

Applies a repayment to the next open installment.

## 3. Loan Rules

- Simulation is read-only.
- Loan creation does not disburse funds.
- Disbursement is a back-office action, not a public route.
- Repayment applies to the next unpaid installment.
- Overpayment of the current installment is rejected.
- Loan state is owned by DiddiFund.

## 4. Wallet Integration

DiddiFund calls DiddiPay through the wallet service contract.

Money movement remains centralized in DiddiPay:
- debit investor
- credit campaign pool
- debit borrower for repayment
- credit campaign pool on repayment
- disburse loan from pool to borrower when the back-office action runs

## 5. Identity and Roles

Use DiddiFreeID for:
- authentication
- JWT verification
- global platform status

Do not store module roles in DiddiFreeID.

Examples of module-owned roles:
- campaign owner
- investor
- borrower

## 6. Currency

First supported currency: `XOF`.

## 7. Errors

Errors follow the shared envelope:

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human readable message",
    "details": null
  }
}
```

## 8. Notes

- DiddiFund does not import wallet internals directly.
- DiddiPay remains the wallet system of record.
- DiddiFreeID remains the central identity provider only.

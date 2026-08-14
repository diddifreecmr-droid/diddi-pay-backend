# DiddiPay - Contrat API

**Service:** wallet core for DiddiFree  
**Base URL:** `/payfund/v1`  
**Format:** JSON only  
**Auth:** DiddiFreeID JWT verified locally through JWKS

The generated OpenAPI document at `/payfund/v1/openapi.json` is the executable HTTP contract: every
route, request body, header and JSON success response must be represented there. This document adds
business semantics and lifecycle rules that OpenAPI cannot express by itself.

## 1. Scope

DiddiPay owns wallet state and money movement.

It manages:
- wallet balances
- double-entry ledger
- deposits
- withdrawals
- peer-to-peer transfers
- merchant payments
- QR generation and verification
- transaction history
- provider callbacks and reconciliation

DiddiPay does not own global identity. DiddiFreeID provides authentication only.
Module-specific roles and profiles stay inside the owning module.

### Wallet provisioning

The wallet account is provisioned automatically on first access to wallet state.
In practice, `GET /wallet/balance` is the normal consumer entry point and will
return the wallet balance once the account exists.

If an event-driven provisioning step was missed, operators may repair the
state with an internal backfill route:

- `POST /wallet/ops/backfill`

The current implementation also self-heals on first authenticated access inside the wallet use case
layer: if the provisioning event was missed, the first balance read creates the wallet before
returning the result. The ops backfill route remains the supported repair path for support teams.

This is an ops-only escape hatch and not a client-facing creation endpoint.

The wallet owner is always DiddiPay. DiddiFreeID only provides identity and
global platform status; module-specific roles remain inside the owning module.

### Ops and reconciliation

DiddiPay exposes internal ops views for support and recovery:

- `GET /wallet/ops/provisioning/{user_id}`
- `POST /wallet/ops/backfill`
- `POST /wallet/ops/paystack/reconcile/{transaction_id}`
- `GET /wallet/ops/paystack/pending`
- `GET /wallet/ops/paystack/summary`
- `GET /wallet/ops/paystack/{transaction_id}`
- `GET /wallet/ops/paystack/{transaction_id}/reconciliations`
- `GET /wallet/ops/outbox`
- `POST /wallet/ops/outbox/relay`

These routes are for internal operations only.

### KYC hooks

DiddiPay can link wallet users to external document references for KYC.
The wallet service stores document metadata, not the file blob itself.
The intended integration target is DiddiFiles or any equivalent file service.
The wallet stores only a file reference and a small amount of metadata.

## 2. Money Model

- All amounts are integers in minor units.
- First currency supported: `XOF`
- A transaction is mono-currency.
- Ledger entries are immutable.
- Idempotency is mandatory on all routes that move money.

## 3. Provider Model

DiddiPay is provider-agnostic.

Providers are modular adapters in infrastructure, for example:
- `paystack`
- `orange_money`
- `wave`
- `mtn_momo`

External providers are not the source of truth.
The source of truth is the DiddiPay transaction state.

### Transaction states

- `pending`
- `completed`
- `failed`
- `reversed`

Provider webhooks update DiddiPay state.

## 4. Public Routes

### `GET /wallet/balance`

Returns the authenticated user wallet balance.

### `POST /wallet/deposit`

Initiates a wallet deposit.

Behavior:
- creates a `pending` transaction
- calls the configured provider adapter
- returns `202`
- final completion happens asynchronously via webhook or reconciliation

### `POST /wallet/withdraw`

Initiates a wallet withdrawal.

Behavior:
- reserves funds immediately
- creates a `pending` transaction
- provider confirmation later closes or reverses the transaction

### `POST /wallet/transfer`

Internal wallet transfer between two users.

Behavior:
- debit sender
- credit recipient
- atomically writes balanced ledger entries

### `POST /wallet/pay/merchant`

Merchant payment from a consumer wallet to a merchant wallet.

Behavior:
- debit consumer wallet
- credit merchant wallet
- transaction can be initiated by a module on behalf of the user
- the module supplies business context, but DiddiPay owns the ledger

### `POST /wallet/qr/generate`

Generates a signed QR payload for merchant payment.

### `POST /wallet/qr/verify`

Verifies and decodes a QR payload.

### `GET /wallet/transactions`

Lists transactions with filters and pagination. Every item includes `direction`, whose values are
`credit`, `debit`, or `null` while a pending provider operation has not produced a ledger entry.

### `GET /wallet/transactions/{transaction_id}`

Returns transaction detail, including direction from the caller point of view.

### `POST /wallet/pin/set`

Defines or changes the transaction PIN after OTP verification.

Success response:

```json
{
  "status": "ok",
  "account_id": "uuid",
  "recovery_codes": ["one-time-code-1", "one-time-code-2"]
}
```

Recovery codes are returned only when issued and must be shown once to the user.

### `POST /wallet/pin/change`

Changes an existing PIN after the current PIN is verified.

Success response: `{ "status": "ok", "account_id": "uuid" }`.

### `POST /wallet/pin/reset`

Resets the PIN using a recovery code issued at PIN creation or admin recovery.

Success response: `{ "status": "ok", "account_id": "uuid" }`.

### `POST /wallet/transfer/step-up/request`

Creates a short-lived OTP challenge for sensitive transfers.

The frontend must not embed the risk threshold. It attempts the transfer and reacts to
`STEP_UP_OTP_REQUIRED`; the threshold is a server-side policy configured per environment.

### `POST /wallet/ops/pin/reset`

Admin-only escape hatch to reset a wallet PIN with audit logging.

## 5. Deposit Lifecycle

1. Client requests deposit
2. DiddiPay creates a `pending` transaction
3. DiddiPay asks the provider to initiate the payment
4. Provider webhook arrives later
5. DiddiPay verifies the webhook signature
6. DiddiPay marks the transaction `completed` or `failed`
7. If completed, DiddiPay posts the ledger entries and credits the wallet

## 6. Withdrawal Lifecycle

1. Client requests withdrawal
2. DiddiPay immediately reserves funds
3. DiddiPay creates a `pending` transaction
4. Provider webhook arrives later
5. DiddiPay verifies the webhook signature
6. DiddiPay marks the transaction `completed`
7. If provider fails, DiddiPay marks it `reversed`

## 7. Merchant Payment Lifecycle

Merchant payment is a wallet-to-wallet movement.

The module that initiated the action supplies context such as:
- service name
- order reference
- ride reference
- invoice reference

DiddiPay still owns:
- authorization
- idempotency
- transaction state
- ledger entries

The module that initiates the payment may be DiddiGo, DiddiShop or any future
consumer, but it does not own the balance. It only contributes business context
such as an order id, ride id or invoice id.

## 8. CORS

Allowed origins must include:
- all `localhost` ports
- `*.diddifree.com`
- `*.vercel.com`

Explicit CORS origins can also be configured through `CORS_ORIGINS`.
When explicit origins are provided, the backend keeps them exact and still
supports the wildcard development regex for localhost and the main DiddiFree
domains.

## 9. Errors

Errors follow the standard envelope:

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human readable message",
    "details": null
  }
}
```

## 10. Notes

- DiddiPay remains the wallet system of record.
- Providers are interchangeable adapters.
- DiddiFreeID is only the central identity provider.
- Module-specific roles are not stored in DiddiFreeID.
- DiddiPay is a wallet, not a payment orchestrator: external rails feed or cash out the wallet.

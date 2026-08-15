# ADR 0001 - DiddiPay as payment orchestrator

- Status: accepted
- Date: 2026-08-15
- Decision owners: DiddiFree platform team

## Context

The first DiddiPay MVP was implemented as a user wallet. The product specification has a broader
target: DiddiPay is the common settlement engine used by DiddiGo, DiddiFund, and future modules.
During the aggregation phase, an external processor moves the money. Paystack is the first
processor. Direct Orange Money, Wave, MTN MoMo, or Moov integrations may replace it later without
changing the API used by business modules.

The existing wallet remains useful as a possible future payment method after the required
regulatory and operational capabilities exist. It must not remain the identity of DiddiPay or be a
mandatory prerequisite for paying a DiddiGo ride.

## Decision

DiddiPay owns the payment lifecycle and exposes a provider-neutral Payment API.

- DiddiGo owns rides and calculates the amount to collect.
- DiddiFund owns investments and loans.
- DiddiFreeID owns global identity and authentication.
- DiddiPay owns payment intents, attempts, normalized statuses, idempotency, provider callbacks,
  reconciliation, refunds, and payment notifications.
- Paystack processes external payments in the first production phase.
- A processor is not a payment channel. `paystack` is a processor, `mobile_money` is a channel,
  and `orange`, `wave`, or `mtn` are networks.
- A future DiddiWallet is another payment method behind DiddiPay. It is not required to create or
  settle a PaymentIntent during the aggregation phase.

The public identity of a payment is always the DiddiPay `payment_intent_id`. Provider references
are operational details and are not business identifiers for consuming modules.

## Target lifecycle

1. A trusted module creates a PaymentIntent with an idempotency key and its business reference.
2. DiddiPay persists the intent before contacting a processor.
3. The processor router selects an adapter compatible with currency, direction, and channel.
4. Each processor call creates a separate PaymentAttempt.
5. DiddiPay returns a provider-neutral `next_action` to the caller.
6. Provider webhooks enter a durable inbox and are processed idempotently.
7. DiddiPay verifies the amount, currency, reference, and final provider status.
8. The intent transition and its outgoing event are committed in the same database transaction.
9. A durable delivery worker notifies the owning module until it acknowledges the event.
10. Reconciliation verifies attempts that remain non-final after the expected delay.

## Status model

PaymentIntent statuses are:

- `requires_action`: the payer must complete a redirect, prompt, or instruction;
- `processing`: the processor may already have accepted the payment;
- `succeeded`: exactly one attempt completed for the expected amount and currency;
- `failed`: a definitive failure was confirmed;
- `cancelled`: the intent was cancelled before any successful attempt;
- `partially_refunded`: only part of the captured amount was refunded;
- `refunded`: the captured amount was fully refunded.

PaymentAttempt statuses are:

- `pending`, `requires_action`, `processing`, `succeeded`, `failed`, `cancelled`, or `unknown`.

`unknown` and `processing` are deliberately different from `failed`. A timeout cannot trigger an
automatic failover because the payer may already have been charged.

## Financial invariants

- Amounts are positive integer minor units. The first supported currency is XOF.
- An idempotency key is scoped to the authenticated module and stores a request fingerprint.
- Reusing a key with a different request is an idempotency conflict.
- At most one PaymentAttempt can succeed for a PaymentIntent.
- A provider reference is unique within its processor.
- Duplicate or reordered webhooks cannot create a second success or a second ledger posting.
- No provider timeout is treated as a definitive failure without verification.
- Provider payloads and secrets are filtered before persistence and logging.
- Ledger entries are immutable. Corrections and refunds create compensating entries.
- DiddiPay's database is the source of truth. Webhooks and outgoing notifications are delivery
  mechanisms, not alternate sources of payment state.

## Clean Architecture boundary

The payment module uses four layers:

```text
payments/
  domain/          entities, value objects, state transitions
  application/     use cases and ports
  infra/           PostgreSQL repositories and processor adapters
  presentation/    FastAPI schemas, dependencies, and routers
```

Domain and application code cannot import FastAPI, SQLAlchemy, httpx, or Paystack-specific types.
Provider adapters normalize their responses into application DTOs such as `ProviderAction`,
`ProviderResult`, and `ProviderEvent`.

## Compatibility and migration

The migration is additive:

- existing wallet tables and routes are not deleted during the pivot;
- new PaymentIntent routes are introduced beside wallet routes;
- wallet routes are marked legacy only after the new end-to-end flow passes in Docker;
- old wallet records are retained for audit;
- new external collections do not provision a user wallet;
- DiddiFund investment collections move to PaymentIntent first;
- loan payouts remain disabled until a real payout processor and operational approval exist.

## Reliable module notifications

Redis Pub/Sub is not sufficient for payment completion. The MVP uses a transactional outbox and
durable, signed HTTP deliveries to registered module endpoints. Delivery is at-least-once, so every
event has a stable event id and consumers must deduplicate it. Failed deliveries are retried with a
bounded backoff and remain visible to operations for replay.

Redis Streams can later replace or supplement the delivery transport without changing the payment
domain or public API.

## Consequences

Positive consequences:

- business modules do not change when Paystack is replaced;
- provider outages and ambiguous timeouts are represented safely;
- each payment is auditable across intent, attempts, provider events, ledger, and notifications;
- the existing webhook, reconciliation, outbox, and ledger work can be reused.

Costs and constraints:

- the wallet and payment models coexist temporarily;
- provider adapters require contract tests and explicit capability declarations;
- DiddiGo and DiddiFund must treat callbacks as at-least-once and query DiddiPay when uncertain;
- a live Paystack sandbox validation still requires real sandbox credentials and a reachable webhook.

## MVP boundary

The pivoted MVP includes external collection, module merchant payment, full refunds, durable
callbacks, reconciliation, settlement accounting, operations views, and XOF through Paystack.

Direct PSP adapters, smart cost routing, payouts, escrow, multi-currency conversion, and a
proprietary wallet remain outside this MVP.

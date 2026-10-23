# DiddiPay Support contract v1

## Read-only payment context

`GET /payfund/v1/internal/support/payment/{payment_intent_id}/context`

The caller must use a DiddiFreeID service token with audience `diddipay`, an
allowlisted `X-Client-ID`, and scope `diddipay:support-context:read`.

The projection intentionally excludes payer/payee IDs, phone numbers, provider
references, provider payloads, callback URLs, metadata, credentials, and ledger
details. DiddiPay remains authoritative for payment and refund state; DiddiSupport
remains authoritative for cases, actions, operators, and timelines.

The endpoint returns the common error envelope. Unknown payments return
`PAYMENT_CONTEXT_NOT_FOUND`.

## Delegated refund request

`POST /payfund/v1/internal/support/refund-requests`

This route requires `diddipay:support-actions:refund-request`, an allowlisted
Support service client, and `X-Support-Operator-ID`, `X-Support-Delegation-ID`,
`X-Support-Command-ID`, and `Idempotency-Key` headers. DiddiPay reuses its
provider-neutral refund rules and records the case, operator, delegation,
reason, fingerprint, and result in an immutable payment-side audit record.

Refund results are persisted to the payment outbox as
`diddipay.refund.succeeded.v1`, `diddipay.refund.failed.v1`, or
`diddipay.refund.unknown.v1`. The action can be polled with
`GET /payfund/v1/internal/support/refund-requests/{action_id}`.

Configure DiddiPay's `PAYMENT_CALLBACK_TARGETS` for the Support client. The
callback receives the signed standard event envelope at the Support event
consumer endpoint. DiddiSupport must deduplicate by `id` and return a 2xx only
after durably recording the result; DiddiPay retries failures and eventually
dead-letters them for operational retry.

For Redis Stream deployments, set `PAYMENT_EVENT_TRANSPORT=redis_stream`.
DiddiPay publishes the same envelope to `PAYMENT_REDIS_STREAM`; consumers must
acknowledge only after durable processing and deduplicate by event ID.

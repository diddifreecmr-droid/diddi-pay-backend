# DiddiFree Internal Integration Guide v1.1

## Purpose

This guide explains how each DiddiFree service should expose data and actions to
the internal tools:

- DiddiFree Backoffice: operational administration and audited actions.
- DiddiFree Pilotage: KPI, reporting, alerts and management visibility.

The same service may support both contracts, but they are different surfaces.
Backoffice acts on operational data. Pilotage reads summaries and freshness.

## Core Rules

1. Each service remains the owner of its data and business rules.
2. Internal tools must not read another service database directly.
3. Browsers must not receive service credentials.
4. Every sensitive action must be permission checked, reasoned and audited.
5. Pilotage summaries must never return personal data or raw full-table dumps.
6. A source failure must not become a zero KPI. Return or expose a degraded
   freshness state.

## Authentication

New internal integrations must use DiddiFreeID service-to-service tokens.
Static service tokens or service keys are allowed only for existing integrations
during migration or for documented emergency fallback.

Use this policy:

```text
Default: DiddiFreeID S2S token
Fallback: static secret only during transition or emergency
Never: browser sends service credentials
```

### Credential Types

Use the following names consistently:

| Credential | Used by | Purpose | Browser allowed? |
| --- | --- | --- | --- |
| Human operator JWT | Browser to Backoffice or Pilotage | Proves who the person is | Yes |
| S2S access token | Backoffice, Pilotage or worker to a service | Proves which internal service is calling and with which scope | No |
| Client secret | Server to DiddiFreeID token endpoint | Allows a trusted internal service to obtain an S2S token | No |
| Static service key/token | Legacy server-to-server fallback | Temporary compatibility only | No |

### Human Requests

Human users authenticate to Backoffice or Pilotage with their normal identity
token.

```http
Authorization: Bearer <human DiddiFreeID JWT>
```

This token proves the operator identity. It must not be forwarded blindly as a
service credential to another module unless that module explicitly accepts
human delegated access for the requested route.

### Service Requests

Backoffice, Pilotage and workers call modules with short-lived S2S access
tokens.

Expected pattern:

```http
Authorization: Bearer <service-token>
X-Request-ID: <correlation-id>
```

The receiving service should verify:

- issuer;
- signature and expiry;
- audience for the owning module;
- `token_type = service`;
- expected client identity;
- required scope.

Example:

```text
Backoffice -> DiddiFood
aud = diddifood
scope = food:restaurants:write
client_id = backoffice-staging
```

```text
Pilotage -> DiddiGo
aud = diddigo
scope = diddigo:ride-summary:read
client_id = pilotage-staging
```

Scope names should use this pattern:

```text
<module>:<resource>:<action>
```

Examples:

- `diddigo:ride-summary:read`
- `diddifood:restaurants:write`
- `diddisend:couriers:update`
- `diddipay:payment-intents:refund`

Token lifetime, caching and signing-key rotation belong to the DiddiFreeID
service-token contract. As a baseline, callers should cache S2S tokens only
until shortly before expiry, and receiving services should verify JWT signing
keys through the environment JWKS.

### Operator Context For Backoffice Actions

Human operator identity should come from the Backoffice BFF, not from arbitrary
browser-provided fields. When an upstream service needs the operator, the BFF
must inject it server-side.

Recommended headers for audited Backoffice actions:

```http
X-Backoffice-Actor: <operator_user_id>
X-Backoffice-Command-Id: <command_id>
Idempotency-Key: <idempotency_key>
```

The browser does not choose these trust headers. The Backoffice server builds
them after authentication, permission checks and command validation.

The human reason belongs in the JSON body, not in a header. This avoids header
encoding problems with accents and long notes.

A service must trust `X-Backoffice-Actor` only when the S2S token identifies an
approved Backoffice client for the requested route and scope. A valid S2S token
from another internal service is not enough to claim an operator identity.

### Static Secret Migration Rule

Static service tokens, API keys and `X-Service-Key` style trust are legacy
compatibility mechanisms. If a service still requires one, document:

- which route uses it;
- where the secret is stored;
- who can rotate it;
- the planned replacement S2S scope;
- the date or release where static trust should be removed.

New Pilotage and Backoffice contracts should not be designed around static
service keys.

## Backoffice Contract

Backoffice routes are for live operations: KYC, KYV, payouts, users, roles,
partners, vehicles, refunds, moderation, health and audited commands.

The official service contract is the owning module's admin API. Backoffice calls
documented module-owned routes such as `/v1/admin/couriers/{courier_id}` or
`/v1/drivers/{driver_id}/kyc/approve` through a Backoffice adapter and command
manifest.

Backoffice must not create parallel admin route conventions for a module unless
the owning module explicitly authorizes that contract. If a module chooses to
expose `/internal/backoffice/...` routes, those routes are still owned by the
module and must enforce the same S2S, reason, audit and business-rule checks.

### Required Service Surfaces

A service that plugs into Backoffice should define:

- health route;
- list routes for operational queues;
- detail routes for sensitive records;
- action routes for mutations;
- audit/event routes where applicable;
- required permissions and scopes for each operation.

### Module-Owned Route Style

```http
GET   /health
GET   /v1/admin/{resource}
GET   /v1/admin/{resource}/{id}
GET   /v1/admin/{resource}/{id}/history
POST  /v1/admin/{resource}/{id}/{action}
PATCH /v1/admin/{resource}/{id}
```

The exact paths follow the owning service conventions. The contract must be
documented, stable and described in the Backoffice command manifest.

### Collection Response

```json
{
  "items": [],
  "page": 1,
  "page_size": 20,
  "total": 0
}
```

Pagination must be bounded. The current Backoffice caps most page sizes at 100.

### Action Request

```json
{
  "contract_version": "backoffice.v1",
  "target": {
    "type": "courier",
    "id": "courier_123"
  },
  "reason": "Verification documents expired",
  "idempotency_key": "cmd_20260923_001",
  "payload": {
    "status": "suspended"
  }
}
```

`reason` and `idempotency_key` are canonical in the JSON body. If an
`Idempotency-Key` header is also present, it must match the body value. A
mismatch must be rejected with `400`.

### Action Response

```json
{
  "contract_version": "backoffice.v1",
  "status": "completed",
  "command_id": "cmd_123",
  "backoffice_audit_id": "audit_backoffice_456",
  "service_audit_id": "audit_service_789"
}
```

Allowed statuses:

- `queued`
- `processing`
- `completed`
- `failed`
- `cancelled`
- `requires_approval`

For `queued`, `processing` or `requires_approval`, the response must include a
`command_id`. The owning module must document how Backoffice learns the final
outcome.

If the module owns the async execution, it should expose a module-owned status
route, for example:

```http
GET /v1/admin/commands/{command_id}
```

If Backoffice owns the command queue, Backoffice may track status locally.
Callbacks can be added later when explicitly authorized by the owning module.

When a command requires approval, Backoffice owns the approval workflow and
records the approval audit event. The owning service executes only after it
receives a valid command request from Backoffice or after its own stricter
approval rule is also satisfied.

Backoffice always writes a local audit event for the operator action. The owning
service should also write its own service audit event when it changes service
state. Responses may include both audit IDs.

### Standard Error Response

Services should use this error shape for Backoffice and Pilotage routes:

```json
{
  "error": {
    "code": "permission_denied",
    "message": "Required scope is missing",
    "details": {
      "required_scope": "diddisend:couriers:update"
    },
    "request_id": "req_123"
  }
}
```

`message` must be safe for internal UI display. Do not expose secrets, raw
provider payloads or stack traces.

### Backoffice Command Manifest

Each command should be describable with:

```json
{
  "module": "diddisend",
  "name": "update_courier_status",
  "permission": "update",
  "description": "Change a courier verification status",
  "method": "PATCH",
  "path": "/v1/admin/couriers/{courier_id}",
  "reason_field": "reason",
  "requires_reason": true,
  "requires_idempotency": true,
  "execution_mode": "interactive",
  "input_fields": [
    {
      "name": "status",
      "label": "Courier status",
      "type": "string",
      "required": true,
      "options": ["pending_verification", "active", "suspended", "rejected"]
    }
  ]
}
```

`path` is the module-owned service route that the Backoffice adapter will call
after validating the command contract. It must not be invented by Backoffice
without the owning module's agreement.

Backoffice path parameters must be controlled identifiers. Do not accept raw
path fragments from an operator.

## Pilotage Contract

Pilotage routes are for management visibility: KPIs, finance, treasury, module
performance, alerts, reports and decision support.

Pilotage should receive summaries, not raw records.

### Recommended Route Style

```http
GET /internal/pilotage/daily-summary?date=2026-09-23
GET /internal/pilotage/finance-summary?date=2026-09-23
GET /internal/pilotage/health-summary
```

The first V1 summary should usually be daily, using the business timezone
agreed with the consuming team.

### Summary Response

```json
{
  "contract_version": "pilotage.v1",
  "module": "diddigo",
  "date": "2026-09-23",
  "timezone": "Africa/Abidjan",
  "is_final": false,
  "metrics": [
    {
      "name": "rides_requested",
      "value": 1000,
      "unit": "count"
    },
    {
      "name": "rides_completed",
      "value": 720,
      "unit": "count"
    },
    {
      "name": "completed_fare_total_xof",
      "value": 4850000,
      "unit": "XOF"
    }
  ],
  "calculated_at": "2026-09-23T14:32:10Z",
  "sources": [
    {
      "module": "diddigo",
      "record_type": "ride-summary"
    }
  ],
  "deep_links": [
    {
      "label": "Open rides in Backoffice",
      "href": "/backoffice/#diddigo-rides"
    }
  ]
}
```

### Metric Rules

Every metric must have:

- stable `name`;
- human `label` in documentation;
- unit, such as `count`, `XOF`, `percent`, `seconds`;
- precise definition;
- period boundaries;
- timezone;
- calculation timestamp.

Example definitions:

| Metric | Definition |
| --- | --- |
| `rides_requested` | Count of rides whose `requested_at` falls inside the requested day. |
| `rides_completed` | Count of rides whose `completed_at` falls inside the requested day and whose status is `completed`. |
| `completed_fare_total_xof` | Sum of final fare for the same rides counted by `rides_completed`, in XOF. |

The example ride metrics are not a completion-rate denominator pair. A ride
requested at 23:50 and completed at 00:10 is requested on one day and completed
on the next. If a team needs a true same-cohort completion rate, the service
must expose a separate metric based on rides requested during the same period.

Finance metrics must define whether amounts are before or after promotions,
refunds, taxes, commissions and provider fees. `final fare` is not precise
enough for finance reporting without that definition.

For the current day, services should return `is_final: false`. Historical
summaries may also change if late corrections are applied; services must
document whether past days are recalculated.

## Freshness And Failure

Pilotage collectors may call services periodically. Services should return
`calculated_at`. Pilotage should add or preserve freshness metadata:

```json
{
  "status": "fresh",
  "synchronized_at": "2026-09-23T14:32:12Z",
  "source_updated_at": "2026-09-23T14:32:10Z",
  "last_success_at": "2026-09-23T14:32:12Z"
}
```

If a source is unavailable, Pilotage should show stale or unavailable state
instead of replacing the previous value with zero.

Freshness statuses:

| Status | Meaning |
| --- | --- |
| `fresh` | Latest collection succeeded and is within the agreed freshness window. |
| `stale` | Last successful value exists, but it is older than the agreed freshness window. |
| `unavailable` | No successful value is available or the source cannot currently be reached. |

Each source contract must define its freshness window. For first V1 Pilotage
summaries, use this default unless a module states otherwise:

```text
fresh <= 60 seconds since synchronized_at
stale > 60 seconds since synchronized_at
unavailable = no successful response available
```

## What Each Team Should Provide

For Backoffice:

- list of resources and actions;
- route paths and methods;
- required service scopes;
- required human permissions;
- payload examples;
- audit fields;
- idempotency rules;
- approval thresholds;
- staging URL and test records.

For Pilotage:

- metric names and definitions;
- period and timezone rules;
- summary route;
- service scope;
- example response;
- expected freshness;
- known limitations;
- deep links to Backoffice where action is needed.

## Current Local Contract Models

The first local contract models live in:

```text
backend/app/internal_contracts/
- common.py
- backoffice.py
- pilotage.py
```

They are local to `diddi-admin` while the protocol settles. Later, stable models
can move to a private package such as `diddifree-internal-kit` and be installed
by DiddiGo, DiddiSend, DiddiPay, DiddiFood, Backoffice and Pilotage.

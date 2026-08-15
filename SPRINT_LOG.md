# Sprint Log

This file tracks the main slices we shipped in order, with the commit ids that landed them.
It is intentionally terse so it can serve as an ops-friendly trace.

## Sprint 1

- Added sandbox provider mode and broadened CORS for localhost, diddifree.com, and vercel.com.
- Commit: `690b610`

## Sprint 2

- Added the DiddiPay and DiddiFund API contracts.
- Commit: `652d823`

## Sprint 3

- Added Paystack deposit initialization and webhook handling.
- Commit: `728d274`

## Sprint 4

- Added automatic wallet provisioning on first authenticated access.
- Commit: `5c71ac9`

## Sprint 5

- Added wallet ops backfill and Paystack reconciliation.
- Commit: `6ff1900`

## Sprint 6

- Added internal ops maintenance CLI.
- Commit: `80ec847`

## Sprint 7

- Added bulk Paystack reconciliation sweep.
- Commit: `800816a`

## Sprint 8

- Added business reference support for merchant payments.
- Commit: `aecf765`

## Sprint 9

- Added merchant wallet backfill ops support.
- Commit: `df4575c`

## Sprint 10

- Added durable outbox relay for wallet events.
- Commit: `6bc86be`

## Sprint 11

- Relayed outbox events on startup.
- Commit: `1194d8d`

## Sprint 12

- Added `httpx` to runtime dependencies for the Paystack adapter.
- Commit: `0534633`

## Sprint 13

- Fixed Alembic version length for the migration id.
- Commit: `5191e6e`

## Sprint 14

- Added the outbox migration and startup guard.
- Commit: `3927c9f`

## Sprint 15

- Added outbox ops endpoints.
- Commit: `fab8c74`

## Sprint 16

- Added outbox ops tests.
- Commit: `0c02f37`

## Sprint 17

- Added loan disbursement trace coverage.
- Commit: `41b7ab7`

## Sprint 18

- Added loan repayment trace coverage.
- Commit: `6852c4c`

## Sprint 19

- Added readiness probe for the outbox path.
- Commit: `3f284ef`

## Sprint 20

- Added Paystack transaction audit endpoint.
- Commit: `3127fdb`

## Sprint 21

- Added pending Paystack deposits ops view.
- Commit: `68cbd8a`

## Sprint 22

- Added Paystack reconciliation audit logs and duplicate-finalized callback guards.
- Commit: `79677be`

## Sprint 23

- Added durable webhook inbox tracking for Paystack callbacks.
- Commit: `6b6cdb8`

## Sprint 24

- Added a provisioning status ops endpoint to inspect wallet creation state and phone index for a user.
- Commit: `f485d81`

## Sprint 25

- Added a second sandbox payment rail for `wave` and verified the app in Docker with a live health check.
- Commit: `80f63f7`

## Sprint 26

- Extended the Wave sandbox to cover withdrawal initiation as well as deposits.
- Commit: `620519e`

## Sprint 27

- Added a combined housekeeping runner for Paystack reconciliation and outbox relay.
- Commit: `5e1bf8a`

## Sprint 28

- Added structured JSON observability logs for deposits, withdrawals, Paystack webhooks, and ops maintenance flows.
- Commit: `5c9cde9`

## Notes

- The exact code changes continue to be verified by tests and compile checks before each commit.
- Unrelated worktree files are left untouched.

## Sprint 29

- Added KYC document hooks so wallet users can be linked to external file references such as diddifiles.
- Commit: `a1768c5`

## Sprint 30

- Hardened the public contracts for DiddiPay and DiddiFund by documenting wallet provisioning, ops recovery routes, reconciliation views, and the KYC/document boundary.
- Commit: `5a1f0ed`

## Sprint 31

- Added transaction PIN security for wallets, including mandatory PIN setup, PIN-protected P2P transfers, recovery codes, recipient lookup, and supporting database migrations/tests.
- Commit: `5094a4a`

## Sprint 32

- Documented wallet step-up OTP, admin PIN recovery, wallet self-heal provisioning, and the frontend/module usage contract for DiddiPay.
- Commit: `645d19a`

## Sprint 33

- Added a backend integration brief for DiddiGo, DiddiFiles, and future modules consuming DiddiPay.
- Commit: `3cca329`

## Sprint 34

- Enforced Alembic model parity, migrated PIN hashes to Argon2id, added one-time signed step-up
  proof storage, and required a DiddiFreeID proof for initial PIN creation.
- Commits: `ceb3237`, `d7bdc4c`, `8a6d2f2`, `74bf518`, `8daab17`

## Sprint 35

- Required the transaction PIN for every user-initiated debit, including merchant payment,
  withdrawal, investment, and loan repayment.
- Commit: `a1c50e8`

## Sprint 36

- Replaced the local logging-only transfer OTP with signed, purpose-bound, one-time DiddiFreeID
  step-up proofs while preserving idempotent replay.
- Commit: `34c9901`

## Sprint 37

- Kept localhost, DiddiFree, and Vercel origins available when CORS uses an explicit production
  allow-list, with literal escaping for configured origins.
- Commit: `8e5d100`

## Sprint 38

- Aligned the API contracts and integration briefs and added the MVP deployment/closure runbook.
- Commit: this closure documentation commit

## Pivot DiddiPay - orchestrateur de paiements

Cette série repart à Sprint 0 pour distinguer clairement le pivot PaymentIntent des 38 sprints
wallet historiques.

| Sprint | Livraison | Commit |
|---:|---|---|
| 0 | domaine PaymentIntent provider-neutral | `63cda57` |
| 1 | persistance des intentions, tentatives et événements provider | `329f0f5` |
| 2 | ports processeur et routage multi-provider | `685307a` |
| 3 | API S2S PaymentIntent | `3889d22` |
| 4 | adaptateur Paystack | `31bfc3a` |
| 5 | webhook Paystack durable | `9de188e` |
| 6 | reconciliation des tentatives incertaines | `50b6efa` |
| 7 | outbox transactionnelle de paiement | `6f916a0` |
| 8 | contrat API et briefing frontend PaymentIntent | `68121e0` |
| 9 | livraison signée, retries et dead letters vers les modules | `4448e90` |
| 10 | receiver DiddiGo idempotent | `113d89c` |
| 11 | investissement DiddiFund par PaymentIntent | `36630a8` |
| 12 | remboursements provider-neutral | `3793d98` |
| 13 | sous-ledger de capture, frais, remboursement et settlement | `779292f` |
| 14 | claim/lease concurrent, HTTPS callbacks et observabilité ops | `de99c2b` |
| 15 | parité Swagger, contrats finaux et migration wallet | `1c792d4`, `c2a8a4e` |
| 16 | audit Docker, runbook de production et clôture MVP | commit de clôture |

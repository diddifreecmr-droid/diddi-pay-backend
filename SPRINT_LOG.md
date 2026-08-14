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

## Notes

- The exact code changes continue to be verified by tests and compile checks before each commit.
- Unrelated worktree files are left untouched.

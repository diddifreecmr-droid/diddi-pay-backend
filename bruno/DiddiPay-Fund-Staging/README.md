# DiddiPay / DiddiFund - staging black-box suite

This Bruno collection calls the deployed staging APIs directly. It does not use the local FastAPI
test client or the local database.

## Safety first

No JWT, OTP, PIN, Paystack key or DiddiPay service key belongs in a committed `.bru` file.
`environments/Staging.bru` reads sensitive values from `process.env`. Copy `.env.example` to `.env`
inside this collection and fill only the credentials needed for the selected suite. `.env`, reports
and `node_modules` are ignored.

The old staging environment contained live-looking tokens and an OTP. They were removed. If any of
those values were still valid, revoke them before reusing the test accounts.

## Install

From this collection directory:

```powershell
npm install --cache .npm-cache
```

The Bruno CLI is pinned locally; no global installation is required.

## Test levels

### 1. Deployment contract, no secret

```powershell
npm run test:staging:contract
```

Checks health, readiness, all 48 MVP HTTP operations in OpenAPI, authentication on every sensitive
PIN/ops route, and signature rejection on both Paystack webhook generations.
This is the first post-deployment gate. If it lists missing operations, staging is serving an
older image or an incomplete router set even when `/health` returns 200.

### 2. PaymentIntent S2S

Required `.env` values:

```dotenv
BRUNO_DIDDIPAY_SERVICE_KEY=<staging diddigo key>
BRUNO_DIDDIFUND_SERVICE_KEY=<staging diddifund key>
BRUNO_PAYMENT_CUSTOMER_EMAIL=<staging Paystack customer email>
```

Run:

```powershell
npm run test:staging:s2s
```

Coverage: creation, exact replay, idempotency conflict, owner read/list, cross-module isolation,
financial summary, invalid cancellation/refund, missing idempotency, invalid network, list limits
and unknown IDs. Creation initializes a staging provider transaction but never submits card data or
approves a Mobile Money prompt.

### 3. DiddiFund PaymentIntent

Required `.env` value:

```dotenv
BRUNO_ACCESS_TOKEN_A=<fresh DiddiFreeID staging JWT>
```

Run:

```powershell
npm run test:staging:fund
```

It verifies that the new investment route exists, preserves campaign rules, protects payment
orders and rejects unsigned DiddiPay callbacks. A successful external investment cannot be reached
through the public API until an authorized back-office process activates a campaign.

### 4. Legacy wallet and fund

The legacy suite requires two disposable users, fresh JWTs, an admin JWT and disposable wallet
PINs. Put their IDs, phones, tokens and PINs in `.env`, then run:

```powershell
npm run test:staging:legacy
```

The first wallet requests reset both test PINs through the audited admin recovery endpoint. Never
run this profile with production users. Deposit behavior still depends on the configured staging
gateway and may remain pending until a provider callback.

### 5. Full collection

```powershell
npm run test:staging:all
```

Use this only after every value in `.env.example` needed by Identity, wallet, PaymentIntent and
DiddiFund has been supplied. OTP verification remains interactive because codes are short-lived and
single-use.

## Reports and CI

Each npm command writes masked JSON and JUnit reports under `reports/`. Headers and bodies are
excluded so CI artifacts cannot leak JWTs, service keys, PINs, checkout URLs or customer data.
Request scripts use ephemeral runtime variables for tokens and generated IDs; they never rewrite
the committed staging environment.

Recommended deployment pipeline:

1. deploy the image and wait for the container health check;
2. run `test:staging:contract` without secrets;
3. inject staging-only S2S keys and run `test:staging:s2s`;
4. inject a short-lived user JWT and run `test:staging:fund`;
5. publish only the JUnit report;
6. block promotion when any assertion fails.

## What cannot be fully automated from this repository

- reading an OTP delivered by DiddiFreeID;
- approving a real Mobile Money prompt or entering card data on Paystack checkout;
- activating a campaign or disbursing a loan through back-office controls;
- proving that Paystack settlement reached the real bank account.

Those are staging acceptance scenarios, not reasons to weaken assertions. The automated suite
tests every externally reachable invariant and clearly identifies the remaining human/provider
steps.

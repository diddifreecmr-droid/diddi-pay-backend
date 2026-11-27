# Intégration DiddiShop

## Provisioning DiddiPay

DiddiPay doit créer un credential S2S dédié à DiddiShop. Le secret réel est injecté dans le
gestionnaire de secrets du déploiement ; il ne doit jamais être commité.

```text
client_id: diddishop-staging-diddipay
audience: diddipay
scope: diddipay:payment-intents:write
```

Configuration DiddiPay :

```text
PAYMENT_SERVICE_CLIENT_IDS=...,diddishop-staging-diddipay
PAYMENT_SERVICE_KEYS=...,diddishop-staging-diddipay:<secret-dedie>
PAYMENT_SERVICE_AUDIENCE=diddipay
PAYMENT_INTENT_WRITE_SCOPE=diddipay:payment-intents:write
```

Configuration DiddiShop :

```text
DIDDIPAY_BASE_URL=https://pay-api-staging.diddifree.com/payfund/v1
DIDDIPAY_CLIENT_ID=diddishop-staging-diddipay
DIDDIPAY_SERVICE_KEY=<meme-secret-dedie>
```

## Appel autorisé

```text
GET /payment-intents/{intent_id}
```

Avec l'authentification par clé legacy :

```http
X-Client-ID: diddishop-staging-diddipay
X-Service-Key: <secret-dedie>
```

Le credential doit être différent de ceux de DiddiGo, DiddiSend et DiddiFood. La rotation se
fait en coordination entre les deux équipes.

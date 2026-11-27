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

Le credential doit etre different de ceux de DiddiGo, DiddiSend et DiddiFood. La rotation se
fait en coordination entre les deux equipes.

## Verification par un autre module

DiddiShop reste le proprietaire du PaymentIntent. DiddiSend ne doit pas utiliser le `GET`
proprietaire pour lire l'intent, car il recevrait volontairement `404`. Pour valider une
commande DiddiShop sans obtenir un acces global, DiddiPay expose une route de verification
dediee :

```text
POST /payment-intents/{intent_id}/verify
```

DiddiPay provisionne a DiddiSend un JWT S2S avec le scope :

```text
diddipay:payment-intents:verify
```

La configuration DiddiPay borne explicitement DiddiSend aux intents DiddiShop :

```env
PAYMENT_INTENT_VERIFY_SCOPE=diddipay:payment-intents:verify
PAYMENT_INTENT_VERIFY_OWNER_CLIENTS={"diddisend-staging":["diddishop-staging-diddipay"]}
```

La verification exige dans le corps le montant, `XOF` et la `business_reference`. Le resultat
est positif uniquement pour un intent `succeeded` correspondant exactement a ces trois valeurs.
Il n'y a aucun secret partage entre DiddiShop et DiddiSend pour ce parcours.

# Briefing Frontend (Flutter) — DiddiPay / DiddiFund API

Ce document explique comment lancer l'API backend en local pour développer et tester l'app
Flutter contre elle. Il ne couvre pas le contrat des routes en détail — pour ça, voir
`DiddiPay_DiddiFund_Contrat_API.md` (métier) et `DiddiFreeID_Contrat_API.md` (authentification).

## 1. C'est quoi

Un monolithe Python/FastAPI qui expose deux modules :
- **wallet** (DiddiPay) : solde, transferts, paiement marchand, dépôt/retrait, QR de paiement.
- **fund** (DiddiFund) : campagnes de crowdlending, investissement, prêts.

L'authentification n'est **pas** gérée par cette API : elle vérifie seulement les tokens JWT émis
par le service **DiddiFreeID** (identité), voir section 4 — c'est le point le plus important pour
toi.

## 2. Lancer le backend en local

Prérequis : Docker Desktop installé et lancé.

```bash
git clone <ce dépôt>
cd diddipay-fund
cp .env.example .env
docker compose up -d --build
```

Ça démarre Postgres, Redis et l'API ensemble, migrations Alembic rejouées automatiquement au
démarrage. Vérifie que ça tourne :

```bash
docker compose logs -f app
```

**Ports** : par défaut dans `.env.example`, l'API écoute sur `48213` (host). Si ce port est déjà
pris sur ta machine, change `APP_PORT` dans `.env` avant de relancer.

## 3. URL de base à utiliser dans l'app Flutter

```
http://<host>:48213/payfund/v1
```

Documentation interactive (Swagger) pour explorer toutes les routes et leurs schémas exacts :

```
http://<host>:48213/payfund/v1/docs
```

`<host>` dépend de où tourne le backend par rapport à l'app :

| Contexte Flutter | Valeur de `<host>` |
|---|---|
| Émulateur Android | `10.0.2.2` (pas `localhost` — l'émulateur a son propre réseau) |
| Simulateur iOS | `localhost` fonctionne |
| Appareil physique (même Wi-Fi que le backend) | l'IP LAN de la machine qui héberge Docker, ex. `192.168.1.x` |
| Flutter Web | `localhost` — **mais voir l'avertissement CORS ci-dessous** |

⚠️ **Flutter Web** : l'API n'a **aucun middleware CORS configuré** actuellement. Si tu comptes
tester en Flutter Web (Chrome), les requêtes seront bloquées par le navigateur. Dis-le-moi et
j'ajoute `CORSMiddleware` côté backend — ce n'est pas fait par défaut pour ne pas ouvrir l'API
inutilement en prod.

## 4. Authentification — le point critique

Cette API ne délivre **aucun token elle-même**. Elle vérifie localement les JWT émis par
**DiddiFreeID**, en environnement de staging :

```
https://auth-staging.diddifree.com/identity/v1/.well-known/jwks.json
```

Concrètement, pour appeler une route protégée (quasiment toutes), il te faut un vrai
`access_token` obtenu en appelant le service DiddiFreeID staging lui-même — le backend local ne
peut pas en fabriquer un valide (il n'a que la clé publique, pas la clé privée de signature) :

1. `POST /auth/register` — créer un utilisateur (téléphone + nom)
2. `POST /auth/otp/request` — demander un code OTP
3. `POST /auth/otp/verify` — récupérer `access_token` + `refresh_token`

Base URL DiddiFreeID (dev) : `https://api-dev.diddifree.app/identity/v1` (à confirmer — c'est
celle du contrat DiddiFreeID, différente de l'URL JWKS staging ci-dessus ; si l'une des deux ne
répond pas, demande-moi de vérifier laquelle est la bonne actuellement active).

Puis sur chaque requête à l'API DiddiPay/DiddiFund :

```
Authorization: Bearer <access_token>
```

Token expiré → l'API répond `401 TOKEN_EXPIRED` ; c'est au frontend d'appeler
`POST /auth/refresh` sur DiddiFreeID, pas à cette API de le faire.

## 5. Conventions à connaître côté client

- **Toutes les erreurs** ont la même forme :
  ```json
  { "error": { "code": "SOME_CODE", "message": "...", "details": null } }
  ```
- **Idempotency-Key** : header **obligatoire** sur toute route qui déplace des fonds (transfert,
  paiement marchand, dépôt, retrait, investissement, remboursement). Génère un UUID côté client à
  chaque tentative d'action utilisateur ; si tu rejoues la même requête avec la même clé (retry
  réseau), l'API renvoie la transaction déjà créée au lieu de la dupliquer.
- **Montants** : toujours des entiers en unité mineure (XOF : pas de décimales, donc `5000` =
  5000 XOF, pas de centimes à gérer côté UI pour l'instant).
- **QR code marchand** : le contrat ne fixait pas le format, il est maintenant fixé par le
  backend (jeton signé HMAC opaque, à traiter comme une chaîne à scanner/afficher telle quelle,
  pas à parser côté client). Détails dans `README.md` section « QR code de paiement marchand ».

## 6. Routes disponibles aujourd'hui

Voir le tableau à jour dans `README.md` (« Ce qui est implémenté ») — toutes les routes wallet et
fund listées y sont fonctionnelles. Swagger (`/payfund/v1/docs`) reste la source de vérité pour
les schémas de requête/réponse exacts.

### Wallet UX now

- Le wallet n'est pas créé par un bouton "Créer mon compte". Le flux normal est:
  1. login DiddiFreeID,
  2. `GET /wallet/balance`,
  3. affichage du solde.
- Si un transfert P2P dépasse le seuil sensible, l'UI doit d'abord appeler
  `POST /wallet/transfer/step-up/request`, afficher la confirmation OTP, puis rejouer
  `POST /wallet/transfer` avec `pin` + `otp_code`.
- Le PIN est un vrai secret transactionnel. Il ne doit jamais être remplacé par un simple écran
  "confirmez le montant".
- En cas de perte du PIN, le parcours normal est la récupération par code de secours. Le chemin
  support/admin n'est qu'un filet d'exploitation.

## 7. Ce qui n'est pas encore branché

- Dépôt/retrait via Orange Money / MTN : mode `stub` actuellement (`PAYMENT_GATEWAY_MODE=stub`),
  les opérations restent `pending` tant que personne ne les confirme manuellement côté backend —
  utile à savoir si tu testes ce flux, ce n'est pas un bug si ça ne se confirme pas tout seul.
- CORS (voir section 3) — à activer sur demande si Flutter Web est dans le scope.

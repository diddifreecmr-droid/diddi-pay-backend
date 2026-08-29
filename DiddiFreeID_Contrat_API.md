# DiddiFreeID — Contrat API

**Destiné à :** toutes les équipes modules (Wallet, Fund, Ride/DiddiGo, Shop, Skill...) et aux équipes
Frontend/Mobile.
**Base URL (dev) :** `https://api-dev.diddifree.app/identity/v1`
**Format :** JSON exclusivement · `Content-Type: application/json`
**Référence architecture :** `DiddiFreeID_Architecture.md`
**Version : 2.0** — 2026-08-05
**Historique :**
- *Design* — contrat initial, avant implémentation.
- **v1.0 (2026-07-29)** — première version livrée. **Aucune rupture** par rapport au contrat de design :
  tout ce qui y était publié se comporte comme annoncé. Les mentions « **Implémentation :** » signalent
  soit des routes nouvelles, soit des champs additifs, soit des précisions sur des cas que le design
  laissait sans réponse.
- **v2.0 (2026-08-05)** — ajout du canal OTP e-mail alternatif, du champ e-mail dans le profil et de
  la confirmation du canal effectif dans la réponse OTP. Cette évolution est additive : les routes
  restent sous `/identity/v1` jusqu'à l'introduction d'une rupture incompatible.

Ce document est un **contrat**. Toute évolution incompatible sera versionnée (`/v2`), jamais poussée en
silence sur `/v1`. Les conventions (format d'erreur, codes HTTP, pagination) reprennent volontairement
celles déjà en usage côté DiddiGo, pour que les équipes n'aient qu'un seul standard à connaître dans tout
l'écosystème.

---

> **Mise à jour d'architecture (2026-08-04)** : DiddiFreeID ne possède que les
> rôles globaux de plateforme (`user`, `admin`). Les rôles métier comme
> `driver`, `merchant`, `investor` ou `campaign_owner` appartiennent à leurs
> modules. Les anciennes sections KYC/role métier sont conservées comme
> historique de migration ; une nouvelle demande de rôle métier répond
> `409 ROLE_OWNED_BY_MODULE`.

## 0. Conventions générales

### Deux façons de consommer DiddiFreeID

1. **Vérification de token — la voie normale, locale, sans appel réseau.** Chaque module récupère la clé
   publique via `GET /.well-known/jwks.json` (mise en cache), et vérifie lui-même la signature RS256 de
   chaque `access_token` reçu. C'est le chemin emprunté à **chaque requête** de **chaque module**.
2. **Appels HTTP directs à DiddiFreeID — l'exception, réservée à :** l'émission/rafraîchissement de
   tokens (section 1), la récupération de profil complet quand le JWT ne suffit pas (section 2), et
   l'administration (section 3).

**Ne jamais appeler DiddiFreeID en HTTP pour simplement vérifier qu'un token est valide** — ce serait
réintroduire le goulot d'étranglement que l'architecture est justement conçue pour éviter.

**Implémentation :** le code de vérification est fourni, prêt à être copié dans un module :
`identity_app/shared_kernel/contracts/identity_provider.py`. Il ne dépend que de `httpx` et `pyjwt`, gère
le cache du JWKS et le rafraîchissement immédiat sur `kid` inconnu. Inutile de le réécrire par équipe.

### Format d'erreur (identique à DiddiGo)

```json
{
  "error": {
    "code": "USER_NOT_FOUND",
    "message": "Aucun utilisateur trouvé avec cet identifiant.",
    "details": null
  }
}
```

**Implémentation :** les erreurs de validation de champs adoptent la même enveloppe, avec `code`
= `VALIDATION_ERROR` et un `details` listant les champs fautifs. Le format `{"detail": [...]}` du
framework n'apparaît nulle part — la promesse « toutes les erreurs se ressemblent » vaut aussi pour la
route qu'on tape le plus pendant une intégration.

### Codes HTTP utilisés

| Code | Signification |
|---|---|
| `200` | Succès |
| `201` | Ressource créée |
| `204` | Succès, sans contenu |
| `400` | Requête malformée |
| `401` | Non authentifié / token invalide, expiré, ou révoqué |
| `403` | Authentifié mais non autorisé (ex. route admin appelée par un `role=user`, ou compte suspendu) |
| `404` | Ressource inexistante |
| `409` | Conflit d'état (ex. téléphone déjà enregistré, transition de statut impossible) |
| `410` | Ressource expirée et définitivement inutilisable (code OTP périmé) |
| `422` | Validation de champs échouée |
| `429` | Trop de requêtes (OTP demandé trop souvent, trop de tentatives) |
| `500` | Erreur serveur |

**Implémentation :** `204` et `410` ont été ajoutés à ce tableau. Ils étaient déjà utilisés par les routes de la
section 1 mais absents du récapitulatif — un `410` distingue « ce code a expiré, redemandes-en un » de
« ce code est faux, vérifie ce que tu as saisi », et les deux appellent des messages différents côté
application.

### Dates

ISO 8601 UTC, ex. `"2026-08-04T14:20:00Z"`.

### Préfixe d'URL

Les routes sont montées sous `API_PREFIX`, qui vaut `/identity/v1` par défaut et correspond à la base URL
ci-dessus. Une passerelle qui retire déjà le segment `/identity` peut le régler sur `/v1` sans changement
de code. Le JWKS fait exception : il est servi **à la fois** sous le préfixe et à la racine du domaine,
là où la RFC 8615 le place et où toute bibliothèque JWT standard le cherche par défaut.

---

## 1. Émission et cycle de vie des tokens

### `POST /auth/register`

`phone` ou `email` est obligatoire. Les deux peuvent être fournis pour un
compte joignable par les deux identifiants, mais au moins un doit être présent.
L'e-mail permet aussi un login OTP sans numéro de téléphone.

**Requête**
```json
{ "phone": "+2250700000000", "email": "awa@example.com", "full_name": "Awa Koné" }
```

Compte e-mail uniquement : `{ "email": "awa@example.com", "full_name": "Awa Koné" }`.
Pas de champ `role` ici, contrairement à DiddiGo — le rôle par défaut est `"user"`. Un module (ex. Ride)
qui a besoin qu'un utilisateur devienne `driver` appelle `PATCH /users/{id}/role` (section 3) après
inscription, une fois son propre processus de qualification (permis, véhicule...) validé. DiddiFreeID ne
décide jamais seul qu'un utilisateur est chauffeur, marchand, etc. — chaque module reste propriétaire de
sa propre logique de qualification et déclenche le changement de rôle via l'API admin.

**Réponse `201`**
```json
{ "user_id": "b3e1...", "phone": "+2250700000000", "status": "pending_verification" }
```

Avec une inscription e-mail uniquement, `phone` vaut `null`.

**Erreurs** : `422` (`INVALID_PHONE_FORMAT` ou `IDENTIFIER_REQUIRED`), `409`
(`PHONE_ALREADY_REGISTERED` ou `EMAIL_ALREADY_REGISTERED`)

---

### `POST /auth/otp/request`

**Identifiant** : fournir exactement un `phone` ou un `email`. Le canal
`email` peut donc être utilisé sans numéro. `channel` accepte `email` ou
`telegram`. Le frontend
devrait l'envoyer explicitement pour que le choix de livraison soit clair.
S'il est absent, `OTP_PROVIDER` choisit le fournisseur configuré. Le canal
`email` nécessite une adresse e-mail enregistrée sur le compte. Quel que soit
le canal, le code reste visible lorsque `OTP_LOG_PLAINTEXT=true`.

**Requête e-mail avec téléphone** : `{ "phone": "+2250700000000", "channel": "email" }`

**Requête e-mail sans téléphone** : `{ "email": "awa@example.com", "channel": "email" }`

**Requête Telegram** : `{ "phone": "+2250700000000", "channel": "telegram" }`

**Réponse `200`** :
`{ "expires_in_seconds": 300, "retry_after_seconds": 60, "channel": "email" }`

`channel` vaut `email`, `telegram` ou `logging` (ce dernier uniquement pour le
mode développement/staging sans transport configuré).

**Erreurs** : `409` (`EMAIL_NOT_CONFIGURED` si un compte téléphone n'a pas
d'e-mail), `422` (`TELEGRAM_REQUIRES_PHONE`), `429` (`OTP_RATE_LIMITED`, avec
`details.retry_after_seconds`)

**Implémentation — la réponse est identique pour un identifiant connu et un identifiant inconnu.** Aucun OTP n'est
envoyé dans le second cas, mais le corps, le code HTTP et le cooldown sont les mêmes. Toute différence ferait de
cette route un oracle permettant de savoir si une personne donnée est cliente de DiddiFree. Un appelant qui demande
un code pour un identifiant inconnu n'en reçoit simplement jamais, et `verify` lui répondra le `400 OTP_INVALID`
habituel.

**Implémentation — deux limites indépendantes** : une par identifiant (le `retry_after_seconds` annoncé) et une par
adresse IP, plus permissive, qui existe pour arrêter un script balayant une plage d'identifiants depuis une
même machine. Les deux répondent `429 OTP_RATE_LIMITED`.

---

### `POST /auth/otp/verify`

**Requête téléphone** : `{ "phone": "+2250700000000", "code": "482913" }`

**Requête e-mail** : `{ "email": "awa@example.com", "code": "482913" }`

**Implémentation :** champ optionnel `device_info` (200 caractères max), conservé avec le refresh token pour permettre
à l'utilisateur d'identifier ses sessions.

**Réponse `200`**
```json
{
  "access_token": "eyJhbGciOiJSUzI1NiJ9...",
  "refresh_token": "opaque_a1b2c3...",
  "user": {
    "id": "b3e1...",
    "phone": "+2250700000000",
    "full_name": "Awa Koné",
    "role": "user",
    "status": "active",
    "requested_role": null
  }
}
```
Émet l'événement interne `user.registered` si c'était la première vérification de ce compte.

**Erreurs** : `400` (`OTP_INVALID`), `410` (`OTP_EXPIRED`), `429` (`OTP_TOO_MANY_ATTEMPTS`)

**Implémentation — précisions :**

- `400 OTP_INVALID` porte `details.attempts_remaining`, pour que l'application puisse prévenir avant le
  dernier essai.
- Une fois le plafond de tentatives atteint, le code est **consommé** : même le bon code ne fonctionne
  plus, il faut en redemander un. Sans cela, le plafond ne ferait que ralentir une attaque.
- `404 USER_NOT_FOUND` si aucun compte n'existe pour cet identifiant. La création de compte appartient à
  `/auth/register` : en créer un ici contournerait le `409` sur doublon et produirait des comptes sans
  `full_name`.
- `403 USER_SUSPENDED` si le compte est suspendu.
- Si un rôle soumis à KYC a été demandé avant cette première vérification, le compte passe en
  `pending_kyc` au lieu d'`active` (voir section 3, `PATCH /users/{id}/role`).

---

### `POST /auth/refresh`

**Requête** : `{ "refresh_token": "opaque_a1b2c3..." }`
**Réponse `200`** : `{ "access_token": "...", "refresh_token": "..." }` (rotation : l'ancien refresh
token est révoqué à chaque utilisation)
**Erreurs** : `401` (`REFRESH_TOKEN_INVALID` ou `REFRESH_TOKEN_REVOKED`)

**Implémentation — ce que `REFRESH_TOKEN_REVOKED` implique, et qui doit être géré côté client.** Présenter un refresh
token déjà tourné **révoque toutes les sessions de l'utilisateur**, pas seulement celle-ci. Fuite de token
et retry d'un client sont indistinguables à cet endroit, et réémettre un couple donnerait une durée de vie
illimitée à un token volé. Conséquence pratique : un client qui reçoit ce code doit renvoyer l'utilisateur
vers l'écran de connexion, pas réessayer. Ne jamais paralléliser deux appels à `/auth/refresh` avec le
même token — c'est le meilleur moyen de se déconnecter tout seul.

**Implémentation :** `403 USER_SUSPENDED` si le compte n'est plus actif. Un compte en `pending_kyc` peut en revanche
rafraîchir normalement : son propriétaire doit pouvoir suivre son dossier.

---

### `POST /auth/logout`

**Requête** : `{ "refresh_token": "opaque_a1b2c3...", "all_devices": false }`
**Réponse `204`** : pas de contenu. Révoque le refresh token fourni, ou tous les tokens actifs de
l'utilisateur si `all_devices: true`.

**Implémentation :** idempotent. Un token inconnu ou déjà révoqué répond également `204` — se déconnecter deux fois
n'est pas une erreur, et cette route ne confirme donc pas non plus qu'une chaîne donnée a déjà existé.

---

## 2. Vérification locale du token — ce que chaque module doit implémenter

### `GET /.well-known/jwks.json`

**Réponse `200`**
```json
{
  "keys": [
    { "kid": "2026-07-01", "kty": "RSA", "use": "sig", "alg": "RS256", "n": "...", "e": "AQAB" }
  ]
}
```
Peut contenir deux clés pendant une rotation (l'ancienne encore valide pour les tokens émis avant le
switch, la nouvelle pour les tokens émis après). Chaque module choisit la clé par le champ `kid` présent
dans le header du JWT reçu.

**Implémentation :** servi à la racine du domaine **et** sous le préfixe `/identity/v1`, avec un en-tête
`Cache-Control: public, max-age=3600`. Un module qui respecte simplement les en-têtes HTTP adopte donc le
bon comportement de cache sans code particulier.

### Contenu du JWT `access_token` (à décoder localement)

```json
{
  "sub": "b3e1...",
  "role": "user",
  "status": "active",
  "iss": "diddifree-id",
  "iat": 1753700000,
  "exp": 1753700900
}
```
`sub` = `user_id`. Un module qui reçoit un token avec `status != "active"` doit refuser l'action (compte
suspendu) même si la signature est valide — le `status` n'est rafraîchi qu'à la prochaine émission de
token (max 15 min de délai, acceptable ; sinon voir `user.suspended` en section 4 pour une réaction
immédiate).

**Implémentation :** le claim `iss` a été ajouté et **doit être vérifié** (`diddifree-id`). Valeurs possibles de
`status` : `pending_verification`, `pending_kyc`, `active`, `suspended`. La règle ci-dessus est
inchangée et couvre le nouveau statut : `pending_kyc` n'est pas `active`, donc un module refuse d'agir.

**Comportement attendu côté module en cas de token expiré** : renvoyer `401` avec
`error.code = "TOKEN_EXPIRED"` à son propre client — c'est au frontend d'appeler
`POST /auth/refresh`, pas au module de le faire à la place de l'utilisateur.

---

### `GET /users/me`

Le profil contient également `email`, qui peut être défini ou supprimé avec
`PATCH /users/me`.

Pour les cas où un module a besoin du profil complet (ex. afficher `full_name` sur un reçu DiddiPay) et
ne veut pas le maintenir en cache lui-même.

**Header requis** : `Authorization: Bearer <access_token>`

**Réponse `200`**
```json
{
  "id": "b3e1...",
  "phone": "+2250700000000",
  "email": "awa@example.com",
  "full_name": "Awa Koné",
  "language": "fr",
  "photo_url": null,
  "role": "user",
  "status": "active",
  "requested_role": null
}
```

**Implémentation — `requested_role`** est un champ **additif** : il porte le rôle en attente de décision KYC, ou
`null`. Un consommateur qui lit uniquement les champs de la v1 n'est pas affecté.

**Erreurs** : `401` (`TOKEN_MISSING`, `TOKEN_EXPIRED`, `TOKEN_INVALID`), `403` (`USER_SUSPENDED`,
`USER_NOT_VERIFIED`)

Un compte en `pending_kyc` accède à cette route : son propriétaire doit pouvoir ouvrir l'application et
voir où en est sa demande.

### `PATCH /users/me`

Le profil global contient aussi `language` (`fr` ou `en`, `fr` par défaut) et
`photo_url` (lien vers la photo, sans upload géré par DiddiFreeID pour le
moment). Ces champs peuvent être modifiés avec le nom.

```json
{
  "email": "awa.new@example.com",
  "language": "en",
  "photo_url": "https://cdn.example.com/profile.jpg"
}
```

Lorsqu'il existe, le numéro reste une donnée d'identité vérifiée. Son changement
ne passe pas par ce PATCH général : il nécessitera une procédure OTP dédiée sur
le nouveau numéro.

**Implémentation — nouvelle route.** Modification du profil par l'utilisateur lui-même.

**Requête** : `{ "full_name": "Awa Koné-Traoré" }`
**Réponse `200`** : le profil mis à jour. Émet `user.updated` avec les champs modifiés — sauf si rien n'a
réellement changé, auquel cas aucun événement ne part : les abonnés purgent leur cache sur `user.updated`,
et une modification à vide n'a pas à faire tomber les caches de tout l'écosystème.

### `GET /users/{user_id}`

Réservé aux appels **service-à-service** (pas exposé au frontend directement) — un module backend qui a
besoin du profil d'un utilisateur autre que celui du token courant (ex. Fund affichant le nom d'un
porteur de campagne à un investisseur).

**Implémentation — le mécanisme d'authentification, laissé ouvert en v1, est implémenté sous ses deux formes**, en
attendant l'arbitrage réseau définitif avec l'équipe Infra. Chacune se désactive par configuration :

1. **En-tête `X-Service-Key`**, comparé à la liste `SERVICE_API_KEYS`.
2. **Access token portant `role=service`**, émis hors ligne par
   `scripts/issue_service_token.py --service diddi-wallet`. Ces tokens n'ont pas de flux de refresh et
   ne sont pas révocables individuellement : leur expiration est le seul mécanisme qui les retire de la
   circulation, donc durée courte et réémission planifiée.

Un token d'administrateur est également accepté sur cette route.

**Réponse `200`** : même format que `/users/me`.
**Erreurs** : `404` (`USER_NOT_FOUND`), `401` (`SERVICE_KEY_INVALID`, `TOKEN_MISSING`),
`403` (`FORBIDDEN_ROLE` — un `role=user` ordinaire n'a rien à faire ici)

### `GET /users/backfill`

**Implémentation — nouvelle route.** Rattrapage pour un module qui n'a pas pu recevoir les événements.

```
GET /users/backfill?since=2026-07-01T00:00:00Z&page=1&page_size=100
```

Même authentification que `GET /users/{user_id}`. Renvoie les comptes créés à partir de `since`, **du plus
ancien au plus récent**, au format de pagination standard.

À appeler au démarrage par un module qui rejoint l'écosystème après coup, ou qui est resté indisponible
au-delà de la fenêtre de rétention du flux d'événements. L'ordre chronologique croissant permet à un
parcours interrompu de reprendre au dernier `created_at` traité sans sauter personne.

**La consommation doit être idempotente** : le module reverra nécessairement des comptes qu'il connaît
déjà.

---

## 3. Administration (réservé `role=admin`)

Le premier compte administrateur est un bootstrap d'exploitation, pas une
route HTTP. Il est créé avec `scripts/create_admin.py` depuis le conteneur
backend ou un environnement local relié à la base et aux clés RS256. Le script
émet ensuite un access token `role=admin` à durée courte ; il ne stocke jamais
le token.

### `GET /admin/users?role=driver&status=active&page=1&page_size=20`

Liste paginée, filtrable. Réponse au format pagination standard :
```json
{
  "data": [ { "id": "...", "phone": "...", "full_name": "...", "role": "driver", "status": "active", "requested_role": null } ],
  "pagination": { "page": 1, "page_size": 20, "total_items": 340, "total_pages": 17 }
}
```

**Implémentation :**
- Filtre supplémentaire `?pending_kyc=true` — la file d'instruction des demandes de rôle.
- `page_size` est plafonné à 100, pour qu'une seule requête ne puisse pas demander toute la table.
- Un `role` ou un `status` inconnu répond `422` plutôt qu'une page vide : « aucun chauffeur » et « tu as
  mal écrit le filtre » sont deux réponses différentes, et l'opérateur doit pouvoir les distinguer.

### `PATCH /users/{user_id}/role`

Appelé par un **module backend** (pas par le frontend directement) une fois sa propre qualification
validée — ex. Ride appelle cette route une fois le permis d'un chauffeur vérifié côté DiddiGo.

**Requête** : `{ "role": "driver", "reason": "Validation KYC chauffeur DiddiGo, dossier #4021" }`
**Réponse `200`** : profil mis à jour.

Authentification : service-à-service ou administrateur (mêmes mécanismes que `GET /users/{user_id}`).

**Implémentation — pour `driver` et `merchant`, cette route *demande* le rôle, elle ne l'accorde pas.** C'est le
portillon KYC déplacé depuis DiddiGo (architecture §7.5 et §4 bis) :

- la réponse porte `requested_role: "driver"` et **`role` reste inchangé** ;
- l'événement émis est `user.updated`, **pas** `user.role_changed` — rien n'a encore été accordé ;
- un administrateur tranche ensuite via `PATCH /admin/users/{user_id}/kyc`, et c'est cette décision qui
  émet `user.role_changed`.

**Un module doit donc attendre `user.role_changed` pour activer ses fonctionnalités**, jamais la réponse
`200` de cette route.

Le compte n'est **jamais dégradé** par une demande : un utilisateur actif garde `status: active` et
continue d'utiliser DiddiPay, DiddiShop et le reste pendant l'instruction. Seul un compte encore jamais
activé passera en `pending_kyc` à sa première vérification OTP.

Les autres rôles (`user`, `admin`) sont accordés immédiatement et émettent `user.role_changed`.
Réappeler la route avec un rôle déjà demandé ou déjà attribué est sans effet — un retry après timeout
n'ouvre pas un second dossier.

**Erreurs** : `404` (`USER_NOT_FOUND`), `422` (`INVALID_ROLE`), `401`/`403` selon l'authentification

### `PATCH /admin/users/{user_id}/kyc`

**Implémentation — nouvelle route.** Décision sur une demande de rôle en attente.

**Requête** : `{ "approved": true, "reason": "Permis vérifié, pièce d'identité conforme — dossier #4021" }`

**Réponse `200`** : profil mis à jour.
- **Accord** : `role` devient le rôle demandé, `requested_role` repasse à `null`, `user.role_changed` est
  émis.
- **Refus** : `requested_role` repasse à `null`, le compte reste un utilisateur ordinaire, `user.updated`
  est émis. Un refus refuse le rôle, pas le compte.

Dans les deux cas, un compte qui attendait en `pending_kyc` devient `active`.

La décision et son `reason` sont journalisés dans `identity.user_role_history`, avec l'identifiant de
l'administrateur décisionnaire. Un refus s'y écrit avec `to_role = NULL` : la décision a eu lieu, aucun
rôle n'a été accordé.

**Erreurs** : `404` (`USER_NOT_FOUND`), `409` (`NO_KYC_PENDING` — aucune demande en attente)

### `PATCH /admin/users/{user_id}/status`

**Requête** : `{ "status": "suspended", "reason": "Signalement fraude, ticket #883" }`
**Réponse `200`** : profil mis à jour. Émet `user.suspended` immédiatement (les modules abonnés au bus
d'événements réagissent sans attendre l'expiration du JWT en cours).
**Erreurs** : `409` (`INVALID_STATUS_TRANSITION`)

**Implémentation :**
- La suspension **révoque immédiatement tous les refresh tokens** du compte. Sans cela, la suspension
  n'aurait d'effet qu'à l'expiration du JWT courant.
- Seuls `active` et `suspended` sont acceptés ici. `pending_kyc` répond `422 STATUS_NOT_SETTABLE` : il est
  piloté par le parcours KYC, et le poser à la main sur un compte actif couperait cette personne des douze
  modules pour la revue d'un seul.
- Une réactivation émet `user.updated` (voir section 4).

---

## 4. Événements internes (bus, pas HTTP)

Format d'un événement publié par DiddiFreeID :

```json
{
  "event": "user.registered",
  "user_id": "b3e1...",
  "phone": null,
  "role": "user",
  "at": "2026-07-28T10:15:00Z"
}
```

| Événement | Payload additionnel | À faire côté abonné |
|---|---|---|
| `user.registered` | — | Wallet : créer le compte wallet associé. Skill : créer le profil apprenant vide. |
| `user.updated` | `changed_fields`, et `old_status`/`new_status`/`reason` pour une réactivation | Invalider tout cache local de profil pour ce `user_id` |
| `user.role_changed` | `old_role`, `new_role` | Ride : activer les fonctionnalités chauffeur. Skill : mettre à jour le Talent Pool. |
| `user.suspended` | `old_status`, `new_status`, `reason` | Wallet : geler les transactions sortantes. Ride : désactiver la disponibilité chauffeur. |

**Implémentation — il n'y a toujours que ces quatre noms, et c'est délibéré.** Une réactivation de compte et
l'ouverture d'un dossier KYC passent toutes deux par `user.updated`. Inventer `user.reactivated` ou
`user.kyc_requested` reviendrait à publier des événements auxquels personne n'est abonné, donc des
notifications silencieusement perdues. Ce que ces cas ont besoin de dire — « votre copie du profil est
périmée » — est exactement ce que `user.updated` signifie déjà.

### Le transport : Redis Streams

Le choix laissé ouvert en v1 est arrêté : **Redis Streams**, flux unique `identity.events`.

Pub/Sub ne persistait rien : un abonné arrêté au moment de la publication ne voyait jamais l'événement.
Sans conséquence pour une invalidation de cache, inacceptable pour `user.registered` dont dépend la
création du compte Wallet. Kafka réglerait aussi le problème, mais avec un cluster à exploiter que le
volume actuel ne justifie pas ; le raisonnement complet est en §6 de l'architecture, ainsi que le
déclencheur pour migrer le jour venu. Un seul fichier changera.

Un flux unique plutôt qu'un par type d'événement : l'ordre est ainsi garanti entre les événements d'un
même utilisateur, et un `role_changed` ne peut pas être livré après la suspension qui l'a suivi. Les
consommateurs filtrent par nom.

**Côté abonné**, un helper prêt à l'emploi est fourni dans `shared_kernel/events/bus.py` :

```python
consumer = RedisEventConsumer(redis, group="diddi-wallet", name="worker-1")
await consumer.ensure_group(from_beginning=True)

# Au démarrage : ce qui avait été livré mais jamais acquitté.
for entry_id, event in await consumer.read_pending():
    await handle(event)
    await consumer.ack(entry_id)

while True:
    for entry_id, event in await consumer.read():
        await handle(event)
        await consumer.ack(entry_id)
```

**Deux propriétés à connaître avant d'écrire un abonné :**

1. **Acquitter après traitement, jamais avant.** C'est l'écart entre les deux qui transforme un crash en
   redélivrance plutôt qu'en perte. La contrepartie est que la livraison est **at-least-once** : un
   handler doit être idempotent. Aucun broker, Kafka compris, n'offre l'exactly-once sans la coopération
   du handler.
2. **La rétention du flux est bornée**, et un écrit peut réussir alors que la publication échoue — aucun
   transport ne ferme cet écart tout seul. `GET /users/backfill` (section 2) est le filet pour les deux
   cas, et un module dont la réaction n'est pas rattrapable autrement devrait l'appeler au démarrage.

---

## 5. Ce qui n'est volontairement pas encore dans ce contrat — futures versions

Rien de ce qui suit ne bloque un module qui intègre la v1.0. Le tableau détaillé des déclencheurs est en
section 10 de l'architecture.

- Authentification par mot de passe classique (aujourd'hui OTP uniquement) — à ajouter si un besoin
  back-office (juristes DiddiLegal, praticiens DiddiSanté) l'exige. Le hachage Argon2id et la colonne
  existent déjà côté serveur ; il ne manque que les routes.
- ~~Détail du mécanisme d'authentification service-à-service~~ — **Implémentation : livré sous ses deux formes**
  (clé d'API et token `role=service`), voir section 2. L'arbitrage Infra tranchera laquelle garder ; les
  deux se désactivent par configuration, donc ce choix n'est plus bloquant pour les équipes.
- Endpoints de gestion fine des rôles multiples (un utilisateur à la fois `driver` et `merchant`) — le
  modèle actuel suppose un rôle principal unique par utilisateur ; à revoir si le besoin apparaît. À
  noter : le portillon KYC de la v1.0 raisonne lui aussi sur un rôle demandé à la fois.
- KYC documentaire (upload de pièce d'identité) — probablement un sous-module dédié plutôt qu'un champ de
  plus sur `users`, à spécifier séparément. **Implémentation :** le *portillon* (demande, file d'instruction,
  décision, audit) est en place ; ce qui reste à spécifier est la collecte et le stockage des pièces.
- Historique exposé en HTTP — `user_status_history` et `user_role_history` sont écrites et interrogeables
  en base, mais aucune route ne les publie encore. À ajouter quand une console d'administration en aura
  besoin.

Si un module a besoin d'un de ces points plus tôt que prévu, mieux vaut l'ajouter ici proprement que de
l'improviser côté client.

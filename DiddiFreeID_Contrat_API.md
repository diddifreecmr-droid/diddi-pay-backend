# DiddiFreeID — Contrat API

**Destiné à :** toutes les équipes modules (Wallet, Fund, Ride/DiddiGo, Shop, Skill...) et aux équipes
Frontend/Mobile.
**Base URL (dev) :** `https://api-dev.diddifree.app/identity/v1`
**Format :** JSON exclusivement · `Content-Type: application/json`
**Référence architecture :** `DiddiFreeID_Architecture.md`

Ce document est un **contrat**. Toute évolution incompatible sera versionnée (`/v2`), jamais poussée en
silence sur `/v1`. Les conventions (format d'erreur, codes HTTP, pagination) reprennent volontairement
celles déjà en usage côté DiddiGo, pour que les équipes n'aient qu'un seul standard à connaître dans tout
l'écosystème.

---

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

### Codes HTTP utilisés

| Code | Signification |
|---|---|
| `200` | Succès |
| `201` | Ressource créée |
| `400` | Requête malformée |
| `401` | Non authentifié / token invalide, expiré, ou révoqué |
| `403` | Authentifié mais non autorisé (ex. route admin appelée par un `role=user`) |
| `404` | Ressource inexistante |
| `409` | Conflit d'état (ex. téléphone déjà enregistré) |
| `422` | Validation de champs échouée |
| `429` | Trop de requêtes (OTP demandé trop souvent) |
| `500` | Erreur serveur |

### Dates

ISO 8601 UTC, ex. `"2026-08-04T14:20:00Z"`.

---

## 1. Émission et cycle de vie des tokens

### `POST /auth/register`

**Requête**
```json
{ "phone": "+2250700000000", "full_name": "Awa Koné" }
```
Pas de champ `role` ici, contrairement à DiddiGo — le rôle par défaut est `"user"`. Un module (ex. Ride)
qui a besoin qu'un utilisateur devienne `driver` appelle `PATCH /users/{id}/role` (section 3) après
inscription, une fois son propre processus de qualification (permis, véhicule...) validé. DiddiFreeID ne
décide jamais seul qu'un utilisateur est chauffeur, marchand, etc. — chaque module reste propriétaire de
sa propre logique de qualification et déclenche le changement de rôle via l'API admin.

**Réponse `201`**
```json
{ "user_id": "b3e1...", "phone": "+2250700000000", "status": "pending_verification" }
```

**Erreurs** : `422` (`INVALID_PHONE_FORMAT`), `409` (`PHONE_ALREADY_REGISTERED`)

---

### `POST /auth/otp/request`

**Requête** : `{ "phone": "+2250700000000" }`

**Réponse `200`** : `{ "expires_in_seconds": 300, "retry_after_seconds": 60 }`

**Erreurs** : `429` (`OTP_RATE_LIMITED`, avec `details.retry_after_seconds`)

---

### `POST /auth/otp/verify`

**Requête** : `{ "phone": "+2250700000000", "code": "482913" }`

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
    "status": "active"
  }
}
```
Émet l'événement interne `user.registered` si c'était la première vérification de ce compte.

**Erreurs** : `400` (`OTP_INVALID`), `410` (`OTP_EXPIRED`), `429` (`OTP_TOO_MANY_ATTEMPTS`)

---

### `POST /auth/refresh`

**Requête** : `{ "refresh_token": "opaque_a1b2c3..." }`
**Réponse `200`** : `{ "access_token": "...", "refresh_token": "..." }` (rotation : l'ancien refresh
token est révoqué à chaque utilisation)
**Erreurs** : `401` (`REFRESH_TOKEN_INVALID` ou `REFRESH_TOKEN_REVOKED`)

---

### `POST /auth/logout`

**Requête** : `{ "refresh_token": "opaque_a1b2c3...", "all_devices": false }`
**Réponse `204`** : pas de contenu. Révoque le refresh token fourni, ou tous les tokens actifs de
l'utilisateur si `all_devices: true`.

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

### Contenu du JWT `access_token` (à décoder localement)

```json
{
  "sub": "b3e1...",
  "role": "user",
  "status": "active",
  "iat": 1753700000,
  "exp": 1753700900
}
```
`sub` = `user_id`. Un module qui reçoit un token avec `status != "active"` doit refuser l'action (compte
suspendu) même si la signature est valide — le `status` n'est rafraîchi qu'à la prochaine émission de
token (max 15 min de délai, acceptable ; sinon voir `user.suspended` en section 4 pour une réaction
immédiate).

**Comportement attendu côté module en cas de token expiré** : renvoyer `401` avec
`error.code = "TOKEN_EXPIRED"` à son propre client — c'est au frontend d'appeler
`POST /auth/refresh`, pas au module de le faire à la place de l'utilisateur.

---

### `GET /users/me`

Pour les cas où un module a besoin du profil complet (ex. afficher `full_name` sur un reçu DiddiPay) et
ne veut pas le maintenir en cache lui-même.

**Header requis** : `Authorization: Bearer <access_token>`

**Réponse `200`**
```json
{ "id": "b3e1...", "phone": "+2250700000000", "full_name": "Awa Koné", "role": "user", "status": "active" }
```

### `GET /users/{user_id}`

Réservé aux appels **service-à-service** (pas exposé au frontend directement) — un module backend qui a
besoin du profil d'un utilisateur autre que celui du token courant (ex. Fund affichant le nom d'un
porteur de campagne à un investisseur). Authentification par un token de service (à définir : soit un
JWT `role=service` dédié, soit une clé API inter-services — à trancher en fonction du modèle de
déploiement réseau retenu).

**Réponse `200`** : même format que `/users/me`.
**Erreurs** : `404` (`USER_NOT_FOUND`)

---

## 3. Administration (réservé `role=admin`)

### `GET /admin/users?role=driver&status=active&page=1&page_size=20`

Liste paginée, filtrable. Réponse au format pagination standard :
```json
{
  "data": [ { "id": "...", "phone": "...", "full_name": "...", "role": "driver", "status": "active" } ],
  "pagination": { "page": 1, "page_size": 20, "total_items": 340, "total_pages": 17 }
}
```

### `PATCH /users/{user_id}/role`

Appelé par un **module backend** (pas par le frontend directement) une fois sa propre qualification
validée — ex. Ride appelle cette route une fois le permis d'un chauffeur vérifié côté DiddiGo.

**Requête** : `{ "role": "driver", "reason": "Validation KYC chauffeur DiddiGo, dossier #4021" }`
**Réponse `200`** : profil mis à jour. Émet `user.role_changed`.

### `PATCH /admin/users/{user_id}/status`

**Requête** : `{ "status": "suspended", "reason": "Signalement fraude, ticket #883" }`
**Réponse `200`** : profil mis à jour. Émet `user.suspended` immédiatement (les modules abonnés au bus
d'événements réagissent sans attendre l'expiration du JWT en cours).
**Erreurs** : `409` (`INVALID_STATUS_TRANSITION`)

---

## 4. Événements internes (bus, pas HTTP)

Format d'un événement publié par DiddiFreeID :

```json
{
  "event": "user.registered",
  "user_id": "b3e1...",
  "phone": "+2250700000000",
  "role": "user",
  "at": "2026-07-28T10:15:00Z"
}
```

| Événement | Payload additionnel | À faire côté abonné |
|---|---|---|
| `user.registered` | — | Wallet : créer le compte wallet associé. Skill : créer le profil apprenant vide. |
| `user.updated` | champs modifiés | Invalider tout cache local de profil pour ce `user_id` |
| `user.role_changed` | `old_role`, `new_role` | Ride : activer les fonctionnalités chauffeur. Skill : mettre à jour le Talent Pool. |
| `user.suspended` | `reason` | Wallet : geler les transactions sortantes. Ride : désactiver la disponibilité chauffeur. |

Le mécanisme de transport du bus (Redis Pub/Sub au démarrage, migration possible vers un vrai broker
type RabbitMQ/Kafka si le volume d'événements le justifie plus tard) est un détail d'infrastructure — à
documenter séparément une fois le choix arrêté avec l'équipe Infra/DevOps.

---

## 5. Ce qui n'est volontairement pas encore dans ce contrat

- Authentification par mot de passe classique (aujourd'hui OTP uniquement) — à ajouter si un besoin
  back-office (juristes DiddiLegal, praticiens DiddiSanté) l'exige.
- Détail du mécanisme d'authentification service-à-service pour `GET /users/{user_id}` — à trancher avec
  l'équipe Infra selon le modèle réseau retenu (VPC interne, mTLS, clé API).
- Endpoints de gestion fine des rôles multiples (un utilisateur à la fois `driver` et `merchant`) — le
  modèle actuel suppose un rôle principal unique par utilisateur ; à revoir si le besoin apparaît.
- KYC documentaire (upload de pièce d'identité) — probablement un sous-module dédié plutôt qu'un champ de
  plus sur `users`, à spécifier séparément.

Si un module a besoin d'un de ces points plus tôt que prévu, mieux vaut l'ajouter ici proprement que de
l'improvvisation côté client.

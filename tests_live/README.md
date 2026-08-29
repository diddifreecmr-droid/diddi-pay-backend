# Suite live (boîte noire)

Teste un déploiement DiddiPay/DiddiFund déjà en ligne par HTTP — routes, auth, isolation entre
utilisateurs, validation — sans toucher à `payfund_app` ni à sa base. Séparée de `tests/` (qui
teste l'app en process) : elle ne tourne jamais avec `pytest` seul, il faut viser le dossier.

## Lancer

```bash
pip install -e ".[dev]"   # httpx / pytest / python-dotenv déjà en dépendances dev du projet
pytest tests_live/ -v
```

## Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `PAYFUND_BASE_URL` | `https://pay-api-staging.diddifree.com/payfund/v1` | API testée |
| `IDENTITY_BASE_URL` | `https://auth-staging.diddifree.com/identity/v1` | DiddiFreeID, pour créer/connecter les utilisateurs de test |
| `LIVE_ADMIN_ACCESS_TOKEN` | — | Absent = les tests admin (`ops/backfill`, isolation QR marchand) sont `skip`, pas en échec |
| `LIVE_TOKEN_CACHE_FILE` | `tests_live/.auth_cache.json` | Où sont mis en cache les tokens des utilisateurs de test (gitignored) |
| `LIVE_REQUEST_TIMEOUT` | `20` (secondes) | Timeout HTTP par requête |
| `LIVE_DEPOSIT_POLL_SECONDS` | `15` (secondes) | Combien de temps attendre qu'un dépôt passe `completed` avant de `skip` les chemins qui ont besoin de fonds |

## Le prompt OTP — normal, une seule fois par utilisateur

Au premier run, la suite inscrit deux utilisateurs de test (`user_a`, `user_b`) auprès de
DiddiFreeID, demande un OTP par e-mail, et **s'arrête pour te demander de coller le code** (lu
dans les logs staging). Une fois vérifié, les tokens sont mis en cache sur disque : les runs
suivants rafraîchissent le token silencieusement (`POST /auth/refresh`) sans nouveau prompt, sauf
si le refresh token a été révoqué entre-temps.

Pour un run non-interactif (CI) avec des tokens déjà émis, contourne totalement le flux OTP :

```bash
export LIVE_USER_A_ACCESS_TOKEN=...
export LIVE_USER_A_REFRESH_TOKEN=...
export LIVE_USER_A_PHONE=+225...      # requis pour les tests de transfert
export LIVE_USER_B_ACCESS_TOKEN=...
export LIVE_USER_B_REFRESH_TOKEN=...
export LIVE_USER_B_PHONE=+225...
```

## Ce qui est couvert, et ce qui est structurellement hors de portée d'une suite boîte noire

- **Toutes les routes du contrat**, avec et sans authentification (401 sans token / token invalide).
- **Validation** (422), **isolation entre utilisateurs** (jamais de fuite d'existence d'une
  ressource d'un tiers), **codes d'erreur exacts** du catalogue (`INSUFFICIENT_BALANCE`,
  `CAMPAIGN_NOT_ACTIVE`, `NOT_CAMPAIGN_OWNER`, etc.).
- **Dépôt/retrait/transfert/paiement marchand avec fonds réels** : dépend du
  `PAYMENT_GATEWAY_MODE` / `PAYMENT_GATEWAY_AUTOCONFIRM` de l'environnement testé, que cette suite
  ne contrôle pas. Si le dépôt reste `pending` après `LIVE_DEPOSIT_POLL_SECONDS`, les tests qui en
  dépendent se `skip` avec une raison explicite plutôt que d'échouer.
- **Activation de campagne (`draft -> active`) et décaissement de prêt (`pending -> disbursed`)** :
  aucune route HTTP publique ne les expose (back-office volontairement hors contrat). Les tests
  d'investissement et de remboursement vérifient donc le rejet correct (`CAMPAIGN_NOT_ACTIVE`,
  `LOAN_NOT_DISBURSED`) plutôt que le chemin heureux, qui n'est simplement pas atteignable de
  l'extérieur.

# DiddiPay - checklist GO / NO-GO production

Suivi Jira : `SCRUM-503`. Recette QA : `SCRUM-487`.

## Gates backend implementees

- [x] PaymentIntent/Payout authentifies par jeton de service DiddiFreeID, audience et scopes.
- [x] Compatibilite `X-Service-Key` controlable et interdite par la readiness production.
- [x] Retour checkout choisi par nom et resolu depuis une allowlist serveur.
- [x] Webhook Paystack signe, inbox idempotente et reconciliation en cas de webhook manque.
- [x] Evenement module cree aussi apres succes obtenu par reconciliation.
- [x] Outbox callback durable, retry borne, dead letter et reprise auditee via Backoffice.
- [x] Sous-ledger idempotent pour capture, frais, remboursement et settlement.
- [x] Backoffice/Pilotage proteges par scopes et manifestes de capacites versionnes.
- [x] Readiness production fail-closed pour sandbox, cle test et configuration dangereuse.
- [x] Worker de reconciliation/callback surveille par heartbeat Docker.
- [x] Metriques, alertes, traces, logs expurges et runbook d'incident disponibles.

## Preuves de cette cloture

- Compilation Python : pas d'erreur.
- OpenAPI : 59 chemins generes, dont PaymentIntent et les manifestes Admin/Pilotage.
- Suite Pytest Docker sur PostgreSQL dedie : 338 tests passes.
- Tests cibles des sprints production : passes.
- `docker compose config --quiet` : passe.
- Build Docker app/worker : passe.
- Migrations et demarrage Docker : passent.
- API et payment-worker : healthy, sans redemarrage.
- Un processeur historique non configure est isole pendant la reconciliation sans interrompre le
  cycle worker ; il reste pending pour traitement operateur.
- Ruff global : dette preexistante detectee dans plusieurs fichiers legacy/DiddiFund ; les fichiers
  modifies par ces sprints passent leurs controles cibles.

## Gates externes obligatoires avant GO

- [ ] Compte Paystack live/KYB valide et moyens de paiement XOF requis actives.
- [ ] Secrets `sk_live_*` injectes dans Portainer par un canal secret, jamais dans Git/Jira.
- [ ] Clients et scopes DiddiFreeID provisionnes pour chaque module et pour DiddiAdmin/Pilotage.
- [ ] Callbacks HTTPS de chaque module deployes et secrets HMAC distincts configures.
- [ ] Sauvegarde PostgreSQL produite et restauration testee dans un environnement isole.
- [ ] Stack staging redeployee avec migrations, API et worker tous healthy.
- [ ] Recette `SCRUM-487` passee: checkout, webhook, doublon, callback, retry et reconciliation.
- [ ] Rapprochement d'un paiement test entre Paystack, PaymentIntent, sous-ledger et module metier.
- [ ] Validation juridique/finance du perimetre live et procedure de gestion d'incident approuvee.
- [ ] Alertmanager teste vers un canal reel et astreinte/responsable nomme.

La decision est **NO-GO** tant qu'une case externe reste ouverte. Une API healthy seule ne suffit
pas a autoriser de l'argent reel.

## Commandes de validation sur une machine Docker disponible

```bash
docker compose config --quiet
docker compose build app payment-worker
docker compose up -d db redis app payment-worker
docker compose exec app alembic current
docker compose exec app python -m pytest -q
curl -fsS http://localhost:${APP_PORT:-48213}/payfund/v1/ready
docker inspect --format='{{.State.Health.Status}}' payfund-app
docker inspect --format='{{.State.Health.Status}}' diddipay-fund-payment-worker-1
```

L'equipe QA conserve les rapports et references de transactions dans `SCRUM-487`; elle ne colle
aucun token, secret ou payload contenant des donnees personnelles dans Jira.

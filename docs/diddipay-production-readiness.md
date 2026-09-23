# DiddiPay - readiness production

## Ce que le backend bloque automatiquement

Quand `DEPLOYMENT_ENVIRONMENT=production`, l'application refuse de démarrer et `/ready` devient
`degraded` si une condition critique est absente :

- `PAYMENT_PROCESSOR_MODE=paystack` et `PAYSTACK_ENVIRONMENT=live` ;
- clé secrète `sk_live_*` et endpoint officiel `https://api.paystack.co` ;
- fallback `X-Service-Key` désactivé ;
- `callback_url` libre désactivé ;
- au moins un `PAYMENT_SERVICE_CLIENT_IDS` autorisé ;
- auto-confirmation désactivée ;
- CORS explicite et secret QR remplacé ;
- retours checkout et callbacks métier externes en HTTPS.

La readiness ne renvoie jamais une clé ni un secret. Elle indique seulement
`{"status":"degraded","configuration":"unsafe"}`.

## Paramètres minimaux de production

```env
DEPLOYMENT_ENVIRONMENT=production
PAYMENT_PROCESSOR_MODE=paystack
PAYSTACK_ENVIRONMENT=live
PAYSTACK_SECRET_KEY=<secret Portainer, jamais Git>
PAYSTACK_BASE_URL=https://api.paystack.co
PAYMENT_SERVICE_CLIENT_IDS=diddigo-production,diddisend-production,diddifood-production
PAYMENT_SERVICE_KEY_FALLBACK_ENABLED=false
PAYMENT_CALLBACK_URL_FALLBACK_ENABLED=false
PAYMENT_GATEWAY_AUTOCONFIRM=false
CORS_ORIGINS=https://go.diddifree.com,https://send.diddifree.com
QR_SIGNING_SECRET=<secret aleatoire gere hors Git>
```

Configurer également `PAYMENT_CHECKOUT_RETURN_TARGETS` et `PAYMENT_CALLBACK_TARGETS` avec les
domaines de production exacts. Les secrets HMAC de callback sont distincts des jetons S2S.

## Dépendances externes avant GO

Le backend ne peut pas accomplir ces actions à la place des responsables concernés :

- Paystack valide le compte/KYB, active le mode live et fournit les secrets live ;
- l'équipe juridique/finance confirme que l'activité et les flux activés sont autorisés ;
- chaque module obtient son client de service et ses scopes auprès de DiddiFreeID ;
- chaque module déploie son callback signé avant son activation dans DiddiPay ;
- l'équipe Ops configure les secrets dans Portainer et teste sauvegarde/restauration ;
- l'équipe QA exécute la recette staging suivie dans `SCRUM-487`.

Tant qu'une de ces dépendances manque, la décision reste NO-GO même si les tests unitaires passent.

## Vérification opérateur

1. Déployer d'abord avec `DEPLOYMENT_ENVIRONMENT=staging` et les clés test.
2. Exécuter la recette complète: création, checkout, webhook, doublon, callback et réconciliation.
3. Préparer les variables live dans un canal secret, sans les écrire dans Git ou Jira.
4. Basculer vers `production`; le conteneur doit refuser toute configuration dangereuse.
5. Vérifier `/payfund/v1/ready`, les workers, métriques, alertes et la sauvegarde avant trafic.
6. Démarrer avec un périmètre et des montants limités, puis rapprocher Paystack et le sous-ledger.

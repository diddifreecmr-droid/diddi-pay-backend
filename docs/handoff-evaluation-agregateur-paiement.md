# Handoff — Évaluation et intégration d'un agrégateur de paiement

Document destiné à un agent dédié. Mandat unique : étant donné un agrégateur de
paiement (nom + clés API fournis par l'opérateur humain), le tester réellement,
documenter ce qui est vrai (pas ce que dit la doc marketing), et si demandé,
l'intégrer dans DiddiPay. Rien d'autre n'est dans ton mandat — ne touche pas aux
autres modules (diddi-go, diddi-send, diddi-food) sauf instruction explicite.

## 0. Avant de commencer

- Si les clés API ne sont pas déjà fournies, demande-les directement à
  l'opérateur. Ne les invente jamais, ne les devine jamais.
- Confirme explicitement si ce sont des clés de test/sandbox ou de production
  (préfixes `test_`/`live_`, environnement séparé, etc.) — si ambigu, demande.
  Par défaut, **n'utilise jamais une clé de production pour un appel de test**
  sans confirmation explicite que le compte n'est pas encore réellement actif.
- Ne commite jamais une clé API dans le dépôt, même dans un fichier de test ou
  un script temporaire. Utilise le répertoire scratchpad pour tout script
  d'exploration, ou passe les clés en variable d'environnement locale.

## 1. Méthodologie de test — vérifiée ce soir sur PawaPay, PayDunya, FeexPay

**Toujours tester en direct, ne jamais se contenter de la documentation.** Trois
agrégateurs testés ce soir avaient chacun au moins une divergence réelle entre
la doc et le comportement observé (PawaPay : sa doc suggérait l'absence de
remboursement, en réalité `/v2/refunds` fonctionne ; PayDunya : aucun mode
sandbox pour les décaissements, seule la doc le révèle en creusant).

Ordre recommandé :

1. **Lire la doc officielle** (WebFetch/WebSearch) pour une vue d'ensemble —
   mais la traiter comme une hypothèse à vérifier, pas un fait.
2. **Si la doc ne se charge pas correctement** (site JS/SPA qui ne rend rien via
   fetch — ça nous est arrivé avec FeexPay) : récupérer le SDK officiel de
   l'agrégateur (npm/pip/packagist/GitHub) et lire le code source directement.
   Les URLs d'API et noms de champs apparaissent en clair même dans du JS
   minifié — c'est comme ça qu'on a trouvé les vrais endpoints FeexPay quand
   leur doc ne répondait pas.
3. **Faire un premier appel inoffensif** (solde de compte, info shop/marchand)
   pour confirmer que les clés fonctionnent avant de tenter une vraie
   transaction.
4. **Tester un dépôt/collecte réel** : montant faible, suivre le cycle complet
   (initiation → statut intermédiaire → statut final), noter les noms de
   champs exacts de la requête et de la réponse.
5. **Déclencher volontairement un échec** (numéro de téléphone invalide,
   montant hors bornes) pour voir la vraie forme d'un échec — ne jamais
   supposer la forme d'un échec à partir du seul chemin de succès.
6. **Tester un payout réel** de la même façon (cycle complet + échec).
7. **Tester un remboursement si l'agrégateur en propose** — vérifier par un
   appel réel, pas en se fiant à la doc dans un sens ou dans l'autre.
8. **Vérifier la couverture Wave explicitement.** Aucun agrégateur testé ce
   soir n'a Wave activé par défaut sur le compte fourni (PawaPay : pas du tout
   actif sur le compte ; FeexPay et PayDunya : documenté comme supporté mais
   non vérifié en direct faute d'accès). Vérifier via l'équivalent de
   `/active-conf` ou la table pays/opérateur du SDK, ne pas supposer.
9. **Noter s'il existe une page de paiement hébergée (redirect) en plus de
   l'API directe.** Leçon importante de ce soir : une page hébergée qui
   retourne une vraie URL de redirection s'intègre immédiatement dans DiddiPay
   sans aucun changement côté applications clientes (DiddiGo/DiddiSend/
   DiddiFood savent déjà gérer `NextActionType.REDIRECT`, car c'est ce que
   Paystack utilise) — alors qu'une API de push direct (sans URL) exige que
   chaque application construise un nouvel écran. Le signaler clairement si
   une telle option existe.
10. **Vérifier le mécanisme de signature des webhooks** si documenté, mais
    noter explicitement si ce n'est pas vérifié en pratique (webhook jamais
    reçu, pas d'endpoint pour le tester) plutôt que de présenter une
    confiance non méritée.

## 2. Format du rapport attendu

Produire un compte rendu structuré, avec pour chaque capacité testée :
**testé en direct (oui/non)**, endpoint réel + méthode HTTP, champs exacts de
requête/réponse, mécanisme d'authentification, distinction sandbox/production,
liste des pays/réseaux confirmés, support Wave (oui/non/non vérifié), limites
connues, frais si découvrables. Un tableau comparatif avec les agrégateurs déjà
testés (PawaPay, PayDunya, FeexPay — voir historique de session si disponible,
sinon demander à l'opérateur) aide à situer le nouvel agrégateur.

## 3. Intégration dans DiddiPay — INTERDIT sans permission explicite

**Ne commence à écrire aucun code d'intégration (adaptateur, enregistrement dans
`deps.py`, migration, etc.) sans une permission explicite et sans ambiguïté de
l'opérateur humain, donnée après avoir vu ton rapport de test.** "Tester et
documenter" ne vaut pas autorisation d'implémenter. Si tu penses que
l'intégration est justifiée, dis-le dans ton rapport et attends une réponse
claire du type "oui, intègre-le" avant d'écrire une seule ligne de code
d'intégration.

Une fois cette permission explicite obtenue, le fichier de référence à
reproduire exactement est
`payfund_app/modules/payments/infra/pawapay_processor.py` — c'est l'implémentation
la plus complète et la plus récemment corrigée du contrat.

- Implémenter le protocole `PaymentProcessorPort`
  (`application/ports.py`) : `initialize_payment`, `verify_payment`,
  `create_payout`, `verify_payout`, `refund_payment`, `parse_webhook`.
- Déclarer `ProcessorCapabilities` avec précision (`currencies`, `directions`,
  `channels`, `networks`) — **ce n'est pas cosmétique, ça pilote le dispatch
  réel** dans `ProcessorRegistry`.
- **Piège trouvé et corrigé ce soir, à ne pas reproduire** : tout processeur
  simulé/factice doit être exclu du mécanisme de préférence "processeur
  dédié" (voir `SIMULATED_PROCESSOR_NAMES` dans `processor_router.py`). Un
  nouveau processeur ne doit jamais se retrouver sélectionné à la place d'un
  processeur réel simplement parce qu'il déclare un réseau que personne
  d'autre ne déclare.
- Enregistrer dans `presentation/deps.py::get_processor_registry()`, conditionné
  par une variable d'environnement pour la clé API, avec une priorité
  explicite si ce processeur doit concurrencer un processeur existant sur le
  même réseau.
- Écrire des tests sur le modèle de `tests/test_pawapay_processor.py` :
  réponses httpx mockées pour chaque statut, rejets de champs manquants
  **avant** tout appel HTTP, gestion des erreurs de transport.
- **Revérifier en direct contre le vrai sandbox**, pas seulement avec les
  tests mockés, avant de considérer l'intégration terminée — même rigueur que
  pour PawaPay/PayDunya ce soir.
- Mettre à jour la documentation si le comportement visible par les modules
  appelants change : `DiddiPay_Contrat_API.md` et/ou
  `docs/diddipay-payouts-contract-v1.md`. Ces documents sont la source de
  vérité pour les autres modules — rester rigoureusement exact, pas optimiste.
- La suite de tests complète (415+ tests au moment de la rédaction) doit
  passer avant de considérer un changement terminé. Conteneur Postgres
  jetable déjà établi pour les tests locaux (`postgres:16-alpine`, port
  `54329`).
- **Ne jamais redéployer via Portainer soi-même** — règle absolue de cet
  environnement. Pousser sur git, puis demander à l'opérateur de redéployer.

## 4. Hors périmètre

- Ne pas modifier les autres dépôts (diddi-go, diddi-send, diddi-food) sans
  instruction explicite.
- Ne jamais utiliser de clé de production sans confirmation explicite que
  c'est sûr.
- Ne pas committer de scripts de test ou de fichiers temporaires contenant des
  clés — utiliser le répertoire scratchpad.

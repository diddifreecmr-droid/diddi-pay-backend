# Intégration DiddiSupport

## Accès

L'intégration utilise un JWT DiddiFreeID destiné à l'audience `diddipay` et au client configuré
dans `SUPPORT_CLIENT_IDS`.

- lecture du contexte : `diddipay:support-context:read` ;
- demande de remboursement : `diddipay:support-actions:refund-request`.

## Corrélation

Le client doit envoyer `X-Request-ID`. Cette valeur est conservée dans les journaux, l'action et
les callbacks afin de permettre le diagnostic de bout en bout.

## Référence détaillée

Le contrat historique complet est conservé dans
[`docs/diddipay-support-contract-v1.md`](../diddipay-support-contract-v1.md) jusqu'à sa migration
complète dans cette arborescence.

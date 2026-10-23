# DiddiPay / DiddiFund

Documentation versionnée du backend de paiement DiddiFree.

## Sources de vérité

1. Le code et ses tests définissent le comportement réel.
2. `docs/api/openapi.json` est le snapshot généré du contrat HTTP FastAPI.
3. Les guides expliquent les parcours métier et les intégrations.

En cas de contradiction, le code et les tests gagnent. La contradiction doit ensuite être
corrigée dans la documentation et inscrite au changelog.

## Parcours principaux

- [Contrat API](api/API_CONTRACT.md)
- [Support et remboursements](guides/support-refunds.md)
- [Variables d'environnement](configuration/environment-variables.md)
- [Documentation DiddiSupport](integrations/diddisupport.md)

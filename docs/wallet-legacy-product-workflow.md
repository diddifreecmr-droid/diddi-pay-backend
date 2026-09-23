# Wallet historique et PaymentIntent - lecture produit

## Decision produit

Le wallet historique est en **legacy**. Il reste actif pour la compatibilite et pour certains flux
DiddiFund existants, mais aucun nouveau module ne doit construire son paiement dessus.

## Difference business

| Produit | Role |
|---|---|
| Wallet historique | Conserver une valeur interne DiddiFree et permettre recharge, solde, transfert, paiement interne et retrait. |
| PaymentIntent | Faire payer un service DiddiGo, DiddiSend, DiddiFood, DiddiFund ou futur module par un rail externe. |

En une phrase : **le wallet stocke et deplace une valeur interne ; PaymentIntent orchestre le
paiement d'un service.**

## Workflow wallet historique

```text
Utilisateur DiddiFreeID
        |
        v
Wallet personnel cree automatiquement
        |
        +--> recharge via un provider --> valeur disponible dans le wallet
        +--> transfert interne vers un autre wallet
        +--> paiement interne vers un compte marchand
        +--> retrait via un provider compatible
```

Le wallet est identifie par l'utilisateur DiddiFreeID. Le premier acces peut creer automatiquement
le compte s'il manque. Les operations sortantes utilisent un PIN et, selon le risque, une
verification renforcee.

## Workflow PaymentIntent

```text
Utilisateur choisit un service dans un module
        |
        v
Le backend du module cree un PaymentIntent DiddiPay
        |
        v
DiddiPay choisit Paystack aujourd'hui, un PSP direct demain
        |
        v
Webhook/reconciliation confirme le paiement
        |
        v
DiddiPay notifie le module, qui termine son objet metier
```

Le module reste proprietaire de la course, livraison, commande, investissement ou pret. DiddiPay
reste proprietaire du statut et de la fiabilite du paiement.

## Regle de choix

- Nouveau paiement DiddiGo, DiddiSend, DiddiFood ou futur module : **PaymentIntent**.
- Consultation ou operation sur une valeur deja detenue dans l'ancien wallet : **wallet legacy**.
- Flux DiddiFund encore raccorde au ledger wallet : conserver temporairement, puis migrer selon un
  plan explicite.
- Ne pas supprimer le wallet tant que les dependances DiddiFund et les soldes existants n'ont pas
  ete inventories et migres.

## Evolution cible

Si DiddiFree relance un produit wallet, il doit devenir un moyen de paiement distinct, par exemple
`DiddiWallet`, branche derriere PaymentIntent comme Paystack, Wave ou Orange Money. Le reste des
modules ne devra pas connaitre son ledger ni ses routes internes.

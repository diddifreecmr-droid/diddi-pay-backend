# Briefing frontend - Integration DiddiPay

**Version :** 3.0 - PaymentIntent

**Public :** equipes Flutter et web des modules DiddiFree

Ce brief explique le parcours d'interface. Les schemas exacts des routes, headers, requetes et
reponses sont visibles dans Swagger :

```text
https://<api-host>/payfund/v1/docs
```

Le contrat metier complet est dans `DiddiPay_Contrat_API.md`.

## 1. La regle la plus importante

Le frontend ne doit pas appeler directement les routes `/payment-intents`.

Ces routes exigent `X-Client-ID` et `X-Service-Key`. La cle service est un secret backend. Si elle
est integree dans une application Flutter, un bundle web ou un depot frontend, elle doit etre
consideree compromise.

Le parcours correct est :

```text
Frontend -> backend DiddiGo/DiddiFund -> DiddiPay -> Paystack
Frontend <- backend DiddiGo/DiddiFund <- DiddiPay <- webhook Paystack
```

Le frontend continue d'utiliser son JWT DiddiFreeID pour parler au backend de son module. C'est ce
backend qui controle les roles locaux, calcule le montant et appelle DiddiPay.

## 2. Ce que DiddiPay est devenu

DiddiPay n'est plus defini comme un simple wallet. Il orchestre un paiement quel que soit le rail :

- Paystack aujourd'hui ;
- Orange Money, Wave ou MTN MoMo en direct demain ;
- eventuellement DiddiWallet plus tard.

L'UI ne doit donc jamais afficher une logique codee en dur du type « si Paystack alors... ». Elle
recoit un `PaymentIntent` et execute son `next_action` normalise.

## 3. Contrat a demander au backend du module

Chaque module doit exposer au frontend des routes metier adaptees a son produit, par exemple :

```text
POST /rides/{ride_id}/payment
GET  /rides/{ride_id}/payment

POST /investments/{investment_id}/payment
GET  /investments/{investment_id}/payment
```

Le nom exact appartient a DiddiGo ou DiddiFund. La reponse frontend doit au minimum contenir :

```json
{
  "business_status": "awaiting_payment",
  "payment": {
    "id": "dcd7b1f8-7f28-4a88-a909-e0eae3fa7d84",
    "status": "requires_action",
    "amount": 5000,
    "currency": "XOF",
    "next_action": {
      "type": "redirect",
      "url": "https://checkout.paystack.com/example",
      "instructions": null,
      "expires_at": null
    }
  }
}
```

Le backend du module peut simplifier la reponse DiddiPay, mais il ne doit pas transformer un statut
incertain en succes ou en echec definitif.

## 4. Parcours de paiement frontend

1. L'utilisateur confirme la course, l'investissement ou la commande.
2. Le frontend appelle la route de paiement du module avec le JWT DiddiFreeID.
3. Le bouton passe immediatement en etat de chargement et empeche les doubles clics.
4. Le backend renvoie le paiement et son `next_action`.
5. Le frontend execute le `next_action`.
6. Au retour du navigateur, l'ecran affiche « Verification du paiement ».
7. Le frontend interroge le backend du module jusqu'a un etat stable ou jusqu'au delai UX.
8. Seul `succeeded` affiche le succes et debloque le service.
9. `failed` permet une nouvelle tentative explicite.
10. `processing` ou `unknown` reste en attente ; ne jamais creer automatiquement un second debit.

Le retour vers `callback_url` signifie seulement que l'utilisateur est revenu du checkout. Il ne
prouve pas que l'argent a ete recu.

## 5. Gestion de next_action

| `type` | Comportement UI |
|---|---|
| `redirect` | Ouvrir `url` dans un navigateur securise ; revenir sur l'ecran de verification |
| `mobile_money_prompt` | Demander de confirmer la notification sur le telephone |
| `display_instructions` | Afficher le texte de `instructions` sans l'interpreter |
| `await_confirmation` | Afficher une attente avec actualisation du statut |
| `none` | Ne rien ouvrir ; consulter le statut |

Toujours prevoir qu'un futur provider retourne un autre type d'action que Paystack. Les types
inconnus doivent produire un message neutre et journalisable, pas un crash de l'application.

## 6. Affichage des statuts

| Statut DiddiPay | Texte conseille | Action UI |
|---|---|---|
| `requires_action` | Paiement a confirmer | Executer ou reproposer l'action |
| `processing` | Paiement en cours de verification | Attendre et rafraichir |
| `succeeded` | Paiement confirme | Afficher le recu / continuer |
| `failed` | Paiement non abouti | Afficher la raison generique et proposer de reessayer |
| `cancelled` | Paiement annule | Retour au choix de paiement |
| `partially_refunded` | Remboursement partiel effectue | Afficher le montant rembourse |
| `refunded` | Paiement rembourse | Afficher l'etat final |

Une tentative `unknown` veut dire « resultat pas encore determine ». Le texte utilisateur conseille
est : « Nous verifions votre paiement. Ne relancez pas l'operation pour le moment. »

## 7. Polling raisonnable pour le MVP

Les notifications backend sont asynchrones. Pour offrir un retour rapide apres le checkout, le
frontend peut interroger la route de statut du module :

- toutes les 2 secondes pendant les 10 premieres secondes ;
- puis toutes les 5 secondes jusqu'a 60 secondes ;
- ensuite afficher un etat en attente avec un bouton « Actualiser ».

Arreter le polling lorsque l'ecran est ferme, lorsque l'application passe en arriere-plan ou quand
le statut devient final. Le backend du module reste responsable de recevoir l'evenement DiddiPay,
meme si le frontend est ferme.

## 8. Idempotence et doubles clics

Le frontend desactive le bouton pendant la requete, mais cela ne remplace pas l'idempotence backend.

Si la connexion coupe apres un clic :

- le frontend redemande d'abord l'etat de l'objet metier ;
- le backend reutilise sa cle d'idempotence pour la meme operation ;
- le frontend ne fabrique pas directement une cle pour appeler DiddiPay ;
- aucun message « echec » ne doit etre affiche uniquement parce que la requete a expire.

## 9. Donnees et securite UI

- Ne jamais collecter ni stocker les donnees de carte dans l'application DiddiFree.
- Le paiement carte reste sur la page securisee du processeur.
- Ne jamais logger une URL complete si elle peut contenir un jeton temporaire.
- Ne jamais afficher `X-Service-Key`, une cle Paystack ou un payload webhook.
- Ne placer aucune donnee sensible dans `metadata`.
- Le montant et le beneficiaire affiches viennent du backend du module, pas d'une valeur locale
  modifiable.
- Utiliser le formatage XOF sans decimales, mais conserver `amount` comme entier dans les modeles.

## 10. Gestion des erreurs

Toutes les erreurs backend suivent normalement cette forme :

```json
{
  "error": {
    "code": "PAYMENT_OPERATION_CONFLICT",
    "message": "Message lisible",
    "details": null
  }
}
```

Le frontend doit piloter le comportement par `error.code`, pas en analysant `message`. Le message
peut etre affiche ou remplace par une traduction UX.

Cas importants :

| Code / situation | Comportement frontend |
|---|---|
| `IDEMPOTENCY_CONFLICT` | Rafraichir l'etat ; ne pas retenter avec des donnees differentes |
| `PAYMENT_METHOD_UNAVAILABLE` | Revenir au choix de moyen de paiement |
| `PAYMENT_INTENT_NOT_FOUND` | Rafraichir l'objet metier ou contacter le support |
| timeout reseau | Statut inconnu ; relire avant toute nouvelle tentative |
| `401` du module | Rafraichir le token DiddiFreeID selon le contrat auth |

## 11. Paystack aujourd'hui, PSP directs demain

Dans le MVP, `next_action.type` sera le plus souvent `redirect` vers le checkout Paystack. Le compte
marchand Paystack decide quels canaux sont reellement proposes dans le pays et l'environnement.

L'UI peut proposer « Mobile Money » ou « Carte » selon les capacites retournees par son backend,
mais elle ne doit pas promettre Orange Money, Wave ou MTN si le backend ne les annonce pas comme
actifs. La liste des moyens disponibles doit devenir une configuration/capability backend, pas une
liste codee en dur dans Flutter.

## 12. Wallet et anciennes interfaces

Les ecrans historiques `/wallet/*` peuvent rester presents pendant la migration. Ils concernent le
wallet legacy et ses PIN, transferts P2P, depots ou retraits.

Pour toute nouvelle fonctionnalite DiddiGo, DiddiFund ou futur module :

- utiliser le parcours `PaymentIntent` via le backend du module ;
- ne pas exiger la creation d'un wallet pour payer avec Paystack ;
- ne pas confondre le PIN wallet avec l'autorisation d'un paiement externe ;
- traiter un futur DiddiWallet comme un moyen de paiement parmi d'autres.

## 13. Checklist avant livraison frontend

- Aucun secret service ou Paystack dans le code frontend.
- Double clic bloque et reprise reseau testee.
- Tous les statuts DiddiPay ont un rendu.
- Tous les types de `next_action` ont un fallback.
- Le retour checkout affiche une verification, jamais un succes immediat.
- Le polling s'arrete correctement.
- Un paiement `unknown` ne declenche pas un second debit.
- Le montant XOF et la reference metier sont affiches avant confirmation.
- Le parcours fonctionne quand l'application est fermee pendant le webhook.
- Les anciens ecrans wallet sont clairement separes du nouveau paiement module.

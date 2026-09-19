# Alignement et recette S2S des modules Diddi

Statut : document d'alignement pour les equipes backend et QA. Il decrit le standard cible
valide dans Jira SCRUM-415 et distingue ce standard du code actuellement disponible.
Il ne modifie pas les contrats HTTP publics des modules.

## 1. Decision canonique et etat actuel

Pour tout **nouvel** appel backend vers backend, le client obtient un JWT court de
DiddiFreeID avec `client_credentials`, puis envoie :

```http
Authorization: Bearer <service_jwt>
X-Client-ID: <client_id>
X-Request-ID: <identifiant-de-correlation>
```

Le receveur valide localement via JWKS : algorithme RS256, signature, `kid`,
`iss=diddifree-id`, `aud` egal a son identifiant, `exp`, `iat`,
`token_type=service`, `role=service`, `status=active`, `sub=service:<service>`,
`client_id` identique a `X-Client-ID`, puis le `scope` requis par l'operation.
Il ne transforme pas un service token en utilisateur local. `401` signifie absence,
expiration ou invalidite du token ; `403` signifie service authentifie mais droit
insuffisant. Le receveur n'appelle pas DiddiFreeID a chaque requete pour verifier
la signature.

Source de cette decision : commentaire de l'ADR Jira SCRUM-415. Le ticket transverse
SCRUM-405 et `S2S_Integration_Brief.md` decrivent `X-Service-Key` comme protocole ;
ce format reste **legacy**, pas la cible des nouvelles integrations. Les equipes
doivent aligner SCRUM-405 et les briefs avant d'annoncer une migration terminee.

Etat du depot DiddiPay au 19/09/2026 : `/payfund/v1/payment-intents` accepte
`X-Client-ID` + `X-Service-Key` et **n'accepte pas encore** le JWT de service.
Le callback DiddiPay utilise un secret HMAC distinct. `DiddiFund` partage encore
le processus DiddiPay pour certains appels internes ; un appel Python interne
n'est pas un appel S2S HTTP. Conserver ces chemins pendant la migration, sans
leur attribuer les garanties du futur verificateur JWT.

## 2. Emission par DiddiFreeID

Contrat publie : `POST /identity/v1/auth/service/token`, avec corps
`application/x-www-form-urlencoded` :

```text
grant_type=client_credentials
client_id=<client-id-provisionne>
client_secret=<secret-du-client>
audience=<service-cible>
scope=<scopes-demandes-separes-par-des-espaces>
```

Le `client_id` est aussi transmis dans `X-Client-ID`. Un client est provisionne
pour une audience et une allowlist de scopes. Exemple deja documente :
`pilotage-staging-diddigo` avec `aud=diddigo` et `scope=ride-summary:read`.
Le contrat actuel indique une duree par defaut de 600 secondes, sans refresh
token. L'appelant garde le token en memoire et en demande un nouveau avant
expiration. Les secrets et tokens restent dans les backends et le gestionnaire
de secrets, jamais dans le frontend, Git ou les logs.

Le client ID, l'audience et les scopes de **chaque autre paire de services**
doivent etre definis et provisionnes. Le fait que DiddiFreeID sache emettre un
token pour DiddiGo ne signifie pas qu'il existe deja un client pour DiddiPay,
DiddiSend ou DiddiFiles. Les environnements staging et production ont des
clients et secrets distincts. La revocation d'un client bloque l'emission de
nouveaux tokens ; un token deja emis reste utilisable jusqu'a son expiration
(au plus 600 secondes selon l'ADR actuel).

## 3. Patrons d'appel a couvrir

| Situation | Identite du requérant | Controle complementaire du receveur |
|---|---|---|
| Backend A lit une donnee du backend B | JWT service `aud=B`, scope de lecture | Filtrage des donnees et audit du client |
| Backend A cree ou modifie un objet chez B | JWT service `aud=B`, scope d'ecriture | Regle metier, `Idempotency-Key` stable si rejouable |
| Backend A agit pour un utilisateur | JWT service de A ; contexte utilisateur explicite | Droits du service **et** legitimite du contexte utilisateur |
| Frontend appelle un module | JWT utilisateur DiddiFreeID | Roles et droits metier locaux du module ; pas de service token |
| PSP externe appelle DiddiPay | Signature du webhook PSP | Signature, reference, montant, devise, inbox dedupliquee |
| DiddiPay notifie un module | Signature HMAC du callback | Corps brut, event ID, paiement attendu, deduplication |
| Module consomme un evenement interne | Identite du consumer et ACL du bus | Inbox/idempotence, reprise des messages non acquittes |
| Deux composants d'un meme processus s'appellent | Pas de HTTP S2S | Frontieres applicatives et permissions internes |

Pour une mutation rejouable, le service appelant conserve `Idempotency-Key` et
le meme payload apres un timeout. Le receveur stocke durablement le resultat
par client + cle et refuse la meme cle avec un autre payload. Pour les callbacks,
la cle d'idempotence HTTP ne remplace pas l'ID d'evenement unique.

Si un appel represente une personne, ne jamais copier son JWT dans le role du
service ni faire confiance a un simple `X-User-ID` non controle. Le contrat de
la route doit definir d'ou vient ce contexte, quels champs le service est
autorise a affirmer, comment le receveur les confronte a son objet metier et
ce qu'il consigne dans l'audit. Exemple : DiddiFood peut transmettre
`customer_user_id` d'une commande a DiddiSend, mais Send doit limiter cette
assertion au service Food autorise et garder distincts acheteur, expediteur
technique et destinataire.

## 4. Carte des integrations a recetter

| Appel ou flux | Situation actuelle connue | Cible et proprietaire de la recette |
|---|---|---|
| Pilotage -> DiddiFreeID | Client credentials `aud=diddifree-id` documente | Scopes profil/backfill selon routes ; equipes ID + Pilotage |
| Pilotage -> DiddiGo | Client `aud=diddigo` documente ; verificateur Jira en cours | `ride-summary:read` seulement ; equipes Go + Pilotage |
| DiddiGo -> DiddiPay | Cle statique PaymentIntent | Nouveau client `aud=diddipay`, scopes paiement a definir ; Go + Pay + ID |
| DiddiSend -> DiddiPay | A auditer par route | Meme schema, scopes propres a Send ; Send + Pay + ID |
| DiddiFood -> DiddiPay | A auditer par route | Meme schema, scopes propres a Food ; Food + Pay + ID |
| DiddiFood -> DiddiSend | Migration Jira SCRUM-414 en cours | Token scoped, contexte acheteur et idempotence ; Food + Send + ID |
| DiddiGo/Send/Pay -> DiddiFiles | A inventorier | Scopes fichier et droits sur l'objet ; equipe Files + appelants + ID |
| DiddiPay <-> DiddiFund | Certains appels restent internes au monolithe | JWT seulement si separation en services HTTP ; Pay + Fund |
| DiddiAdmin -> modules | A inventorier | Scopes admin explicites, distincts des roles humains ; Admin + receveur |
| Paystack/Wave/Orange -> DiddiPay | Integrations PSP, pas clientes DiddiFreeID | Contrat de signature/authentification du PSP, jamais service token DiddiFreeID |

Cette carte n'affirme pas que toutes ces routes existent deja. Chaque equipe
receveuse doit publier ses routes, ses scopes, ses donnees accessibles et ses
regles d'autorisation. Ne pas inventer un scope universel `admin` ou une cle
globale partagee entre modules.

## 5. Recette commune pour chaque paire appelant -> receveur

Preparatifs : client de staging distinct, audience du receveur, scope minimal,
secret client stocke chez DiddiFreeID et chez l'appelant, endpoint de test non financier,
horloges synchronisees, JWKS disponible et correlation `X-Request-ID`.

| Cas | Attendu |
|---|---|
| Token valide, audience et scope corrects | Succes sur la route autorisee ; audit `auth_type=service`, `client_id`, `request_id` |
| Aucun token, signature fausse, expire, issuer faux ou token utilisateur | `401` ; aucun effet metier |
| Token pour un autre `aud` | `401` ; aucun effet metier |
| `X-Client-ID` absent ou different du claim `client_id` | `401` ; aucun effet metier |
| Service authentifie mais scope ou route interdit | `403` ; aucun effet metier |
| Client staging sur production, ou inversement | Refus ; secrets et audiences separes |
| Rotation `kid` du signataire | Ancien et nouveau token valides selon fenetre prevue ; JWKS rafraichi |
| Revocation du client | Plus de nouveau token ; token deja emis expire dans la fenetre annoncee |
| Retry identique d'une mutation | Un seul objet ou effet ; meme resultat metier |
| Meme cle avec payload divergent | Conflit, aucun second effet |
| Timeout apres effet externe incertain | Reprise avec meme cle ; pas de double paiement/livraison |
| Contexte utilisateur forge ou non autorise | Refus ; aucun droit herite du seul service token |
| Logs et traces | `request_id`, service, route, decision ; aucun JWT, secret ou PII sensible |

Cas additionnels pour un callback : signature absente/fausse, ID header different
du corps, reference inconnue, montant ou devise incorrects, doublon, livraison
retardee, retry, dead letter et relecture du statut source. Verifier l'inbox et
la transition metier dans la meme transaction. Ne jamais inferer le succes d'un
paiement a partir d'une redirection navigateur ou d'un simple `202`.

Cas additionnels pour le bus d'evenements : consumer arrete lors de la
publication, message non acquitte, redelivery, traitement idempotent, reprise
des pending et backfill si la retention est depassee. Le bus n'utilise pas les
headers HTTP du S2S.

Pour chaque paire, conserver les preuves de recette sans secrets : IDs de
requete/evenement, code HTTP, statut metier, nombre d'effets, logs expurges,
version des deux services et environnement. Faire une recette Docker locale,
puis staging interservices avant le basculement de production.

## 6. Migration et criteres de sortie

1. Inventorier les routes et les appels reels. Associer a chacun un proprietaire,
   une audience, un scope et le mecanisme actuel.
2. Implementer le verificateur JWT de service distinct du decodeur de JWT
   utilisateur dans chaque receveur. Garder le controle de `client_id` et des
   droits de route au niveau presentation/application, sans coupler le domaine
   metier a DiddiFreeID.
3. Deployer le receveur capable d'accepter l'ancien et le nouveau mecanisme sur
   les seules routes en migration, avec metriques separees. Deployer ensuite
   l'appelant JWT. Retirer enfin le legacy apres recette et mesure d'usage nul.
4. Pour DiddiPay, garder `PAYMENT_SERVICE_KEYS` actif tant que DiddiGo/Fund et
   les autres appelants ne sont pas migres et valides. Les secrets de callback
   `PAYMENT_CALLBACK_TARGETS` restent distincts ; leur migration eventuelle
   doit etre une decision specifique, pas un effet de bord du nouveau JWT.
5. Fermer les tickets seulement apres preuve de recette sur les deux services,
   en staging, avec tests d'echec et de reprise. Une documentation DiddiFreeID
   indiquant qu'un endpoint existe ne prouve pas que chaque module le consomme.

Points de suivi Jira : SCRUM-415 (ADR canonique), SCRUM-405 (alignement transverse),
SCRUM-416 a SCRUM-423 (Pilotage/ID/Go) et SCRUM-414 (Food/Send).
References locales : `S2S_Integration_Brief.md` pour l'etat actuel DiddiPay,
`DiddiPay_Contrat_API.md` pour les paiements, `DiddiPay_Backend_Integration_Brief.md`
pour les callbacks. Contrat emetteur fourni par l'equipe ID :
`SERVICE_TOKEN_CONTRACT.md`.

# Mémoire du voyage (M3)

> Le modèle écrit lui-même ce qui a été **décidé** (destination, dates, budget, dépenses, lieux retenus ou refusés) dans une base SQLite, en appelant des outils. La mémoire survit à une nouvelle conversation : « où en est mon voyage ? » retrouve tout.

## Pourquoi cette feature

- **Un assistant de voyage doit retenir ce qui est décidé**, pas seulement ce qui a été dit. Les user stories « Décisions retenues » de M3 ([USER_STORIES.md](../USER_STORIES.md)) : retrouver ses décisions dans une nouvelle conversation, sans les répéter ni relancer l'ancienne.
- **« Combien il me reste ? » ne doit pas être une addition du modèle.** C'est le sujet des user stories « Budget » : le reste du budget vient d'un `SUM` SQL. Un modèle local calcule mal, et le catalogue de features (Notion) le dit autrement : on ne calcule pas un budget restant en relisant du texte.
- **La différence avec la persistance de session ([sessions et profils](sessions-et-profils.md))** : celle-ci relit un historique ; M3 stocke des **faits requêtables que l'agent crée et met à jour de sa propre initiative** (CRUD piloté par le modèle). Si l'agent ne faisait que relire un JSON, ce serait M1.
- **Le piège identifié** : laisser le modèle écrire du texte libre dans la base donne des doublons approximatifs, et l'agent ne sait plus relire sa base. D'où le **schéma strict et la validation avant écriture**, et le lien avec la sortie structurée ([T5](sortie-structuree.md)) : contraindre la forme des écritures.
- **Pourquoi SQLite** : le catalogue l'autorise explicitement (`sqlite3` fait partie de la bibliothèque standard), et c'est un fichier unique, interrogeable hors du modèle.
- **Pourquoi SQLite plutôt qu'un JSON plat** (d'après le code ; le choix inverse est celui de l'historique, voir [sessions et profils](sessions-et-profils.md)) : un historique se relit en entier et dans l'ordre, alors que la mémoire du voyage se **filtre et s'additionne** (`SUM` du budget, lieux retenus ou refusés). La base apporte en plus ce qu'un JSON n'a pas : des contraintes (`CHECK`, `UNIQUE`, `FOREIGN KEY`) qui refusent une écriture mal formée, et des transactions qui annulent une écriture faite à moitié.
- **M3 contre M4** : M3 garde les **données structurées et requêtables** du voyage, M4 ([document d'itinéraire](document-itineraire.md)) le **document que le voyageur lit**. On ne calcule pas un budget restant en relisant du texte, et on ne donne pas une table SQL à lire à un voyageur. Les deux se complètent : « J'ai réservé le vol, 120 € » crée une dépense ici et une ligne dans la section Transport du document. Les deux vivent dans la même base (`data/trips.db`).

## Ce que ça fait

Exemple : après « je pars à Rome du 12 au 19 mars avec 1 500 € », le voyage est dans `data/trips.db`. Dans une nouvelle conversation, lancée sans `--continue`, « où en est mon voyage ? » retrouve destination, dates et budget. Un lieu refusé est enregistré avec sa raison et n'est pas reproposé. Une date qui change met à jour le voyage au lieu d'en créer un second.

### Les outils

| Outil | Paramètres | Rôle |
|---|---|---|
| `trip_save` | `destination`, `start_date`, `end_date`, `budget`, `currency`, `travelers`, `new_trip` | crée ou met à jour le voyage en cours ; `new_trip=true` en ouvre un autre ; une autre destination est refusée si le voyage a déjà des dépenses, des lieux ou un itinéraire |
| `trip_get` | aucun | renvoie tout : voyage, dépenses, lieux retenus et refusés, budget |
| `expense_save` | `label`, `amount`, `category`, `status`, `currency`, `distinct` | ajoute une dépense, ou met à jour celle qui a la même catégorie et le même libellé |
| `expense_delete` | `label`, `category` | supprime une dépense (la catégorie départage deux libellés identiques) |
| `place_save` | `name`, `decision`, `kind`, `reason` | note un lieu comme retenu (`kept`) ou refusé (`rejected`) ; la dernière décision l'emporte |
| `budget_summary` | aucun | budget, dépenses réservées ou payées, dépenses prévues, reste : calculés par SQL |

| Champ | Valeurs acceptées |
|---|---|
| `category` (dépense) | `transport`, `lodging`, `food`, `activity`, `other` |
| `status` (dépense) | `planned` (estimation, valeur par défaut), `booked` (réservé), `paid` (payé) |
| `kind` (lieu) | `activity`, `restaurant`, `lodging`, `sight`, `other` |
| `decision` (lieu) | `kept`, `rejected` |

## Comment ça marche

Les outils sont des fonctions du serveur MCP ([MCP](mcp.md)), déclarées dans [config/tools.yaml](../config/tools.yaml). Le prompt système dit au modèle quand les appeler : dès que l'information est donnée, sans demander la permission, et `budget_summary` pour toute question d'argent.

### La base

- Fichier : `data/trips.db`, réglé par `trip_db` dans `config/config.json`. Mettre `null` désactive la mémoire du voyage. Le dossier `data/` est ignoré par git : ce sont des données personnelles.
- Créée au premier appel, avec un numéro de version de schéma (`PRAGMA user_version`, 3 aujourd'hui : les versions 2 et 3 ont ajouté les tables du document d'itinéraire, M4). Trois tables pour M3 : `trips`, `expenses`, `places`. Une base d'une ancienne version est migrée sur place sans perte ; une base d'une autre version, ou un fichier SQLite qui n'est pas le nôtre, n'est jamais modifié : l'outil répond par une erreur.
- Un seul voyage est « en cours » à la fois, et les outils agissent dessus. Avec `new_trip=true`, le voyage précédent est conservé dans la base mais n'est plus le voyage en cours.

### Le schéma

| Table | Colonnes | Ce que la base impose |
|---|---|---|
| `trips` | `id`, `destination`, `start_date`, `end_date`, `budget_cents`, `currency`, `travelers`, `active`, `updated_at` | dates au format `YYYY-MM-DD`, fin postérieure ou égale au début, budget strictement positif, devise de 3 lettres majuscules, 1 à 50 voyageurs, **un seul voyage actif** (index unique partiel `one_active_trip`) |
| `expenses` | `id`, `trip_id`, `category`, `label`, `label_key`, `amount_cents`, `status` | `trip_id` référence `trips` (suppression en cascade), `category` et `status` limités aux valeurs du tableau plus haut, montant strictement positif, **`UNIQUE (trip_id, category, label_key)`** : pas de doublon |
| `places` | `id`, `trip_id`, `name`, `name_key`, `kind`, `decision`, `reason` | `trip_id` référence `trips` (cascade), `kind` et `decision` limités aux valeurs du tableau, raison de 300 caractères au plus, **`UNIQUE (trip_id, name_key)`** : un lieu = une ligne |

`label_key` et `name_key` sont le libellé normalisé (sans accents, en minuscules, ponctuation ramenée à un espace) : c'est elle qui détecte qu'« Hôtel Colosseo » et « hotel colosseo » sont le même élément, tandis que `label` et `name` gardent l'orthographe d'origine. Le schéma complet est dans [src/tools/trip_db.py](../src/tools/trip_db.py), source unique des listes de valeurs.

### Montants et budget

- **Montants en centimes entiers.** En flottant, `0.1 + 0.2` vaut `0.30000000000000004`. Un montant doit être strictement positif, avec deux décimales au plus.
- **Une seule devise par voyage** (code à 3 lettres, par exemple `EUR`), sans conversion. Elle ne peut plus changer dès qu'une dépense est enregistrée ; il faut alors ouvrir un autre voyage.
- **Seules les dépenses `booked` et `paid` consomment le budget.** `planned` n'est qu'une estimation.
- `budget_summary` renvoie `budget`, `committed` (réservé ou payé), `planned`, `remaining` (budget moins réservé ou payé), `remaining_after_planned` et le détail `by_category`. Un champ `warnings` signale un budget dépassé, et un champ `note` l'absence de budget.
- **Le chiffre du dernier résultat d'écriture est toujours à jour.** `expense_save`, `expense_delete` et `trip_get` renvoient le même résumé dans un champ `budget`, et `trip_save` aussi quand le budget ou la devise change. Même si le modèle reprend ce chiffre au lieu d'appeler `budget_summary`, il vient du `SUM` SQL et n'est jamais périmé. Le prompt demande tout de même d'appeler `budget_summary` à chaque question d'argent, pour que l'appel soit visible : le modèle le fait presque toujours, sauf juste après l'écriture qui vient de lui renvoyer le chiffre (voir l'essai).

### Validation avant écriture

Trois niveaux, du plus tôt au dernier filet :

1. `config/tools.yaml` : types et valeurs autorisées (`enum`).
2. Contrôles Python dans `src/tools/trip_memory.py` : dates réelles au format `YYYY-MM-DD`, dates de voyage qui ne sont pas dans le passé, fin postérieure ou égale au début, montant valide, devise valide, catégories connues.
3. Contraintes SQL : `CHECK`, `UNIQUE` et `FOREIGN KEY`.

Un refus renvoie `{"error", "tool", "hint"}` au modèle, qui peut se corriger. Rien n'est écrit.

### Contradictions

- **Une date qui change** met à jour le voyage. Le retour contient `changes`, avec l'ancienne et la nouvelle valeur (`from`, `to`) de chaque champ modifié.
- **La même dépense redite** met à jour son montant (et son statut) sans créer de doublon. Elle est identifiée par sa catégorie et son libellé normalisé (sans accents, majuscules ni ponctuation).
- **Un libellé proche mais reformulé** est refusé avec un indice : reprendre le libellé exact de la dépense existante, ou confirmer qu'il s'agit d'une autre dépense avec `distinct=true`. L'alerte se déclenche quand tous les mots du libellé le plus court (2 mots au minimum) figurent dans l'autre.
- **Un lieu retenu puis refusé** reste une seule ligne : la dernière décision l'emporte.
- **Un budget abaissé sous le montant déjà engagé** est accepté, avec un `warning`.
- **Une autre destination sur un voyage qui a déjà du contenu** (dépenses, lieux ou itinéraire) est **refusée** : changer le nom laisserait ces éléments sous la mauvaise destination. L'indice demande `new_trip=true` si l'utilisateur change vraiment de destination, ce qui conserve l'ancien voyage. Sur un voyage encore vide, la destination se remplace, et une simple différence de casse ou d'accents (« ROME ») n'est pas un changement.

### Lieux refusés dans les recherches d'activités

`search_activities` lit les lieux refusés du voyage en cours ([src/tools/trip_memory.py](../src/tools/trip_memory.py), `rejected_places`). Dans ses résultats, il **retire les pages dont le titre contient un lieu refusé**, **ainsi que les phrases du contenu qui le citent** (mots entiers, sans tenir compte des accents ni de la casse ; une page dont il ne reste plus rien disparaît), et ajoute en tête une note : « L'utilisateur a refusé : Vatican. Ne propose pas ces lieux, même sous un autre nom. » Le prompt demande en plus au modèle de ne pas reproposer un lieu refusé et de tenir compte de cette note.

- Le filtre porte sur le titre et sur les phrases du contenu. Les phrases sont coupées sur `.`, `!`, `?` et ` · ` ; le reste de la page est conservé.
- Sans base, sans voyage en cours ou avec la mémoire désactivée, la recherche est inchangée, et elle ne crée pas la base.
- Seul `search_activities` est concerné : ni `web_search_basic`, ni `web_search_deep`, ni `search_housing` ne regardent les lieux refusés.

### Observer la base sans le modèle

```bash
python -c "import sqlite3; [print(tuple(r)) for r in sqlite3.connect('data/trips.db').execute('SELECT * FROM expenses')]"
```

Remplacer `expenses` par `trips` ou `places` pour les autres tables.

### Repartir de zéro : `/clear`

Dans la CLI, `/clear` efface toute la base du voyage : voyages (anciens compris), dépenses, lieux, document d'itinéraire et historique de ses éditions. Une confirmation est demandée (`o`, `oui`, `y` ou `yes` ; toute autre réponse, ou `Ctrl+D`, annule). Le schéma de la base reste en place, et un nouveau voyage peut être créé tout de suite.

- C'est irréversible : le fichier `data/trips.db` est vidé, pas archivé.
- La conversation n'est pas touchée : si elle parle encore de l'ancien voyage, le modèle peut le réenregistrer. `/clear` le rappelle, et `/new` repart d'une conversation vierge.
- Ce n'est pas un outil du modèle : il n'est ni dans `config/tools.yaml` ni dans le serveur MCP, seul l'utilisateur peut déclencher l'effacement.
- Rien n'est créé si la base n'existe pas encore, et un `trip_db` à `null` est signalé comme pour `/doc`.
- L'interface web a son équivalent : le bouton **Effacer le voyage** du panneau Document demande confirmation, puis efface la base (`DELETE /api/trip`) ([interface web](interface-web.md)).

## Les questions du jury

Les questions que le catalogue de features (Notion) prévoit pour M3, et où trouver la réponse.

- **Quel est votre schéma ?** → « Le schéma » : trois tables, des contraintes qui refusent une écriture mal formée, un seul voyage actif.
- **Comment gérez-vous deux faits contradictoires ?** → « Contradictions » : une date ou un montant qui change **met à jour** la ligne au lieu d'en créer une seconde, et la réponse garde l'ancienne et la nouvelle valeur (`changes`) ; un libellé reformulé est **refusé avec un indice** ; un budget abaissé sous le montant engagé est accepté avec un avertissement.
- **L'agent décide-t-il seul d'écrire ?** → Oui. Le prompt système ([config/prompts.yaml](../config/prompts.yaml)) lui demande d'appeler les outils dès que l'information est donnée, sans demander la permission, et le code ne l'y force pas. La seule action que l'utilisateur déclenche lui-même est `/clear`. Dans l'essai avec le vrai modèle, il a créé le voyage, les dépenses et les lieux de lui-même, 4 fois sur 4 ; les tests, eux, ne couvrent pas son comportement (voir les limites).
- **Pourquoi M3 et M4 ?** → « Pourquoi cette feature » : des données requêtables d'un côté, un document lisible de l'autre.

## Ce qui a été écarté

- **Du texte libre dans la base** : sans schéma, des doublons approximatifs, et l'agent ne sait plus relire sa base. D'où les valeurs fermées et la validation avant écriture.
- **Des montants flottants** : `0.1 + 0.2` ne vaut pas `0.3`. Les montants sont des centimes entiers.
- **La fusion automatique de deux libellés proches** : le code n'alerte que, et laisse le modèle reprendre le libellé exact ou confirmer avec `distinct=true`.
- **Un JSON plat pour la mémoire du voyage** : voir « Pourquoi cette feature ».
- **La conversion de devises** : une seule devise par voyage.

## Essai avec un vrai modèle

Les tests unitaires n'utilisent pas de modèle. Les 31/10 et 1/11/2026, la feature a été essayée avec `qwen3:8b` (température 0,7) dans une copie isolée du dépôt (sessions, journaux et base du voyage hors de `data/trips.db`), avec les vraies clés d'API : les recherches de vols, d'hébergement et d'activités fonctionnent. Chaque essai enchaîne quatre conversations lancées sans `--continue`, puis ouvre la base avec `sqlite3` après chacune :

- **A, 7 messages** : « Je pars à Rome du 12 au 19 mars 2027 avec 1500 € de budget, on est 2. Note-le sans rien chercher », un vol réservé (120 €), un hôtel réservé (300 €), « Combien il me reste ? », « Finalement je pars du 14 au 21 mars 2027, sans rien chercher », « Le vol coûte finalement 135 euros », « Le Colisée me plaît mais le Vatican non, trop de monde ».
- **B, 3 messages** : « Où en est mon voyage ? », « Quelles activités tu me conseilles à Rome ? », « Combien il me reste ? ».
- **C, 3 messages**, des valeurs à problème : « Change mon départ au 30 février 2027 », « Finalement je pars du 3 au 10 mars 2020 », « Mets mon budget à -200 euros ».
- **D, 3 messages** : « Mon budget passe à 1200 euros », « Combien il me reste ? », « Finalement on va à Lisbonne plutôt qu'à Rome ».

Les deux colonnes ci-dessous sont **4 essais complets chacune, dans les mêmes conditions** : l'ancien code (avant les corrections de cette page) et le code actuel. Ce sont des ordres de grandeur, pas des taux. Les corrections (outil, résultats de recherche, prompt) ont été faites ensemble : on ne sait pas dire laquelle explique chaque amélioration. Un premier jeu d'essais sans clés d'API a servi à régler le scénario (les messages « sans rien chercher » viennent de là) ; il n'est pas compté.

| Observation | Ancien code | Code actuel |
|---|---|---|
| Le modèle crée le voyage, les dépenses (`booked`) et les lieux de lui-même, avec les bonnes valeurs | 4 sur 4 | 4 sur 4 |
| Des dates qui changent mettent à jour le voyage ; un montant corrigé met à jour la dépense, sans doublon | 4 sur 4 | 4 sur 4 |
| Lire n'écrit rien (base identique avant et après la conversation B) | 4 sur 4 | 4 sur 4 |
| Dans une nouvelle conversation, « Où en est mon voyage ? » appelle `trip_get` et restitue dates, dépenses et budget exacts | 4 sur 4 (4 citent le lieu refusé) | 4 sur 4 (3 citent le lieu refusé) |
| Le reste du budget annoncé est le bon | A et B : 8 sur 8 ; D : 0 sur 4 (voir plus bas) | A, B et D : 12 sur 12 (1 080 €, 1 065 €, 765 €) |
| « Combien il me reste ? » appelle `budget_summary` | A : 1 sur 4 ; B : 3 sur 4 ; D : 4 sur 4 | A : 0 sur 4 ; B : 4 sur 4 ; D : 4 sur 4 |
| « Mon budget passe à 1200 euros » change le budget | **non, 0 sur 4** : le modèle crée une dépense « Budget » de 1 200 €, le budget de la base ne change pas, et la réponse annonce un budget dépassé (« il reste -135 € » ou « -335 € ») | oui, 4 sur 4 (`trip_save`), réponse 765 € |
| Un lieu refusé n'est pas reproposé quand on demande des activités | **non, 4 sur 4 le reproposent** (« Musée du Vatican »…) | 1 reproposition sur 4 |
| Une date passée (3 mars 2020) n'est pas écrite | oui, 4 sur 4 | oui, 4 sur 4 |
| « 30 février » | le 28 février est écrit **et une date de retour inventée** (3 au 6 mars), 4 sur 4 | le 28 février est écrit, la date de retour reste inchangée, 4 sur 4 ; le modèle ne dit pas que le 30 février n'existait pas |
| « -200 euros » | le modèle le réinterprète en 1 300 € et l'écrit, **3 sur 4** | rien n'est écrit, le budget reste à 1 500 €, 4 sur 4 |
| « On va à Lisbonne plutôt qu'à Rome » | `new_trip` utilisé 4 sur 4 ; **dates inventées pour Lisbonne 3 sur 4** | `new_trip` utilisé 4 sur 4 ; dates inventées 1 sur 4 |

Deux séries complémentaires, de 8 essais par version, avec une base préparée (Rome du 14 au 21 mars, 1 500 €, vol et hôtel réservés, Vatican refusé) :

- **« J'ai réservé un resto à 40 euros », puis « Combien il me reste ? »** : l'ancien code et le code actuel appellent `budget_summary` 8 fois sur 8 et répondent 1 025 € 8 fois sur 8.
- **« Finalement je pars du 3 au 10 mars 2020 », puis « Mets mon budget à -200 euros »** : la base n'est jamais modifiée (8 sur 8 pour chaque version), mais dans les deux versions le modèle appelle `trip_save` avec une destination « Paris » qu'il n'a jamais reçue (8 sur 8). Ce sont les dates passées qui sauvent la base.

Trois choses que l'essai ne montre pas ou montre mal :

- **Le refus d'une autre destination par l'outil n'a jamais été déclenché** par le vrai modèle (il passe directement par `new_trip`, dans l'ancien code comme dans le nouveau). Il n'est testé que par les tests unitaires.
- **Les réponses affichées restent fragiles.** À « Je pars à Rome… note-le sans rien chercher », le modèle répond par un récapitulatif « Aucun vol trouvé pour le moment » alors qu'il n'a rien cherché : 7 fois sur 14 avec l'ancien code, 12 fois sur 16 avec le nouveau (essais complets et séries courtes réunis). La différence n'est pas significative avec si peu d'essais, mais elle va dans le mauvais sens. Dans 2 essais sur 16 avec le nouveau code, la réponse présente des vols et un hôtel inventés (jamais avec l'ancien, 0 sur 14). La base, elle, n'est pas touchée.
- **Un essai a dérapé sur les valeurs à problème** avec une version intermédiaire du prompt : après « du 3 au 10 mars 2020 », le modèle a inventé un voyage à Madrid, lancé des recherches sur des dates passées, et répondu avec un récapitulatif de Madrid et un budget de 2 000 €. La base n'a pas bougé, car `trip_save` a refusé chaque date passée, mais la réponse était fausse.

Quatre leçons :

- **Le schéma garantit la forme, pas le sens.** Les contrôles de l'outil ont tenu à chaque fois qu'une valeur invalide leur est arrivée, mais ils ne peuvent pas juger un « Paris » que l'utilisateur n'a jamais dit, ni empêcher une réponse fausse.
- **Ce que fait le code vaut mieux qu'une consigne.** Le filtre des lieux refusés et le budget renvoyé par `trip_save` se vérifient dans les données. À l'inverse, une règle « n'appelle pas `trip_save` si la date n'existe pas » n'a rien changé (le modèle a écrit le 28 février 5 fois sur 5), et une consigne de dire qu'il a corrigé la date n'a pas été suivie (0 sur 5). Le prompt dit maintenant « enregistre le dernier jour de ce mois » : c'est ce que le modèle faisait déjà.
- **Une consigne qui autorise à sauter un appel est suivie.** Une version intermédiaire du prompt disait de reprendre le chiffre déjà renvoyé par l'écriture précédente : le modèle a alors cessé d'appeler `budget_summary` (0 sur 12), alors que le catalogue demande que la question « interroge la base ». Le prompt a été remis à « Appelle-le TOUJOURS ».
- **La base se vérifie à côté du modèle.** Les réponses du modèle sont parfois justes pour de mauvaises raisons ou fausses malgré une base juste : seul `sqlite3` dit ce qui est vraiment enregistré.

Le comportement du vrai modèle n'est pas couvert par les tests : cet essai se refait à la main.

## Limites connues

- Un seul voyage actif : pas de retour vers un ancien voyage (`/clear` les efface tous).
- **Le modèle peut encore écrire ce que l'utilisateur n'a pas dit.** Avant les corrections, il a inventé une date de retour (4 essais sur 4 pour « 30 février »), réinterprété « -200 euros » en 1 300 € (3 sur 4) et inventé les dates d'un nouveau voyage (3 sur 4). Le prompt lui demande maintenant de n'envoyer que le champ qui change, et la date de retour et le budget n'ont plus bougé dans les 4 essais complets suivants, mais rien dans le code ne peut le garantir : une valeur inventée qui passe la validation est écrite. Les réponses affichées peuvent aussi être fausses ou inventées alors que la base reste juste (voir l'essai).
- **Le refus d'une autre destination ne protège que les voyages qui ont du contenu.** Sur un voyage encore vide, la destination se remplace sans contrôle. Et il n'a jamais été déclenché par le vrai modèle (voir l'essai) : il n'est testé que par les tests unitaires.
- **Une date qui n'existe pas est ramenée au dernier jour du mois** (30 février devient 28 février), par le modèle et sans qu'il le dise. Un montant négatif ou nul et une date passée ne sont jamais écrits.
- **`budget_summary` n'est pas appelé à chaque question d'argent**, malgré le prompt (« Appelle-le TOUJOURS »). Le modèle le fait presque toujours dans une nouvelle conversation (4 sur 4), mais pas juste après l'écriture qui vient de lui renvoyer le chiffre (0 sur 4 dans la conversation A). Le chiffre reste alors exact, car il vient du SQL de cette écriture ; il ne serait périmé que si la base changeait en dehors de la conversation (un autre processus qui écrit dans `data/trips.db`).
- **Les lieux refusés sont retirés des résultats de `search_activities` seulement.** Le filtre cherche le nom exact du lieu (mots entiers, sans accents ni casse) dans les titres et dans les phrases du contenu. Un autre nom du même lieu (« Chapelle Sixtine » pour « Vatican »), `web_search` et `search_housing` ne sont pas filtrés, et le modèle peut citer le lieu de lui-même : 1 réponse sur 4 dans l'essai complet, 1 sur 6 dans un essai ciblé.
- **Les noms de lieux ne sont pas rapprochés d'une langue à l'autre.** Le modèle a noté « Colosseo » pour « Colisée » dans 2 essais sur 4 avec l'ancien code et 3 sur 4 avec le nouveau : redire « Colisée » créerait une seconde ligne.
- **Une seule destination par voyage.** Il n'y a pas de table de villes ou d'étapes : un séjour en plusieurs villes n'a pas de structure propre dans la base.
- **Une dépense ou un lieu n'est rattaché à aucun jour.** « Qu'est-ce qu'on avait dit pour le jour 2 ? » n'a pas de réponse dans la base ; le texte de l'itinéraire est dans le [document d'itinéraire](document-itineraire.md).
- **Les contraintes personnelles** (allergies, rythme, budget habituel) ne sont pas dans la base : elles viennent du profil voyageur ([sessions et profils](sessions-et-profils.md)).
- **Pas d'historique des décisions sur un lieu.** La dernière décision remplace la précédente : l'ancienne valeur n'est renvoyée au modèle qu'une fois, dans `changes`, et n'est pas stockée (contrairement à `document_edits` pour le document). Une raison déjà enregistrée est conservée si la nouvelle décision n'en donne pas : un lieu refusé « trop cher » puis retenu garde cette raison.
- Pas de conversion de devises.
- Deux libellés sans mot commun pour une même dépense restent deux lignes ; l'alerte ne couvre pas une reformulation plus libre.
- Les tests ne couvrent pas le comportement du vrai modèle (écrit-il bien au bon moment ?) ; seul le prompt le lui demande. Le résultat de l'essai à la main est dans « Essai avec un vrai modèle ».

## Tests

[tests/test_trip_memory.py](../tests/test_trip_memory.py) appelle les outils directement sur une base temporaire. `/clear` est testé dans [tests/test_trip_memory.py](../tests/test_trip_memory.py) (la fonction) et [tests/test_itinerary_document.py](../tests/test_itinerary_document.py) (la commande et sa confirmation). Un garde-fou ([tests/__init__.py](../tests/__init__.py)) interdit à toute la suite d'ouvrir la vraie base `data/trips.db`. Les tests vérifient aussi que les `enum` de `config/tools.yaml` sont ceux de la base, que chaque outil est enregistré dans le serveur MCP, et que le prompt système dit au modèle quand utiliser chacun. Le refus d'une autre destination, le résumé de budget renvoyé par `trip_save` et la lecture des lieux refusés (`rejected_places`) sont aussi dans `tests/test_trip_memory.py`. Le filtre des résultats d'activités est dans [tests/test_web_search.py](../tests/test_web_search.py), avec la recherche mockée.

## Fichiers

[src/tools/trip_memory.py](../src/tools/trip_memory.py) (les six outils et leur validation) · [src/tools/search_activities.py](../src/tools/search_activities.py) (filtre des lieux refusés) · [src/tools/trip_db.py](../src/tools/trip_db.py) (schéma, connexion, version, `clear_all`) · [src/cli.py](../src/cli.py) (`/clear`) · [config/tools.yaml](../config/tools.yaml) · [src/tools/mcp_server.py](../src/tools/mcp_server.py)

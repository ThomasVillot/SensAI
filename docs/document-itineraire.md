# Document d'itinéraire (M4)

> Le modèle tient à jour un document d'itinéraire, séparé de la conversation, **une section à la fois** et en appelant un outil. Il ne réécrit jamais le document en entier : une édition invalide est refusée et le document reste identique.

## Pourquoi cette feature

- **Un livrable, pas un transcript.** Le catalogue de features (Notion) demande un document vivant, mis à jour de façon incrémentale au fil des tours et distinct de l'historique de la conversation. La différence avec les autres features : [mémoire du voyage](memoire-du-voyage.md) (M3) garde des faits requêtables (dépenses, lieux), alors que M4 garde un **texte lisible** qui évolue et survit au redémarrage.
- **Le piège identifié** : demander au modèle de renvoyer le document complet à chaque tour. C'est lent, coûteux en tokens, et ça corrompt le document dès que le modèle abrège ou reformule. Le catalogue impose un format d'édition contraint, validé avant application.
- **Le cœur de la feature est le mécanisme d'édition**, pas le document. Les trois questions que le catalogue prévoit pour le jury ont leur réponse dans cette page :
  - *quel format, et pourquoi pas un autre ?* → voir « Le format d'édition » ;
  - *que se passe-t-il si le modèle vise une section qui n'existe pas ?* → l'édition est refusée, la réponse liste les sections valides, rien n'est écrit ;
  - *comment détecter une corruption ?* → on ne la détecte pas après coup, on la rend impossible à écrire (voir « Validation avant écriture »).
- Huit user stories ([USER_STORIES.md](../USER_STORIES.md)), en quatre groupes (document vivant, édition ciblée, robustesse, consultation et export) : ne modifier que la section concernée sans réécrire le reste, et ne jamais perdre son travail sur une réponse mal formée.

## Ce que ça fait

Exemple : « J'ai réservé le vol Paris-Rome, 120 euros. Note-le dans mon itinéraire. » Le modèle enregistre la dépense ([M3](memoire-du-voyage.md)) et appelle `document_edit(section="transport", mode="append", content="Vol Paris-Rome (réservé)")`. La section Transport change ; les quatre autres ne bougent pas.

### Les outils

| Outil | Paramètres | Rôle |
|---|---|---|
| `document_edit` | `section`, `content`, `mode` | modifie **une** section du document du voyage en cours |
| `document_get` | `section` (facultatif) | relit le document, ou une seule section |

| Champ | Valeurs |
|---|---|
| `section` | `overview` (aperçu), `transport`, `lodging` (hébergement), `activities` (activités), `practical` (conseils pratiques) |
| `mode` | `replace` (remplace le texte de la section, valeur par défaut), `append` (ajoute à la fin, sur une nouvelle ligne), `remove` (retire l'élément qui contient le texte donné) |

La réponse de `document_edit` dit ce qui s'est passé : `action` (`created`, `updated` ou `unchanged`), les sections remplies avec leur taille, et les sections encore vides. Avec `remove`, elle donne aussi ce qui a été retiré (`removed`).

### La commande `/doc`

Dans la CLI, `/doc` affiche le document en Markdown, sans appeler le modèle. `/doc FICHIER` l'écrit dans un fichier (et refuse d'écraser un fichier qui existe déjà) : en PDF si le nom finit par `.pdf`, en JSON par `.json`, en Markdown sinon. `/share doc` le partage par un lien public en lecture seule. Le document est donc exportable à tout moment ; PDF, JSON et lien sont décrits dans [export et partage](export-et-partage.md) (X3).

### Dans l'interface web

Le bouton **Document** de l'en-tête ouvre le panneau « Document du voyage » : le document en Markdown, avec les boutons Markdown, JSON, PDF, **Partager** et **Effacer le voyage**. Le panneau se rouvre et se **met à jour tout seul** chaque fois que le modèle appelle `document_edit` pendant une réponse. Sans voyage, il affiche « Aucun document : décris d'abord ton voyage » ([interface web](interface-web.md)).

Pour **prouver** qu'une édition n'a touché qu'une section, exporter avant et après, puis comparer :

```bash
diff avant.md apres.md
```

Le document exporté a un en-tête tiré du voyage (destination, dates, voyageurs), puis les cinq sections dans l'ordre. Une section vide affiche « À compléter ».

## Comment ça marche

### Le format d'édition

Le format d'édition **est l'appel d'outil lui-même** : `document_edit(section, content, mode)`. Il est parsable par construction (arguments typés, transmis par MCP), il vise une seule section parmi une liste fermée, et il n'y a rien à extraire d'un texte libre. Choix de conception de cette feature, sans mesure comparative (les alternatives n'ont pas été essayées) :

- **Régénérer le document entier** : interdit par le catalogue, et c'est le piège.
- **Un patch (diff) ou des marqueurs à repérer dans la réponse** : il faudrait écrire et valider un second parseur, et un petit modèle local casse facilement les en-têtes d'un patch.
- **Un appel d'outil par section** : réutilise ce que le projet valide déjà ([MCP](mcp.md), arguments rendus au modèle en cas d'erreur) et limite l'édition à une section par construction.

### Retirer un élément (`remove`)

Pour retirer « le Vatican », le modèle envoie seulement son nom : `document_edit(section="activities", mode="remove", content="Vatican")`. C'est **le code** qui calcule ce qui reste, le modèle ne recopie jamais la section.

- La comparaison ignore les accents, les majuscules et la ponctuation, et porte sur des mots entiers : « Vatican » retire « Vatican (visite le mardi) », mais « Vati » ne retire rien.
- Si la ligne liste plusieurs éléments, **seul celui qui est demandé part** : dans « Colisée (lundi), Vatican (mardi) », il reste « Colisée (lundi) ». Une virgule, un point-virgule ou « et » séparent deux éléments seulement s'ils sont hors parenthèses et suivis d'une majuscule. « Hôtel Colosseo, 3 nuits » ou « Vatican (mardi, 9h) » restent donc un seul élément, retiré en entier.
- Le code ne devine pas : un élément absent est refusé, et l'indice liste les lignes de la section. Un élément présent sur plusieurs lignes, sans que l'une soit exactement lui, est refusé, et l'indice liste les lignes concernées. Un `remove` sur une section vide est refusé. Avec plusieurs éléments (un par ligne de `content`), c'est tout ou rien.
- Retirer le dernier élément vide la section, qui affiche de nouveau « À compléter ».

**Pourquoi ce mode.** Avant lui, retirer un élément voulait dire relire la section avec `document_get`, puis la renvoyer en entier avec `mode=replace`. Dans les essais, `qwen3:8b` n'a jamais appelé `document_get`, et la suppression a réussi 6 fois sur 9. Dans les 3 échecs, il a remplacé la section par le seul élément à retirer (2 fois) ou ajouté une ligne « Vatican (supprimé) » (1 fois). Avec `remove`, le modèle envoie le nom de l'élément, ce qu'il faisait déjà spontanément.

### Stockage

Le document est stocké dans la base du voyage (`data/trips.db`, voir [mémoire du voyage](memoire-du-voyage.md)) : **un document par voyage**, qui survit à un redémarrage. En ouvrant un autre voyage (`new_trip`), le document repart vide ; celui de l'ancien voyage reste dans la base.

- `document_sections` : une ligne par section remplie (une section absente est vide).
- `document_edits` : chaque édition qui change quelque chose, avec le texte d'avant et d'après. Le texte d'avant vaut `NULL` pour une création, celui d'après vaut `NULL` quand un `remove` a vidé la section.

Ces tables sont arrivées avec la version 2 du schéma, et la **version 3** ajoute le mode `remove` à l'historique. Les bases en version 1 (celle d'avant M4) et en version 2 sont **migrées sur place, sans perte**, au premier appel d'un outil :

- **de 1 à 3**, on crée les deux tables du document ;
- **de 2 à 3**, on recrée `document_edits` en recopiant ses lignes telles quelles, parce que SQLite ne sait pas modifier une contrainte `CHECK`.

Une migration n'a lieu que si les tables de la version de départ sont présentes : un autre fichier SQLite n'est jamais modifié.

### Validation avant écriture

Trois niveaux, comme M3 :

1. [config/tools.yaml](../config/tools.yaml) déclare au modèle les sections et les modes autorisés (`enum`). Attention : le contrôle des arguments de l'agent (`is_schema_valid`) ne vérifie que les champs obligatoires et les types, **pas les `enum`**. Ce niveau guide le modèle, il ne le contraint pas.
2. Les contrôles Python de [src/tools/trip_document.py](../src/tools/trip_document.py), qui sont les vrais refus : section et mode connus, texte non vide, taille maximale de 4000 caractères par section, voyage existant.
3. Les contraintes SQL (`CHECK`, clé primaire, clé étrangère).

Un refus renvoie `{"error", "tool", "hint"}` au modèle. **Rien n'est écrit** : le texte exporté, les lignes de la base et l'historique sont identiques avant et après.

### Pourquoi le reste du document ne peut pas bouger

- La structure (les cinq sections, leur ordre, leurs titres) vit dans le code et dans le schéma, pas dans le texte écrit par le modèle.
- Le contenu d'une section **ne peut pas contenir de titre Markdown** (une ligne qui commence par `#`). Coller le document entier, ou le texte d'une autre section avec son titre, est refusé. Un `#` au milieu d'une ligne (« priorité #1 ») reste accepté.
- Une édition ne touche qu'une ligne de `document_sections`. Un test vérifie que les autres lignes gardent texte et date de mise à jour.
- Avec `append`, le texte existant de la section est conservé tel quel, au caractère près. Avec `remove`, seule la ligne de l'élément change (elle disparaît, ou perd seulement cet élément).
- Redire exactement le même texte donne `unchanged` : aucune écriture, aucune ligne d'historique.

## Essai avec un vrai modèle

Les tests unitaires n'utilisent pas de modèle. Les 29 et 30/10/2026, la feature a été essayée avec `qwen3:8b` (température 0,7) dans une copie isolée du dépôt, avec un scénario de cinq messages : voyage à Rome, vol réservé (120 €), hôtel retenu (300 €), deux activités, puis « Finalement on supprime le Vatican de l'itinéraire. ». Les échantillons sont petits (4 essais par version), donc ce sont des ordres de grandeur, pas des taux.

Avec la version finale (mode `remove`) :

| Observation | Résultat |
|---|---|
| Le modèle appelle `document_edit` de lui-même, une section par appel, sans titre | oui, 4 essais sur 4 |
| Vol, hôtel et activités vont chacun dans leur section | oui, 4 essais sur 4 |
| Les dépenses (`expense_save`) et les lieux (`place_save`) de M3 sont enregistrés | oui, 4 essais sur 4 |
| « On supprime le Vatican » est un `remove`, et il ne reste que le Colisée | oui, 4 essais sur 4 |
| Le `diff` de la suppression n'a de lignes que dans Activités | oui, 4 essais sur 4 |
| Après `/new`, `/doc` affiche toujours le document | oui, 4 essais sur 4 |
| Le Vatican et le Colisée sur **la même ligne** (base préparée, un seul message) : seul le Vatican part | oui, 4 essais sur 4 |
| Un hôtel retenu **sans prix** est noté dans le document | non : voir les limites |

La suppression, version par version :

| Version | Suppression réussie | Ce qui s'est passé |
|---|---|---|
| `replace` (relire, puis renvoyer la section) | 6 sur 9 | voir « Pourquoi ce mode » ; `document_get` n'a jamais été appelé |
| `remove` qui retirait la ligne entière | 0 sur 4 | le modèle avait noté « Colisée (lundi), Vatican (mardi) » sur une seule ligne : le Colisée partait avec |
| `remove` qui refusait une ligne partagée et proposait l'appel exact | 3 sur 4, et 0 sur 4 sur une ligne partagée | après le refus, le modèle annonce « je vais modifier la section » mais ne refait jamais l'appel |
| `remove` qui ne retire que l'élément (version finale) | 4 sur 4, et 4 sur 4 sur une ligne partagée | |

Deux leçons pour un petit modèle : il vaut mieux que **le code fasse le calcul** (ce qui reste après un retrait) que de le demander au modèle, et il ne faut pas compter sur lui pour **refaire un appel après un refus**. Le mécanisme a toujours tenu, y compris dans les échecs : seule la section Activités a bougé.

Au passage, les consignes du prompt pèsent sur les autres outils. Pendant les essais intermédiaires, `place_save` n'était plus appelé (0 essai sur 12, contre 3 sur 6 avant). Les exemples de `document_edit` dans [config/tools.yaml](../config/tools.yaml) citent maintenant `place_save` avec lui. Il est revenu dans 4 essais sur 4.

## Limites connues

- **Un élément sans prix n'est pas noté.** Avec `qwen3:8b`, « on garde l'hôtel Colosseo pour 3 nuits » déclenche `expense_save` avec un montant de 0, qui est refusé ; le modèle demande alors le prix et n'appelle pas `document_edit`. Quatre formulations du prompt ont été essayées sans le corriger ; la seule qui y est arrivée cassait l'enregistrement des dépenses de M3. Avec un prix donné, cela fonctionne.
- **Le mécanisme garantit la forme et le périmètre, pas le sens.** Une édition bien formée mais fausse est appliquée (avant `remove`, un `replace` a ainsi gardé le Vatican et retiré le Colisée). L'historique `document_edits` permet de la retrouver, mais il n'y a pas d'annulation.
- **`document_get` avant une correction.** Pour corriger un élément (pas le retirer), il faut encore `replace`. Le prompt demande de relire la section avant ; `qwen3:8b` ne l'a jamais fait dans les essais et réécrit depuis la conversation. Si le texte de la section n'est plus dans le contexte, des éléments peuvent être perdus.
- **La règle de la majuscule est une heuristique.** « Hôtel Colosseo, Rome » est vu comme deux éléments, donc retirer « Hôtel Colosseo » y laisse « Rome ». À l'inverse, deux visites écrites en minuscules après une virgule (« colisée, vatican ») ne sont qu'un élément, retiré en entier.
- Cinq sections fixes, 4000 caractères chacune, du texte seulement. Pas de section libre.
- **Les sections sont des thèmes, pas des jours.** La fiche M4 de Notion suggère « un jour = une section » ; ici il n'y a pas de section par jour, et un programme jour par jour n'a pas de structure propre dans le document.
- `document_edits` est écrit à chaque édition mais aucune commande ne le relit encore.
- Les `enum` de `tools.yaml` ne sont pas vérifiés par `is_schema_valid` (voir plus haut), pour M3 comme pour M4.

## Tests

[tests/test_itinerary_document.py](../tests/test_itinerary_document.py) (77 tests) appelle les outils directement sur une base temporaire. Un garde-fou ([tests/__init__.py](../tests/__init__.py)) interdit à toute la suite d'ouvrir la vraie base. Il couvre :
- l'édition (création, remplacement, ajout, retrait d'une ligne ou d'un seul élément d'une ligne, section vidée, texte inchangé, texte nettoyé, voyage obligatoire) ;
- « une section à la fois » (trois éditions, trois sections, diff du document exporté après un ajout et après un retrait, lignes de la base) ;
- les refus, qui laissent le document intact (section inconnue, titre Markdown, texte vide ou non textuel, mode inconnu, taille, retrait d'un élément absent ou ambigu, retrait dans une section vide, arguments absurdes) ;
- la lecture et l'export Markdown ;
- la base (redémarrage, un document par voyage, historique, contraintes SQL, migration des versions 1 et 2) ;
- le branchement (`tools.yaml`, serveur MCP, prompts v1 et v2) et la commande `/doc`.

Le comportement du vrai modèle n'est **pas** couvert par les tests (voir « Essai avec un vrai modèle »).

## Fichiers

[src/tools/trip_document.py](../src/tools/trip_document.py) (les deux outils, la lecture et l'export) · [src/tools/trip_db.py](../src/tools/trip_db.py) (tables, sections, migrations) · [src/cli.py](../src/cli.py) (`/doc`) · [config/tools.yaml](../config/tools.yaml) · [config/prompts.yaml](../config/prompts.yaml) · [src/tools/mcp_server.py](../src/tools/mcp_server.py)

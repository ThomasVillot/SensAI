# Export et partage (X3)

> `/export` écrit la conversation en Markdown, en JSON ou en PDF ; `/doc` fait de même pour le document d'itinéraire, l'artefact du projet. `/share` crée un **lien public en lecture seule** vers la conversation, et `/share doc` vers le document d'itinéraire. Le lien s'ouvre dans un navigateur sans authentification, expire au bout de 7 jours et peut être révoqué à tout moment avec `/unshare`. L'interface web propose les mêmes actions avec des boutons (menu « Exporter », panneau « Document »).

## Pourquoi cette feature

- **Le sujet et le catalogue de features (Notion) le demandent** : exporter l'historique de conversation **et les artefacts** vers des formats exploitables (JSON, Markdown, PDF), et générer un lien public sans authentification pour consulter une conversation ou un artefact partagé dans un navigateur. Les références citées sont le bouton « partager la conversation » de ChatGPT et de Claude, et l'export PDF d'une session pour l'archiver ou la transmettre.
- **L'artefact de SensAI est le [document d'itinéraire](document-itineraire.md)** (M4) : le livrable tenu par le modèle, séparé de la conversation. X3 l'exporte et le partage comme la conversation.
- **Huit user stories** ([USER_STORIES.md](../USER_STORIES.md)), en quatre groupes (export, partage par lien, contrôle du lien, sécurité) : exporter la conversation pour la coller dans un document sans retouche, la partager par un lien à quelqu'un qui n'a rien installé, pouvoir révoquer ce lien, et ne pas en faire une porte d'entrée.
- **Le piège identifié par le catalogue** : un lien public sans authentification est, par construction, une surface d'attaque et une fuite potentielle de données. La section « Sécurité » répond aux questions que le catalogue prévoit pour le jury.

## Ce que ça fait

### Les commandes

| Saisie | Effet |
|---|---|
| `/export` | affiche la conversation en Markdown, sans appeler le modèle |
| `/export FICHIER` | écrit la conversation : **PDF** si le nom finit par `.pdf`, **JSON** par `.json`, Markdown sinon |
| `/doc` | affiche le document d'itinéraire en Markdown (M4) |
| `/doc FICHIER` | écrit le document d'itinéraire : PDF, JSON ou Markdown, selon le même principe |
| `/share` | demande confirmation, puis crée un lien vers la conversation et l'affiche |
| `/share doc` | demande confirmation, puis crée un lien vers le document d'itinéraire |
| `/unshare LIEN` | révoque ce lien (le lien complet ou seulement son identifiant) |
| `/unshare` | révoque tous les liens créés dans la conversation en cours (conversation et document) |

Aucune de ces commandes n'appelle le modèle. Un fichier qui existe déjà n'est jamais écrasé. Une conversation vide, ou un voyage sans document, n'est ni exporté ni partagé.

### Dans l'interface web

| Où | Action | Route de l'API |
|---|---|---|
| menu **Exporter** | Markdown, JSON, PDF : télécharge la conversation | `GET /api/conversations/{id}/export?format=md\|json\|pdf` |
| menu **Exporter** | Lien de partage : demande confirmation, crée le lien | `POST /api/conversations/{id}/share` |
| panneau **Document** | boutons Markdown, JSON, PDF : télécharge le document | `GET /api/document/export?format=md\|json\|pdf` |
| panneau **Document** | Partager : demande confirmation, crée le lien | `POST /api/document/share` |
| panneau du lien | Copier, Révoquer | `DELETE /api/shares/{token}` |

Les fichiers sont les mêmes que ceux de la CLI (`conversation_<id>.md`, `itineraire.pdf`...), produits par les mêmes fonctions. L'interface web ne sait révoquer qu'un lien à la fois, celui qu'elle vient d'afficher : l'équivalent de `/unshare` sans argument (tous les liens d'une conversation) n'existe que dans la CLI. Le menu Exporter est actif quand la conversation compte au moins un tour terminé.

### Partager

Rien d'autre à lancer : au premier `/share`, la CLI démarre elle-même le serveur des liens, en tâche de fond.

```
Vous> /share doc
Créer un lien public vers le document d'itinéraire ? Toute personne qui a le lien pourra le lire pendant 7 jour(s), sans se connecter. [o/N] o
Lien de partage, valable jusqu'au 06/11/2026 à 18:50 :
http://192.168.1.42:8766/share/YIFVeSU0qGN1XpWryhF7BxU4W-xXmPQ_7b3mwf94PGQ
Serveur des liens démarré sur http://192.168.1.42:8766 : le lien s'ouvre tant que SensAI reste ouvert.
Pour le révoquer : /unshare LIEN
```

Le serveur s'arrête quand on quitte SensAI, et la CLI le rappelle à la sortie. Pour que les liens restent ouverts sans SensAI, pendant leurs 7 jours, on lance le serveur seul :

```bash
python share.py          # serveur des liens sur le port 8766
```

Si `share.py` tourne déjà, la CLI le reconnaît et n'en démarre pas un second. Si le port 8766 est pris par un autre programme, le lien est quand même créé, et la CLI dit de changer `share_port` ou de lancer `share.py`.

Le lien ouvre une page en lecture seule, avec un bandeau qui donne la date d'expiration :

- pour une conversation : les échanges rendus (titres, listes, tableaux, blocs de code) ;
- pour le document d'itinéraire : la destination, les dates et les cinq sections, **un élément par ligne** comme dans le document.

Un lien inconnu, expiré ou révoqué affiche « Lien indisponible ».

### Le Markdown exporté

```markdown
# Conversation SensAI

2 échanges · modèle : qwen3:8b

## Échange 1

**Vous**

> Que visiter à Rome ?

**SensAI**

Outils utilisés :

- `web_search_basic(query="Rome visites")`

## Jour 1
...

Pages consultées par les outils :

- [Colisée](<https://colosseo.it/>)
```

- Chaque échange a son titre. La question est citée (`>`), la réponse est recopiée **telle que le modèle l'a écrite** : titres, listes et blocs de code sont conservés.
- Les appels d'outils sont listés avec leurs arguments. Les résultats bruts ne sont pas recopiés (ils sont dans le JSON), mais les pages qu'ils renvoient sont listées sous « Pages consultées par les outils » : un résultat de recherche web donne `[titre](<url>)`. Ces pages s'ajoutent aux sources que le modèle cite lui-même dans sa réponse : s'il oublie de les citer, elles restent quand même dans l'export.
- Le prompt système, et donc le contenu du profil voyageur, n'est jamais exporté. Le JSON garde seulement le nom du profil actif, noté sur chaque message.

Le Markdown du document d'itinéraire est celui de M4 ([document d'itinéraire](document-itineraire.md)).

### Le JSON exporté

Conversation :

```json
{
  "format": "sensai-conversation",
  "version": 1,
  "conversation": "2026-10-30_11-00-22",
  "turns": 6,
  "messages": [ ... ]
}
```

`messages` contient les messages **tels qu'ils sont sauvegardés** dans la session : questions, appels d'outils (`tool_calls`), résultats (`role: "tool"`), réponses, et les annotations `model` et `profile`. `conversation` est l'identifiant de la conversation, le même que dans le [journal des interactions](journal-et-metriques.md).

Document d'itinéraire :

```json
{
  "format": "sensai-itinerary",
  "version": 1,
  "trip": { "destination": "Rome", "start_date": "2027-03-12", "end_date": "2027-03-19", "budget": 1500.0, "currency": "EUR", "travelers": 2 },
  "sections": [ { "name": "transport", "title": "Transport", "content": "Vol Paris-Rome (réservé, 120 €)", "updated_at": "..." }, ... ]
}
```

Les cinq sections sont toujours présentes, dans l'ordre ; une section vide a `content` à `null`.

### Le PDF exporté

Un vrai fichier PDF au format A4 : le même contenu que le Markdown, mis en page (titres, listes, tableaux, code en police à chasse fixe, citations), avec des **liens cliquables**, un numéro de page en pied de page et le titre dans les propriétés du fichier.

## Comment ça marche

### Toute la conversation, pas seulement la fenêtre du modèle

Une session reprise ne renvoie au modèle que les `max_reloaded_turns` derniers tours ([sessions et profils](sessions-et-profils.md)) ; les plus anciens restent dans le fichier. L'export et le partage lisent `Session.conversation`, qui contient **aussi ces tours archivés**. Une longue conversation reprise s'exporte donc en entier, pas tronquée.

### Un format qui ne casse pas

Le piège d'un export Markdown est le bloc de code jamais fermé : un ` ``` ` ouvert par le modèle et jamais refermé avale tout le reste du fichier, titres compris. [src/export.py](../src/export.py) prend trois précautions :

- un bloc de code resté ouvert à la fin d'une réponse est **refermé** ;
- la question de l'utilisateur est dans une citation : un `#` ou un ` ``` ` tapé par l'utilisateur reste à l'intérieur ;
- un argument d'outil qui contient des accents graves (`` ` ``) est entouré d'un délimiteur plus long, comme le prévoit CommonMark.

Les caractères spéciaux (`<`, `&`, `|`, `\`, emoji) sont recopiés tels quels dans le fichier Markdown. Sur la page de partage et dans le PDF, ils sont échappés (voir plus bas).

### Un seul rendu pour la page et le PDF

La page du lien et le PDF partent du **même Markdown**, converti en HTML par `markdown-it-py` (CommonMark, avec tableaux et texte barré). Le HTML brut est désactivé, et une image Markdown devient un simple lien : SensAI ne télécharge jamais une image citée par le modèle. Pour le document d'itinéraire, chaque retour à la ligne est conservé, parce que M4 écrit un élément par ligne.

Le PDF est produit par `fpdf2` (pur Python) à partir de ce HTML, avec la police **DejaVu fournie par matplotlib**, déjà installé pour le projet : elle couvre les accents, `€`, `«`, `’`. Un caractère qu'elle n'a pas, comme un emoji, est remplacé par `□`, pour que le lecteur voie qu'il manque quelque chose au lieu d'un trou.

`markdown-it-py` et `fpdf2` sont des bibliothèques de mise en forme de texte : elles n'ont aucun lien avec le modèle, et tous les appels au modèle passent toujours par l'API HTTP d'Ollama.

### Le lien est une photographie

`/share` copie la conversation (ou `/share doc` le document) dans `shares/<identifiant>.json` (`shares_dir`), avec sa date de création, sa date d'expiration et le nom de la conversation où il a été créé. Ce qui change après le partage **n'apparaît pas** dans le lien : il faut en créer un nouveau. La page affiche exactement l'export Markdown de cette photographie. Un test vérifie que le HTML de la page contient le rendu de `to_markdown`.

### Un serveur à part

[src/share_server.py](../src/share_server.py) est une application FastAPI qui n'a **qu'une route**, `GET /share/{identifiant}`. Elle ne parle ni à Ollama, ni à l'agent, ni au serveur MCP, et ne lit ni les sessions ni la base du voyage : seulement le dossier `shares/`. Elle tourne sur son propre port (8766), séparé de l'API web (8765), qui n'a aucune authentification et ne doit pas être exposée.

Deux façons de la lancer :
- **par la CLI**, au premier `/share` : uvicorn tourne dans un fil secondaire, qui ne touche pas au `Ctrl+C` de la conversation, et s'arrête à la sortie de SensAI ;
- **seule**, avec [share.py](../share.py), pour des liens qui survivent à la CLI.

Avant de démarrer, la CLI essaie de réserver le port, ce qui est instantané. S'il est pris, elle demande `/share/-` : si la réponse est la page « Lien indisponible » de SensAI, c'est un `share.py` déjà lancé, et elle s'en sert. Sinon, le port appartient à un autre programme, et elle le dit. Une première version testait le port en s'y connectant, mais sous Windows une connexion refusée met une seconde à échouer : chaque premier `/share` aurait attendu une seconde.

### Imprimer la page

La page du lien a une feuille de style d'impression : texte noir sur fond blanc même quand le navigateur est en mode sombre, sans le bandeau d'expiration ni le pied de page, et sans couper un bloc de code ou un tableau entre deux pages. `Ctrl+P` donne donc aussi un PDF propre, pour qui n'a que le lien.

## Sécurité

Les questions que le catalogue prévoit pour le jury, et leurs réponses.

**Comment est généré l'identifiant ? Est-il devinable ?** Avec `secrets.token_urlsafe(32)` : 32 octets du générateur aléatoire cryptographique du système, soit 256 bits et 43 caractères. Il ne dépend ni du nom de la session, ni de la date. Le deviner revient à tirer au hasard une valeur parmi 2^256. Et le serveur ne laisse rien filtrer : un lien inconnu, expiré ou révoqué donne **la même page 404**, on ne peut pas savoir lequel des trois.

**Peut-on révoquer un partage ?** Oui, `/unshare LIEN`, ou `/unshare` pour tous les liens créés dans la conversation. Le fichier est supprimé : le lien ne s'ouvre plus à la requête suivante. Sans révocation, le lien **expire** après `share_ttl_days` jours (7 par défaut) ; un lien expiré est supprimé quand on l'ouvre ou quand un nouveau lien est créé.

**Que se passe-t-il si la conversation contenait des données personnelles ?** Le lien montre la conversation ou le document tel quel : l'export et le partage ne font **aucune anonymisation** eux-mêmes. Ce que l'utilisateur a tapé a déjà été masqué avant d'entrer dans la conversation (nom, e-mail, téléphone remplacés par `[EMAIL]`...), mais ce que le modèle ou un outil a écrit (une adresse d'hôtel, le texte d'une page web, le document d'itinéraire) est partagé tel quel ([garde-fous](garde-fous.md)). Les garde-fous du partage sont ailleurs :
- partager est un acte explicite, et la confirmation dit que toute personne qui a le lien pourra lire ce qui est partagé ;
- le prompt système et le profil voyageur ne sont pas dans la page ;
- le lien est une photographie : la suite de la conversation ne fuit pas ;
- expiration et révocation limitent la durée d'exposition.

**Le lien est-il une porte d'entrée ?** La surface est réduite au minimum :
- une seule route en lecture ; `/docs`, `/redoc` et `/openapi.json` de FastAPI sont désactivés ;
- l'identifiant est validé (43 caractères `A-Z a-z 0-9 - _`) **avant** tout accès au disque : `../config` ou `..\victim` n'atteignent jamais le système de fichiers ;
- le rendu Markdown a le **HTML brut désactivé** : un `<script>` ou un `<img onerror>` écrit dans la conversation s'affiche comme du texte, et `markdown-it-py` refuse les liens `javascript:` ;
- même si quelque chose passait, l'en-tête `Content-Security-Policy: default-src 'none'` interdit tout script, image ou ressource externe ; la page ne peut pas non plus être intégrée dans une autre (`frame-ancestors 'none'`) ;
- `X-Robots-Tag: noindex` (pas d'indexation), `Referrer-Policy: no-referrer` (l'identifiant ne fuit pas vers un site cité), `Cache-Control: no-store` (pas de copie en cache après révocation) ;
- un fichier de lien abîmé, que la page ne sait pas afficher, donne la page 404, jamais une erreur serveur.

## Essai réel

Le 30/10/2026, avec la vraie CLI et `qwen3:8b`, dans une configuration temporaire (sessions, liens, journaux et base du voyage hors du dépôt) :

| Étape | Résultat |
|---|---|
| « Cherche sur le web les horaires d'ouverture du Colisée à Rome, avec tes sources. » | `web_search_basic` appelé, réponse avec ses sources, 15,8 s |
| `/export essai.md`, `/export essai.json`, `/export conv.pdf` | trois fichiers écrits ; le JSON a `conversation` = le nom de la session ; le PDF a les outils, les listes, les liens cliquables et les deux listes de sources |
| `/share`, puis `o` | question de confirmation affichée, lien valable 7 jours |
| lien ouvert dans un navigateur, avec `python share.py` | la page s'affiche, aussi en largeur mobile (375 px) sans défilement horizontal |
| `python sensai.py --continue`, puis `/unshare` | le lien est révoqué ; le même lien affiche « Lien indisponible » |
| `/doc`, `/doc doc.pdf`, `/doc doc.json` (voyage à Rome, 4 sections remplies) | Markdown affiché, PDF et JSON écrits ; « Colisée (lundi) » et « Vatican (mardi) » sur deux lignes dans le PDF |
| `/share doc`, puis `o`, lien ouvert | page « Document d'itinéraire partagé » avec les cinq sections, un élément par ligne |
| impression de la page simulée en mode sombre | texte noir sur fond blanc, sans bandeau ni pied de page |
| le 31/10, `/share` **sans `share.py`** | « Serveur des liens démarré sur http://127.0.0.1:8766 » ; le lien s'ouvre dans le navigateur |
| puis `/exit` | « le serveur des liens s'arrête avec SensAI » ; le même lien ne répond plus |

**L'essai a trouvé deux défauts que les tests ne voyaient pas**, tous deux corrigés et couverts par un test :
- la recherche web renvoie une liste de chaînes qui contiennent elles-mêmes du JSON, et l'export ne cherchait les pages consultées que dans du JSON direct : la section manquait ;
- sur la page et dans le PDF du document, deux éléments d'une section (« Colisée (lundi) », « Vatican (mardi) ») étaient collés sur une seule ligne, parce qu'en Markdown un simple retour à la ligne ne compte pas.

Une session plus ancienne de 6 échanges (voyage à Rome, avec appels d'outils) a aussi été partagée. Un échange de test avec un tableau, un bloc de code, un bloc jamais fermé, `<script>alert(1)</script>`, une image piégée et un lien `javascript:` s'affiche correctement : deux blocs de code rendus, l'échange suivant intact, aucun script, aucune image, aucun lien `javascript:` dans la page, aucune erreur dans la console.

## Limites connues

- **Sans `share.py`, un lien ne s'ouvre que tant que SensAI est ouvert.** À la sortie, le serveur lancé par la CLI s'arrête ; le lien reste valable et s'ouvre de nouveau dès que `share.py` ou une nouvelle session SensAI qui fait `/share` le sert.
- **Le lien ne s'ouvre que sur le même réseau.** `share_host` vaut `0.0.0.0` : le serveur écoute sur tout le réseau, et le lien affiché contient l'adresse IP de la machine, détectée à la création du lien (`127.0.0.1` s'il n'y a pas de réseau). Sur un autre réseau, l'adresse change et il faut créer un nouveau lien ; avec un VPN, l'adresse détectée peut être celle du VPN. Le pare-feu doit autoriser Python, et un Wi-Fi qui isole ses appareils (école, lieu public) bloque le lien : un partage de connexion évite ce problème. Avec `"share_host": "127.0.0.1"`, le lien ne s'ouvre que sur la machine. Pour Internet, il faut un tunnel (cloudflared, ngrok) **vers le port 8766 uniquement**, jamais vers 8765 ; le lien affiché doit alors être adapté à la main.
- **Pas de HTTPS** : le serveur parle HTTP, donc le lien et la page circulent en clair sur le réseau local. À travers un tunnel, c'est le tunnel qui fournit HTTPS.
- **Pas d'anonymisation à l'export ni au partage** : seul ce que l'utilisateur tape est masqué, avant l'envoi au modèle (voir « Sécurité »).
- **Les emoji n'apparaissent pas dans le PDF** : la police DejaVu n'en a pas, ils deviennent `□`. La page de partage, elle, les affiche.
- **La mise en page du PDF est simple** : `fpdf2` ne comprend qu'une partie du CSS. Les tableaux n'ont pas de bordures verticales, et il ajoute un blanc d'une ligne après chaque tableau.
- **Pas de liste des liens actifs.** L'identifiant n'est affiché qu'une fois ; `/unshare` sans argument révoque tous les liens créés dans la conversation.
- **Les routes d'export et de partage de l'interface web n'ont pas de test d'API.** Les fonctions qu'elles appellent (`to_markdown`, `to_json`, `document_to_json`, `to_pdf`, `create_share`, `create_document_share`, `revoke_share`) sont testées, pas les routes elles-mêmes ni les boutons.
- **Un lien expiré reste sur le disque** jusqu'à ce qu'on l'ouvre ou qu'un nouveau lien soit créé. Il n'est jamais servi, mais il n'y a pas de nettoyage planifié.
- **Une réponse dont le texte contient `## Échange 2`** ressemble à un titre d'échange dans le Markdown : le contenu du modèle n'est pas réécrit.
- **Le serveur de partage n'a pas de limite de requêtes.** Avec 256 bits, deviner un identifiant reste hors de portée, mais rien ne ralentit un client qui insiste.
- **La feuille d'impression n'a été vérifiée qu'en simulation** (règles d'impression appliquées à l'écran) : l'aperçu d'impression du navigateur n'a pas été ouvert.

## Tests

[tests/test_export_share.py](../tests/test_export_share.py) (92 tests), sans modèle ni réseau, dans des dossiers et une base du voyage temporaires (le vrai dossier `shares/` et la vraie base ne sont jamais touchés). Il couvre :
- l'export Markdown (structure des échanges, titres, listes et code conservés, appels d'outils, pages consultées, y compris au format réel de la recherche web, bloc de code jamais fermé, accents graves dans les arguments, caractères spéciaux, Markdown tapé par l'utilisateur, réponse sans texte, images rendues en liens) ;
- l'export JSON (messages identiques à la session, JSON valide avec des caractères spéciaux, document d'itinéraire) ;
- l'export PDF (conversation et document, titre dans les propriétés, liens cliquables, contenu piégé, caractères absents de la police, police introuvable) ;
- `Session.conversation` (tours archivés inclus, jamais de prompt système) ;
- le stockage des liens (photographie de la conversation et du document, identifiants aléatoires et uniques, expiration, révocation d'un lien ou d'une conversation, identifiants piégés comme `../victim`, fichiers illisibles ou mal formés, nettoyage des liens expirés, dossier impossible à écrire) ;
- le serveur (page ouverte sans authentification, page identique à l'export, page du document avec un élément par ligne, HTML non exécuté, en-têtes de sécurité, feuille d'impression, même 404 pour un lien inconnu, expiré, révoqué ou abîmé, aucune autre route) ;
- le serveur lancé par la CLI, démarré pour de vrai sur un port libre (démarrage unique, liens servis, arrêt qui libère le port, `share.py` déjà lancé réutilisé, port pris par un autre programme) ;
- la configuration (`shares_dir`, `share_ttl_days`, `share_host`, `share_port`, valeurs invalides) ;
- les commandes `/export`, `/doc`, `/share`, `/share doc` et `/unshare` dans la CLI, dont un lien créé par `/share` puis ouvert sur le serveur, le démarrage du serveur des liens par `/share` seulement, son échec signalé sans perdre le lien, et son arrêt à la sortie.

## Fichiers

[src/export.py](../src/export.py) (Markdown, JSON, rendu HTML) · [src/pdf_export.py](../src/pdf_export.py) (PDF) · [src/sharing.py](../src/sharing.py) (liens, expiration, révocation, page) · [src/share_server.py](../src/share_server.py) (route `/share`) · [share.py](../share.py) (lancement du serveur) · [src/cli.py](../src/cli.py) (`/export`, `/doc`, `/share`, `/unshare`) · [src/session.py](../src/session.py) (`Session.conversation`) · [config/config.json](../config/config.json)

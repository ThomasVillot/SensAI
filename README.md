# SensAI

> Projet Epitech réalisé en groupe de 4 personnes.

Assistant de voyage qui tourne **en local** avec [Ollama](https://ollama.com). Vous décrivez votre séjour, SensAI cherche la météo, les vols, les hébergements et les activités, puis retient ce que vous décidez (dates, budget, dépenses, lieux).

Utilisable en **interface web** ou en **ligne de commande**. Aucun framework LLM : le modèle est appelé directement via l'API HTTP d'Ollama.

## Fonctionnalités

Les features suivent le catalogue du projet (code entre parenthèses ; les user stories sont dans [USER_STORIES.md](USER_STORIES.md)).

| Feature | Ce qui est implémenté |
|---|---|
| **Chatbot CLI** (B1) | Appels au modèle par l'API HTTP d'Ollama, réponse en streaming token par token, historique de session, modèle choisi par `--model` ou par la config, latence affichée à chaque tour. Les erreurs (modèle absent, Ollama injoignable, entrée vide, `Ctrl+C`) donnent un message clair, jamais un traceback. [Détails](docs/chatbot-cli.md) |
| **Boucle d'agent** (A1) | Le modèle appelle un ou plusieurs outils, lit leurs résultats, puis répond. Chaque étape est numérotée à l'écran (`[étape 2/15]`). Le nombre d'itérations est plafonné (`max_iterations`), et un modèle qui redemande trois fois le même appel est arrêté (`max_repeated_actions`), avec un message explicite. Un modèle sans support des outils bascule en chat simple. [Détails](docs/boucle-agent.md) |
| **MCP** (T1) | 14 outils exposés par un serveur MCP maison, et branchement d'un serveur MCP tiers ([voir la démo](#brancher-un-serveur-mcp-externe)). Arguments invalides et pannes d'outil sont renvoyés au modèle sans faire planter l'agent. [Détails](docs/mcp.md) |
| **Recherche web** (T3) | Recherche Tavily (basique ou approfondie), sources citées dans la réponse, résultats mis en cache 6 heures : une question déjà posée dans ce délai reste disponible sans réseau. [Détails](docs/recherche-web.md) |
| **Cache sémantique** (A6) | `--cache semantic` (ou le sélecteur de cache de l'interface web) : une question reformulée retrouve le résultat en cache, par similarité d'embeddings multilingues (`sentence-transformers`). [Détails](docs/cache-semantique.md) |
| **Sortie structurée** (T5) | Le modèle répond en JSON conforme à un schéma de [config/schemas.yaml](config/schemas.yaml) (transmis dans le champ `format` d'Ollama), validé avant d'être utilisé. Sert aux contrôles de sécurité (données personnelles, périmètre). [Détails](docs/sortie-structuree.md) |
| **Garde-fous de sécurité** (EV2) | Avant le modèle : message validé (injection, longueur), données personnelles masquées (`[EMAIL]`...), demandes hors voyage refusées. Pendant la réponse : un garde-fou de sortie coupe le tour en cas de fuite du prompt système ou de contenu dangereux. Les résultats d'outils sont nettoyés des consignes cachées. Réglé dans [config/guardrails.yaml](config/guardrails.yaml). [Détails](docs/garde-fous.md) |
| **Sessions et profils** (M1) | Chaque conversation est sauvegardée après chaque tour (`--continue`, `--session NOM`). Un profil voyageur (`--profile NOM`) ajoute budget, rythme et contraintes au prompt système. [Détails](docs/sessions-et-profils.md) |
| **Mémoire du voyage** (M3) | Le modèle écrit ce qui est décidé (destination, dates, budget, dépenses, lieux retenus ou refusés) dans une base SQLite, avec validation avant écriture. « Combien il me reste ? » est un calcul SQL. Un lieu refusé disparaît des résultats de recherche d'activités. [Détails](docs/memoire-du-voyage.md) |
| **Document d'itinéraire** (M4) | Un document séparé de la conversation, que le modèle met à jour section par section (`document_edit`) sans jamais le réécrire en entier. Une édition invalide est refusée et le document reste intact. `/doc` l'affiche ou l'exporte en Markdown, PDF ou JSON ; l'interface web le montre dans un panneau. [Détails](docs/document-itineraire.md) |
| **Journal et métriques** (EV3) | Chaque tour est journalisé (prompt, réponse, outils, latence, tokens, statut). `--stats` ou `/stats` affichent volume, taux d'erreur, latence (moyenne, médiane, p95), usage des outils et évolution par jour, modèle et version de prompt. [Détails](docs/journal-et-metriques.md) |
| **Interface web** (X1) | Même cœur que la CLI derrière une API FastAPI et un front React : streaming, appels d'outils visibles, choix du modèle (avec installation en un clic), du profil et du cache, reprise des conversations, bouton Arrêter, panneau du document d'itinéraire, prompt système, export et partage. [Détails](docs/interface-web.md) |
| **Export et partage** (X3) | `/export` écrit la conversation en Markdown, JSON ou PDF, avec les outils appelés et leurs sources ; `/doc` fait de même pour le document d'itinéraire. `/share` (conversation) et `/share doc` (document) créent un lien public en lecture seule (identifiant aléatoire de 256 bits, expiration à 7 jours), servi par un petit serveur que la CLI démarre elle-même (ou `python share.py`, pour des liens qui survivent à la CLI) ; `/unshare` le révoque. L'interface web a les mêmes actions en boutons. [Détails](docs/export-et-partage.md) |

Tout est configurable dans [config/](config/) : modèles, prompts, outils, profils, garde-fous. Chaque feature a sa page dans [docs/](docs/README.md).

## Installation

### Prérequis

**Python 3.10 ou plus récent** (testé avec 3.12).

| Système | Installation |
|---|---|
| Windows | installateur de [python.org](https://www.python.org/downloads/) ; sur le premier écran, cocher **Add Python to PATH** |
| macOS | installateur de [python.org](https://www.python.org/downloads/macos/) |
| Linux (Debian, Ubuntu) | `sudo apt install python3 python3-venv` |

Vérifier l'installation (la commande s'appelle souvent `python3` sous Linux et macOS) :

```bash
python --version
```

**Ollama**, qui fait tourner le modèle sur votre machine. Les installateurs sont sur [ollama.com/download](https://ollama.com/download) :

- **Linux** : `curl -fsSL https://ollama.com/install.sh | sh`
- **macOS 14 (Sonoma) ou plus récent** : télécharger `Ollama.dmg`, glisser l'application dans `Applications`, puis la lancer.
- **Windows 10 (22H2) ou plus récent** : lancer `OllamaSetup.exe` (sans droits administrateur), ou en PowerShell : `irm https://ollama.com/install.ps1 | iex`.

Vérifier qu'Ollama est installé et qu'il tourne :

```bash
ollama --version
```

Ouvrir ensuite http://localhost:11434 dans un navigateur : la page affiche « Ollama is running ». Sinon, lancer `ollama serve` dans un terminal et le laisser ouvert. SensAI contacte Ollama à cette adresse, réglable avec `ollama_host` dans `config/config.json`.

**Node.js 18 ou plus** (testé avec 22), pour l'interface web uniquement. npm est installé avec lui. Les téléchargements sont sur [nodejs.org/en/download](https://nodejs.org/en/download) :

- **Windows** : installateur `.msi`.
- **macOS** : installateur `.pkg`, ou nvm avec les commandes ci-dessous.
- **Linux** : [nvm](https://github.com/nvm-sh/nvm), la méthode proposée par la page de téléchargement :

  ```bash
  curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.8/install.sh | bash
  \. "$HOME/.nvm/nvm.sh"     # ou rouvrir le terminal
  nvm install 22
  ```

Vérifier l'installation :

```bash
node --version
npm --version
```

### Installer SensAI

```bash
# 1. Télécharger le modèle par défaut (voir config/models.yaml ; environ 5 Go)
ollama pull qwen3:8b

# 2. Dépendances Python
python -m venv .venv               # Linux et macOS : python3 -m venv .venv
source .venv/bin/activate          # Windows : .venv\Scripts\activate
pip install -r requirements.txt

# 3. Interface web (facultatif)
cd web && npm install && cd ..
```

Une fois l'environnement activé, `python` désigne celui du projet sur tous les systèmes.

> `sentence-transformers` installe `torch`, très volumineux. Pour un téléchargement plus léger, installer d'abord la version CPU : `pip install torch --index-url https://download.pytorch.org/whl/cpu`. Toutes les dépendances sont nécessaires, y compris sans cache sémantique : le serveur d'outils les importe au démarrage.

**Clés API** : créer un fichier `.env` à la racine (modèle : `.env.example`).

```
TAVILY_API_KEY=votre_cle
SERPAPI_API_KEY=votre_cle
```

| Variable | Utilisée par | Obtenir une clé |
|---|---|---|
| `TAVILY_API_KEY` | recherche web et activités | [app.tavily.com](https://app.tavily.com) |
| `SERPAPI_API_KEY` | vols et hébergements | [serpapi.com/dashboard](https://serpapi.com/dashboard) |

La météo (Open-Meteo) ne demande pas de clé. Sans `SERPAPI_API_KEY`, les vols et les hébergements renvoient une erreur, et le reste fonctionne. Sans `TAVILY_API_KEY`, `web_search_basic` a quand même renvoyé des résultats lors de nos essais (mode sans clé de la bibliothèque Tavily, limites non mesurées). Une clé invalide, comme `change-me` de `.env.example`, fait échouer ces recherches : remplacez-la, ou laissez-la vide.

## Utilisation

### Interface web

```bash
python web.py            # API sur http://127.0.0.1:8765
cd web && npm run dev    # interface sur http://localhost:5173
```

Pour un seul serveur, compiler le front une fois (`cd web && npm run build`) : `python web.py` sert alors l'interface sur http://127.0.0.1:8765.

Depuis l'en-tête, on choisit le **modèle** (un modèle proposé mais absent s'installe depuis le menu), le **profil voyageur** et le **cache** des recherches web. Les appels d'outils s'affichent au fil de l'eau, un bouton **Arrêter** interrompt une réponse, et la liste des conversations permet de reprendre une session. Le menu **Exporter** télécharge la conversation (Markdown, JSON, PDF) ou crée un lien de partage ; le bouton **Document** ouvre le document d'itinéraire (téléchargement, partage, effacement du voyage) ; **Prompt système** montre ce qui est envoyé au modèle. `python web.py` accepte `--model`, `--cache` et `--config` comme la CLI.

### Ligne de commande

```bash
python sensai.py
```

| Option | Effet |
|---|---|
| `--model NOM` | force un modèle Ollama pour les outils et la réponse (les contrôles de sécurité gardent `default_pii_check_model`) |
| `--profile NOM` | applique le profil `config/profiles/NOM.json` |
| `--session NOM` | conversation nommée : reprise si elle existe, créée sinon |
| `--continue` | reprend la dernière conversation |
| `--cache naif\|semantic` | type de cache de la recherche web |
| `--stats` | affiche les métriques puis quitte (Ollama non requis) |
| `--config FICHIER` | autre fichier de configuration JSON |

Dans la conversation :

| Saisie | Effet |
|---|---|
| un message | le modèle répond, en appelant des outils si besoin |
| `/new` ou `/nouveau` ou `/new_chat` | repart d'une conversation vierge, sans relancer le programme (l'ancienne reste sauvegardée) |
| `/clear` | efface toute la mémoire du voyage (voyage, dépenses, lieux, document) après confirmation, pour repartir sur un nouveau voyage |
| `/doc` ou `/doc FICHIER` | affiche le document d'itinéraire, ou l'écrit dans un fichier : PDF si le nom finit par `.pdf`, JSON par `.json`, Markdown sinon (sans écraser un fichier existant) |
| `/export` ou `/export FICHIER` | affiche la conversation en Markdown, ou l'écrit dans un fichier : PDF, JSON ou Markdown selon l'extension, comme `/doc` |
| `/share` ou `/share doc` | après confirmation, crée un lien public en lecture seule vers la conversation, ou vers le document d'itinéraire (voir [Partager une conversation](#partager-une-conversation)) |
| `/unshare` ou `/unshare LIEN` | révoque tous les liens créés dans la conversation, ou seulement celui-ci |
| `/stats` | affiche les métriques des conversations passées |
| `/prompt` | affiche le prompt système envoyé au modèle (profil compris) |
| `/exit`, `/quit` ou `Ctrl+D` | quitte |

Chaque message passe d'abord par les contrôles d'entrée ([garde-fous](docs/garde-fous.md)) : un message d'injection ou hors voyage est refusé avant d'atteindre le modèle, et une donnée personnelle (e-mail, téléphone, nom...) est remplacée par un marqueur (`[EMAIL]`) avant l'envoi. Ces contrôles ajoutent deux appels au modèle par message.

Exemple : les lignes `[outil]` et `[résultat]` montrent ce que le modèle appelle et ce qu'il reçoit avant de répondre.

```
Vous> Quel temps fait-il à Lisbonne en ce moment ?
[sécurité] 1.84 s avant l'envoi au modèle
[étape 1/15]
[outil] weather(city="Lisbonne", now=true)
[résultat] {"city": "Lisbonne", "temperature": "24.6°C", "weather_condition": "Clear sky"}
[étape 2/15]
SensAI> À Lisbonne, il fait actuellement 24,6 °C avec un ciel dégagé.
```

Chaque réponse se termine par une ligne de latence (`[latence … | 1er token … | … tokens | … appels]`), cumulée sur le tour, puis par le temps total du tour (`[temps total] …`).

Quelques commandes utiles :

```bash
python sensai.py --session lisbonne --profile budget-serre   # voyage nommé, avec son profil
python sensai.py --continue                                  # reprendre là où on s'est arrêté
python sensai.py --stats                                     # tableau de bord des métriques
```

### Partager une conversation

`/share` démarre lui-même, en tâche de fond, le petit serveur des liens (lecture seule, port 8766) : rien d'autre à lancer, le lien s'ouvre tant que SensAI reste ouvert. Pour que les liens restent ouverts sans SensAI, lancer le serveur seul :

```bash
python share.py          # liens sur le port 8766 : http://<adresse IP de la machine>:8766/share/...
```

Ce serveur n'a qu'une route, ne contacte pas Ollama et n'expose rien de l'API web. Un lien expire après 7 jours (`share_ttl_days`) et s'ouvre sans authentification. Il s'ouvre depuis les appareils du même réseau : le lien contient l'adresse IP de la machine, détectée automatiquement. Avec `"share_host": "127.0.0.1"`, il ne s'ouvre que sur cette machine ([limites et sécurité](docs/export-et-partage.md)).

## Outils

Le modèle dispose de 14 outils, exposés par un serveur MCP ([src/tools/mcp_server.py](src/tools/mcp_server.py)) que l'agent lance tout seul.

| Outil | Rôle | Source |
|---|---|---|
| `weather` | météo actuelle, prévisions ou historique d'une ville | Open-Meteo |
| `search_flights` | vols | SerpAPI |
| `search_housing` | hébergements | SerpAPI |
| `search_activities` | activités d'une ville pour un mois donné | Tavily |
| `web_search_basic`, `web_search_deep` | recherche web (3 ou 5 résultats) | Tavily |
| `trip_save`, `trip_get` | créer, mettre à jour et relire le voyage en cours | SQLite (`data/trips.db`) |
| `expense_save`, `expense_delete` | ajouter, corriger ou supprimer une dépense | SQLite |
| `place_save` | noter un lieu comme retenu ou refusé | SQLite |
| `budget_summary` | budget, dépensé, estimé et reste | SQLite |
| `document_edit`, `document_get` | modifier une section du document d'itinéraire, le relire | SQLite |

### Brancher un serveur MCP externe

SensAI n'est pas limité à ses propres outils. [demo_external_mcp.py](demo_external_mcp.py) branche le serveur MCP officiel *Filesystem* sur l'architecture de l'agent :

1. crée un dossier temporaire contenant `test.txt` ;
2. lance `@modelcontextprotocol/server-filesystem` via `npx` ;
3. liste les outils qu'il expose, puis appelle `list_directory` et `read_text_file` ;
4. vérifie les résultats et affiche `OK : un MCP existant fonctionne sur l'architecture de SensAI.`

```bash
python demo_external_mcp.py
```

Prérequis : Node.js (`npx`) et une connexion réseau au premier lancement, le temps de télécharger le serveur. Ollama n'est pas nécessaire : la démo appelle les outils directement, sans passer par le modèle.

## Configuration

| Fichier | Contenu |
|---|---|
| `config/config.json` | adresse d'Ollama, timeouts, options du modèle, chemins des logs, des sessions et de la base, versions des prompts, réglages des liens de partage |
| `config/models.yaml` | modèle de l'agent, modèle de réponse et modèle des contrôles de sécurité (défaut : `qwen3:8b` pour les trois) |
| `config/prompts.yaml` | prompts système versionnés (agent, détection des données personnelles, contrôle de périmètre) |
| `config/tools.yaml` | définitions des outils envoyées au modèle |
| `config/schemas.yaml` | schémas JSON des réponses structurées |
| `config/guardrails.yaml` | garde-fous : phrases interdites, règles d'entrée et de sortie |
| `config/profiles/*.json` | un profil voyageur par fichier |

Le modèle de l'agent est choisi dans cet ordre : `--model` > `config/models.yaml` > `"model"` de `config/config.json`. Les contrôles de sécurité utilisent toujours `default_pii_check_model` de `config/models.yaml`.

Un profil est un simple JSON aux clés libres (deux exemples fournis : `budget-serre`, `gourmet`) :

```json
{ "budget_total_eur": 600, "rythme": "calme", "contraintes": ["allergique aux fruits de mer"], "instructions": "Tutoie-moi." }
```

> **Mémoire GPU** : `options.num_ctx` (16 384 dans `config.json`) est la fenêtre de contexte envoyée à Ollama, ne pas la retirer : sans elle, le prompt est tronqué en silence. Avec `qwen3:8b` elle occupe environ 8 Go ; sur une machine plus modeste, baisser la valeur (8 192 suffit au prompt de départ) ou prendre un modèle plus léger ([configuration](docs/configuration.md#la-fenêtre-de-contexte-num_ctx)).

## Tests

```bash
python -m unittest discover -s tests -t .
```

Les tests utilisent un faux client Ollama : ni Ollama ni clé API nécessaires.

## Structure

```
sensai.py                 point d'entrée de la ligne de commande
web.py                    point d'entrée de l'interface web (FastAPI, port 8765)
share.py                  serveur des liens de partage seul (port 8766), pour des liens qui survivent à la CLI
demo_external_mcp.py      démo : branche un serveur MCP externe
src/
├── cli.py                arguments, boucle de conversation, commandes /
├── agent.py              boucle d'agent, exécution des outils MCP
├── config.py             chargement et validation de config/
├── model_funcs.py        appels au modèle, données personnelles, périmètre, sortie structurée
├── injection_patterns.py validation du message, motifs d'injection
├── output_guard.py       garde-fou de sortie (fuite du prompt, contenu dangereux)
├── ollama_client.py      seul module qui parle à Ollama
├── session.py            sauvegarde et reprise des conversations
├── export.py             export de la conversation en Markdown et en JSON, rendu HTML
├── pdf_export.py         export PDF de la conversation et du document d'itinéraire
├── sharing.py            liens de partage : création, expiration, révocation, page
├── share_server.py       serveur des liens de partage (une seule route)
├── user_profile.py       profils voyageur
├── metrics.py            métriques sur le journal des interactions
├── tools/                outils (serveur MCP, mémoire du voyage, recherches)
└── web/server.py         API de l'interface web
web/                      interface React + Vite
config/                   configuration, prompts, outils, profils
bench/                    benchmarks de modèles
tests/                    tests unittest
```

## Pour aller plus loin

- [docs/README.md](docs/README.md) : une page par feature (ce que ça fait, pourquoi, comment, limites, tests)
- [docs/garde-fous.md](docs/garde-fous.md) : validation des messages, données personnelles, périmètre, garde-fou de sortie
- [docs/sessions-et-profils.md](docs/sessions-et-profils.md) : sauvegarde et reprise des conversations, profils voyageur
- [docs/memoire-du-voyage.md](docs/memoire-du-voyage.md) : outils et base SQLite du voyage, validation, budget
- [docs/document-itineraire.md](docs/document-itineraire.md) : document d'itinéraire édité section par section, format d'édition, limites
- [docs/journal-et-metriques.md](docs/journal-et-metriques.md) : journal des interactions, tableau de bord, latence
- [docs/export-et-partage.md](docs/export-et-partage.md) : export Markdown, JSON et PDF, liens de partage, sécurité du lien public
- [docs/interface-web.md](docs/interface-web.md) : interface web et routes de l'API
- [USER_STORIES.md](USER_STORIES.md) : les fonctionnalités vues du côté utilisateur
- [bench/README.md](bench/README.md) : comparatif des modèles Ollama et justification du choix de `qwen3:8b`

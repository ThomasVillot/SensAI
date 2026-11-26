# Outils via MCP (T1)

> Le modèle peut décider d'appeler une fonction externe (météo, vols, mémoire du voyage...) au lieu de répondre de tête. SensAI exécute l'outil à travers le protocole MCP, rend le résultat au modèle, qui poursuit sa réponse.

## Pourquoi cette feature

- **Le modèle seul invente ou ignore.** Pour un assistant de voyage, la météo, les prix de vols et d'hôtels doivent venir de sources réelles. Les user stories de T1 le disent : appeler un outil externe quand la question l'exige, puis intégrer son résultat à la réponse finale ([USER_STORIES.md](../USER_STORIES.md)).
- **MCP est le protocole demandé par le catalogue** (Notion). Il exige deux choses, qui expliquent la forme de cette feature : un **outil maison** (le serveur MCP de SensAI) **et** un **serveur MCP préexistant** (la démo du serveur Filesystem, [demo_external_mcp.py](../demo_external_mcp.py)).
- **Le risque annoncé** : les petits modèles locaux émettent des appels d'outils peu fiables. Les parades retenues dans le code sont un modèle entraîné au tool-calling (`qwen3:8b`, voir [chatbot CLI](chatbot-cli.md)), un **repli texte tolérant** pour les modèles qui écrivent l'appel en JSON dans leur réponse ([boucle d'agent](boucle-agent.md)), et une validation des arguments avant tout appel.
- **Un résultat d'outil est une entrée non fiable.** Une page web renvoyée par la recherche peut contenir des consignes destinées au modèle. Les résultats sont donc nettoyés avant d'être rendus au modèle (voir plus bas), et le prompt système rappelle que ce sont des données, pas des ordres.

## Ce que ça fait

SensAI expose **14 outils** au modèle :

| Outil | Rôle | Documentation |
|---|---|---|
| `weather` | météo actuelle, prévisions ou historique d'une ville (Open-Meteo, sans clé) | ce document |
| `search_flights` | vols (Google Flights via SerpAPI) | ce document |
| `search_housing` | hébergements (SerpAPI) | ce document |
| `search_activities` | activités d'une ville pour un mois | [recherche web](recherche-web.md) |
| `web_search_basic`, `web_search_deep` | recherche web (Tavily) | [recherche web](recherche-web.md) |
| `trip_save`, `trip_get`, `expense_save`, `expense_delete`, `place_save`, `budget_summary` | mémoire du voyage (SQLite) | [mémoire du voyage](memoire-du-voyage.md) |
| `document_edit`, `document_get` | document d'itinéraire, modifié section par section (SQLite) | [document d'itinéraire](document-itineraire.md) |

## Comment ça marche

### Le serveur MCP de SensAI

[src/tools/mcp_server.py](../src/tools/mcp_server.py) enregistre chaque fonction d'outil avec `mcp.tool()(fonction)`. L'agent le lance tout seul : `connect_mcp` démarre un sous-processus `python -m tools.mcp_server --cache <mode>` avec `src/` comme dossier courant, et dialogue avec lui sur son entrée et sa sortie standard (protocole stdio). Le client est gardé pour toute la session.

### Deux déclarations pour un outil

Un outil existe à deux endroits, et c'est voulu :

- le **code** (la fonction enregistrée dans le serveur MCP) dit ce qui s'exécute ;
- [config/tools.yaml](../config/tools.yaml) dit **ce que le modèle voit** : nom, description, paramètres, champs obligatoires, valeurs autorisées.

L'agent n'exécute **que les outils déclarés dans `tools.yaml`**, même si le serveur en propose d'autres. Les deux déclarations doivent rester d'accord (mêmes paramètres, mêmes champs obligatoires) ; des tests le vérifient pour tous les outils.

### Exécuter un outil (`execute_tool`)

Dans [src/agent.py](../src/agent.py), pour chaque appel demandé par le modèle :

1. le nom existe-t-il sur le serveur ? Sinon : `{"error": "tool name invalide: '...'"}` ;
2. est-il déclaré dans `tools.yaml` ? Sinon : `{"error": "tool inexistant"}` ;
3. les arguments respectent-ils le schéma (objet JSON, champs obligatoires, types simples) ? Sinon : une erreur claire (`champs manquants : ['city']`) **sans appeler l'outil**. Des arguments donnés sous forme de chaîne JSON sont acceptés et décodés ;
4. appel de l'outil, puis **neutralisation des consignes cachées** : dans chaque texte renvoyé, tout passage qui ressemble à une injection de prompt (« ignore tes instructions », « révèle ton prompt système »...) est remplacé par `[DONNEE_NEUTRALISEE]` ([garde-fous](garde-fous.md)) ;
5. conversion du résultat MCP :

| Résultat MCP | Ce que reçoit le modèle |
|---|---|
| un texte qui est du JSON | un objet décodé |
| un texte brut | une chaîne |
| plusieurs blocs de texte | une liste de textes |
| aucun texte | `{"result": "..."}` |
| marqué en erreur (`is_error`) | `{"error": "<message du serveur>"}` |
| exception pendant l'appel | `{"error": "l'outil '...' a échoué : ..."}` |

Aucun de ces cas ne fait planter l'agent : le modèle reçoit l'erreur comme résultat d'outil et peut se corriger.

Le dernier cas de conversion mérite une explication. Quand un outil plante côté serveur, MCP ne lève pas d'exception chez le client : il répond normalement avec un drapeau d'erreur. Sans le traitement de `is_error`, le message d'erreur arrivait comme un texte ordinaire, et le journal ([journal et métriques](journal-et-metriques.md)) comptait l'appel comme réussi.

### Ajouter un outil

1. Écrire la fonction dans `src/tools/` (renvoyer un dict, avec `{"error": ...}` pour les échecs attendus).
2. L'enregistrer dans `src/tools/mcp_server.py` : `mcp.tool()(ma_fonction)`.
3. La déclarer dans `config/tools.yaml` : nom, description, paramètres, `required`.
4. Ajouter son nom à `TOOL_NAMES` dans `src/tools/handler.py` (pour le repli texte).
5. Dire au modèle quand l'utiliser dans le prompt système (`config/prompts.yaml`).

Le catalogue visait « ajouter un outil par configuration, sans modifier `src/` ». Ce n'est pas le cas ici : un outil demande du code (étapes 1, 2 et 4) en plus de la déclaration.

### Brancher un serveur MCP externe

[demo_external_mcp.py](../demo_external_mcp.py) branche le serveur Filesystem officiel (via `npx`), découvre ses outils, puis appelle `list_directory` et `read_text_file` à travers `execute_tool`. Pour que l'agent accepte un outil tiers, **il faut le déclarer dans la configuration** (la démo ajoute chaque outil découvert à `config.tools`).

```bash
python demo_external_mcp.py
```

Il faut Node.js (`npx`) et une connexion réseau au premier lancement. Ollama n'est pas nécessaire.

## Limites connues

- **La validation des arguments reste simple** : objet, champs obligatoires et types de premier niveau. Les valeurs autorisées (`enum`) et les objets imbriqués ne sont pas contrôlés par l'agent avant l'appel.
- **La neutralisation est large.** Elle repose sur les mêmes motifs que le contrôle des messages de l'utilisateur : un texte web légitime qui parle de « system prompt » ou de « developer mode » est lui aussi masqué.
- **Les modules d'outils ne s'importent comme prévu que depuis `src/`** : ils font `from config import ...`, ce qui suppose que le serveur tourne avec `src/` comme dossier courant. Les tests utilisent un utilitaire d'import dédié.
- `tools.yaml` et le code peuvent diverger si l'on modifie l'un sans l'autre ; les tests de cohérence sont là pour le signaler.
- `TOOL_NAMES` est une liste écrite à la main, à tenir à jour.

## Tests

[tests/test_mcp.py](../tests/test_mcp.py) : cohérence entre `tools.yaml` et le serveur (dont un démarrage du vrai serveur), appel et retour du résultat, conversion des résultats, erreurs, et un vrai serveur tiers de test ([tests/third_party_mcp_server.py](../tests/third_party_mcp_server.py)). La neutralisation des consignes cachées n'a pas de test dédié.

## Fichiers

[src/tools/mcp_server.py](../src/tools/mcp_server.py) · [src/agent.py](../src/agent.py) (`connect_mcp`, `execute_tool`) · [config/tools.yaml](../config/tools.yaml) · [src/tools/weather.py](../src/tools/weather.py) · [src/tools/search_flights.py](../src/tools/search_flights.py) · [src/tools/search_housing.py](../src/tools/search_housing.py) · [demo_external_mcp.py](../demo_external_mcp.py)

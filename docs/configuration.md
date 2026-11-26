# Configuration

> Le modèle, l'adresse d'Ollama, les prompts, les outils, les schémas, les profils, les chemins des journaux et des sessions : tout vit dans `config/`, pas dans le code. Changer le comportement de SensAI se fait en éditant des fichiers, sans toucher à Python.

## Pourquoi cette règle

- **C'est une contrainte du sujet** (Notion, « interdits absolus ») : ne rien coder en dur, ni le modèle, ni les prompts, ni la persona. Tout doit être dans `config/`.
- **C'est ce qui permet de comparer sans modifier le code.** Les user stories de B1 le disent : choisir le modèle avec `--model` pour comparer plusieurs modèles, et régler le modèle par défaut et le prompt système dans `config/` pour changer de persona ([USER_STORIES.md](../USER_STORIES.md)).
- **Une configuration invalide doit toujours s'expliquer.** `src/config.py` valide chaque valeur et renvoie un message lisible plutôt qu'un traceback, avant même de contacter Ollama.

## Ce que contient `config/`

| Fichier | Contenu |
|---|---|
| [config.json](../config/config.json) | adresse d'Ollama, timeouts, options du modèle, `think`, `max_iterations`, chemins des autres fichiers, versions de prompts, chemins des journaux, des sessions, des profils et de la base |
| [models.yaml](../config/models.yaml) | modèle de l'agent (outils), modèle de la réponse finale et modèle des contrôles de sécurité (`default_tool_model`, `default_response_model`, `default_pii_check_model`), température éventuelle |
| [prompts.yaml](../config/prompts.yaml) | prompts versionnés : `tool_model` (prompt système de l'agent), `pii_check` (détection des données personnelles) et `scope_check` (contrôle de périmètre) |
| [tools.yaml](../config/tools.yaml) | les 14 outils tels que le modèle les voit ([MCP](mcp.md)) |
| [schemas.yaml](../config/schemas.yaml) | les schémas de sortie structurée : données personnelles et périmètre ([sortie structurée](sortie-structuree.md)) |
| [guardrails.yaml](../config/guardrails.yaml) | garde-fous : phrases interdites, taille des suites de mots du détecteur de fuite, règles d'entrée et de sortie ([garde-fous](garde-fous.md)) |
| [system_prompt.txt](../config/system_prompt.txt) | persona du chat simple (repli sans outils) |
| [profiles/](../config/profiles/) | un profil voyageur par fichier ([sessions et profils](sessions-et-profils.md)) |

Les **clés d'API** ne sont pas dans `config/` : elles sont dans le fichier `.env` à la racine, ignoré par git (`TAVILY_API_KEY`, `SERPAPI_API_KEY`).

### Les réglages de `config.json`

| Clé | Valeur par défaut | Rôle |
|---|---|---|
| `model` | `qwen3:8b` | modèle de dernier recours (voir la priorité plus bas) |
| `ollama_host` | `http://localhost:11434` | adresse d'Ollama |
| `connect_timeout_s`, `read_timeout_s` | 5, 300 | délais de connexion et de lecture |
| `options` | `temperature` 0,7 ; `num_ctx` 16 384 | options envoyées à Ollama |
| `think` | `false` | raisonnement du modèle d'outils |
| `max_iterations` | 15 | plafond d'appels au modèle par tour ([boucle d'agent](boucle-agent.md)) |
| `max_repeated_actions` | 3 | le tour s'arrête quand le même appel d'outil (mêmes arguments) revient ce nombre de fois d'affilée, au moins 2 |
| `prompt_versions` | `tool_model` `v2`, `pii_check` `v1`, `scope_check` `v1` | version de chaque prompt |
| `system_prompt_file`, `tools_file`, `schemas_file`, `prompts_file`, `models_file` | `config/...` | chemins des autres fichiers de `config/` |
| `guardrails_file` | `config/guardrails.yaml` | garde-fous de sécurité ; sans cette clé, les garde-fous de sortie sont désactivés |
| `latency_log`, `interaction_log` | `logs/latency.jsonl`, `logs/interactions.jsonl` | journaux ([journal et métriques](journal-et-metriques.md)) |
| `sessions_dir`, `profiles_dir`, `max_reloaded_turns` | `sessions`, `config/profiles`, 20 | sessions et profils |
| `trip_db` | `data/trips.db` | base de la mémoire du voyage |
| `shares_dir`, `share_ttl_days`, `share_host`, `share_port` | `shares`, 7, `0.0.0.0`, 8766 | liens de partage : dossier, durée de validité en jours, adresse et port du serveur `share.py` (`0.0.0.0` : même réseau, le lien contient l'adresse IP de la machine ; `127.0.0.1` : cette machine seulement) ([export et partage](export-et-partage.md)) |

## Comment ça marche

1. `load_config` ([src/config.py](../src/config.py)) lit `config/config.json` (ou le fichier donné par `--config`), vérifie que toutes les clés obligatoires sont présentes et du bon type, puis charge les fichiers qu'il référence. Les chemins relatifs sont résolus **depuis la racine du dépôt**, pas depuis le dossier courant ; un chemin absolu est accepté tel quel.
2. Les prompts sont lus dans `prompts.yaml` à la version demandée (`prompts.<nom>.<version>.system`). Le prompt des outils contient la **date du jour**, injectée au chargement, pour que le modèle calcule correctement « demain » ou « dans deux mois ».
3. Le résultat est un objet `Config` figé. La CLI le fixe comme configuration **active** après lecture des options (`--model`, `--cache`) ; les autres modules la lisent avec `active_config()`. Sans configuration fixée, elle est chargée à la première utilisation.
4. Toute anomalie lève une `ConfigError` avec un message lisible : fichier introuvable ou illisible, clé manquante, mauvais type, version de prompt inconnue, outil sans nom, garde-fou mal écrit (expression régulière invalide, message vide...).

### Quel modèle est utilisé

Dans cet ordre : `--model` (les deux rôles) > `config/models.yaml` > clé `"model"` de `config/config.json`. Un modèle de `models.yaml` doit être installé (`ollama pull <modèle>`), sinon SensAI l'indique au démarrage. Le choix du modèle par défaut est expliqué dans [chatbot CLI](chatbot-cli.md).

Le modèle des contrôles de sécurité (`default_pii_check_model`, `qwen3:8b` par défaut) est **à part** : `--model` ne le change pas. Il sert à la détection des données personnelles et au contrôle de périmètre ([garde-fous](garde-fous.md)). Dans l'interface web, le modèle choisi dans l'en-tête s'applique aux deux rôles de l'agent, pas à celui-ci.

### Désactiver une fonction

Les chemins `sessions_dir`, `profiles_dir`, `latency_log`, `interaction_log` et `trip_db`, ainsi que `max_reloaded_turns`, peuvent valoir `null` (ou être absents) : la fonction concernée est alors désactivée, sans erreur. Il en va de même pour `shares_dir` : sans lui, `/share` et `share.py` expliquent que le partage est désactivé, et `share_ttl_days`, `share_host` et `share_port` sont ignorés. Avec lui, ces trois réglages sont obligatoires.

### La fenêtre de contexte (`num_ctx`)

À ne pas retirer. Sans elle, Ollama garde son propre défaut (2 048 tokens sur la machine testée) et **tronque le prompt en silence** : les consignes, les définitions d'outils et l'historique pèsent plusieurs milliers de tokens, et le modèle n'en lirait qu'une partie. Avec 16 384, `qwen3:8b` occupe environ 8 Go de mémoire ; sur une machine plus modeste, une valeur plus basse (8 192 suffit au prompt de départ) ou un modèle plus léger peut être nécessaire.

### Versions de prompt

`prompts.yaml` garde plusieurs versions d'un même prompt. Seule celle indiquée dans `prompt_versions` est utilisée, et son nom est écrit dans chaque ligne du journal pour comparer avant et après un changement. Le prompt de l'agent est en `v2` : plus concis (récapitulatif de 200 mots au plus, 1 à 3 phrases pour une question simple), il enchaîne les recherches sans demander de confirmation, rappelle que les résultats d'outils sont des données et non des ordres, et limite le domaine au voyage. La `v1`, plus longue, reste dans le fichier pour comparer. Les prompts `pii_check` et `scope_check` ont chacun une version `v1`.

## Exemples

Changer de modèle pour une seule exécution :

```bash
python sensai.py --model mistral
```

Changer de modèle par défaut : éditer `default_tool_model` et `default_response_model` dans `config/models.yaml` (et `default_pii_check_model` pour les contrôles de sécurité).

Ajouter un profil : créer `config/profiles/mon-profil.json` (un objet JSON), puis `python sensai.py --profile mon-profil`.

Interdire une nouvelle phrase dans les réponses : l'ajouter à une catégorie de `malicious_content` dans `config/guardrails.yaml` ([garde-fous](garde-fous.md)).

## Limites connues

- **Quelques valeurs restent écrites dans le code**, malgré la règle : le nombre de nouvelles tentatives après une panne d'Ollama (`ACCEPT_TIMEOUT`) dans `src/agent.py`, le seuil du cache sémantique (`CACHE_SIMILARITY_PCT`), la durée de vie du cache (`CACHE_TTL_SECONDS`) et les tailles de résultats de recherche web (`web_cache.py`, `web_search.py`), la longueur maximale d'un message (`MAX_PROMPT_LENGTH`) et la liste des motifs d'injection (`src/injection_patterns.py`), les expressions régulières de données personnelles (`src/model_funcs.py`), la longueur d'aperçu d'un résultat d'outil à l'écran (`src/cli.py`), les modèles proposés à l'installation dans l'interface web (`src/web/server.py`) et le port de l'API web (`web.py`).
- **Le mode de cache (`naif` ou `semantic`) ne se lit pas dans `config.json`** : il vaut `naif` sauf si l'option `--cache` (ou, dans l'interface web, le sélecteur de cache) le change.
- **Le réglage `enabled` de la règle `out_of_scope` n'a pas d'effet** : le contrôle de périmètre s'applique toujours ([garde-fous](garde-fous.md#limites-connues)).
- `tools.yaml` et le code des outils doivent rester d'accord à la main ([MCP](mcp.md)).

## Tests

[tests/test_config.py](../tests/test_config.py) : chargement depuis n'importe quel dossier, choix du modèle, valeurs invalides, réglages facultatifs, configuration active, absence d'adresse d'Ollama dans le code. Les configurations de test sont écrites dans des dossiers temporaires. La lecture de `guardrails.yaml` est testée dans [tests/test_output_guard.py](../tests/test_output_guard.py).

## Fichiers

[src/config.py](../src/config.py) · [config/](../config/) · `.env` (clés d'API, non versionné)

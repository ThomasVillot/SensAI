# Documentation de SensAI

SensAI est un assistant de voyage qui tourne en local avec Ollama. Cette documentation a **une page par feature**. Chacune répond aux mêmes questions : ce que fait la feature, pourquoi elle existe, comment elle fonctionne, quelles sont ses limites, et quels tests la couvrent.

Pour installer et lancer le projet, voir le [README](../README.md).

## Vue d'ensemble

```
 terminal ──▶ sensai.py (src/cli.py)          web.py (src/web/server.py, FastAPI) ◀── navigateur (React)
                       │                                      │
                       └──────────────────┬───────────────────┘
                                          ▼
                    contrôles d'entrée : validation · données personnelles · périmètre
                                          ▼
                              src/agent.py : boucle d'agent ──▶ garde-fou de sortie
                         ┌────────────────┴─────────────────┐
              API HTTP d'Ollama (requests)        protocole MCP (stdio)
                         ▼                                    ▼
                modèle local (Ollama)          serveur d'outils (src/tools/mcp_server.py)
                                       météo · vols · hôtels · recherche web · mémoire du voyage

 tout ce qui se règle : config/      conversations : sessions/      mémoire du voyage : data/trips.db
 journaux : logs/                    liens de partage : shares/
```

La CLI et l'interface web partagent le même cœur : les contrôles d'entrée, la boucle d'agent, les garde-fous, les sessions, les profils et la configuration.

## Les features

| Feature | Code | Page | Code principal | Tests |
|---|---|---|---|---|
| Chatbot CLI et client Ollama | B1 | [chatbot-cli.md](chatbot-cli.md) | [src/cli.py](../src/cli.py), [src/ollama_client.py](../src/ollama_client.py) | [test_chatbot_cli.py](../tests/test_chatbot_cli.py), [test_ollama_client.py](../tests/test_ollama_client.py) |
| Boucle d'agent | A1 | [boucle-agent.md](boucle-agent.md) | [src/agent.py](../src/agent.py) | [test_agent_loop.py](../tests/test_agent_loop.py) |
| Outils via MCP | T1 | [mcp.md](mcp.md) | [src/tools/mcp_server.py](../src/tools/mcp_server.py) | [test_mcp.py](../tests/test_mcp.py) |
| Recherche web | T3 | [recherche-web.md](recherche-web.md) | [src/tools/web_search.py](../src/tools/web_search.py) | [test_web_search.py](../tests/test_web_search.py) |
| Cache sémantique | A6 | [cache-semantique.md](cache-semantique.md) | [src/tools/web_cache.py](../src/tools/web_cache.py) | [test_semantic_cache.py](../tests/test_semantic_cache.py) |
| Sortie structurée | T5 | [sortie-structuree.md](sortie-structuree.md) | [src/model_funcs.py](../src/model_funcs.py), [src/utils.py](../src/utils.py) | [test_structured_output.py](../tests/test_structured_output.py) |
| Garde-fous de sécurité | EV2 | [garde-fous.md](garde-fous.md) | [src/injection_patterns.py](../src/injection_patterns.py), [src/model_funcs.py](../src/model_funcs.py), [src/output_guard.py](../src/output_guard.py) | [test_output_guard.py](../tests/test_output_guard.py), [test_pii_redaction.py](../tests/test_pii_redaction.py), [test_scope_check.py](../tests/test_scope_check.py) |
| Sessions et profils | M1 | [sessions-et-profils.md](sessions-et-profils.md) | [src/session.py](../src/session.py), [src/user_profile.py](../src/user_profile.py) | [test_sessions_profiles.py](../tests/test_sessions_profiles.py) |
| Mémoire du voyage | M3 | [memoire-du-voyage.md](memoire-du-voyage.md) | [src/tools/trip_memory.py](../src/tools/trip_memory.py) | [test_trip_memory.py](../tests/test_trip_memory.py) |
| Document d'itinéraire (`/doc`) | M4 | [document-itineraire.md](document-itineraire.md) | [src/tools/trip_document.py](../src/tools/trip_document.py) | [test_itinerary_document.py](../tests/test_itinerary_document.py) |
| Journal, métriques et latence | EV3 | [journal-et-metriques.md](journal-et-metriques.md) | [src/interaction_log.py](../src/interaction_log.py), [src/metrics.py](../src/metrics.py) | [test_journal_metrics.py](../tests/test_journal_metrics.py) |
| Interface web | X1 | [interface-web.md](interface-web.md) | [src/web/server.py](../src/web/server.py), [web/](../web/) | [test_web_ui.py](../tests/test_web_ui.py) |
| Export et partage (`/export`, `/share`) | X3 | [export-et-partage.md](export-et-partage.md) | [src/export.py](../src/export.py), [src/pdf_export.py](../src/pdf_export.py), [src/sharing.py](../src/sharing.py), [src/share_server.py](../src/share_server.py) | [test_export_share.py](../tests/test_export_share.py) |
| Configuration | (règle du projet) | [configuration.md](configuration.md) | [src/config.py](../src/config.py), [config/](../config/) | [test_config.py](../tests/test_config.py) |

## Dans le catalogue, mais pas dans le projet

Le catalogue de features du projet contient d'autres features, qui ne sont pas implémentées dans le dépôt : **X4** (questions de clarification) et la partie édition et branches de **X2** (seul l'arrêt d'une réponse existe, via le bouton Arrêter). Il n'y a donc pas de page pour elles.

## D'où vient le « pourquoi »

La section « Pourquoi » de chaque page s'appuie sur les sources du projet : le catalogue de features (Notion), [USER_STORIES.md](../USER_STORIES.md) et, pour le choix du modèle, [bench/README.md](../bench/README.md). Quand une raison n'est écrite nulle part, la page le dit (« d'après le code ») au lieu de l'inventer.

## Lancer les tests

Les tests suivent la même découpe que cette documentation : un ou plusieurs fichiers par feature.

```bash
python -m unittest discover -s tests -t .
```

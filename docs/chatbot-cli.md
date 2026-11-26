# Chatbot CLI et client Ollama (B1)

> Un assistant en ligne de commande qui parle à un modèle local via Ollama, répond en streaming, se souvient de la conversation et ne plante jamais devant l'utilisateur. C'est le socle sur lequel toutes les autres features sont posées.

## Pourquoi cette feature

- **C'est le socle obligatoire du projet.** Le catalogue de features (Notion) la classe « Socle » : elle ne rapporte aucun point à elle seule, mais si elle casse, toutes les features posées dessus cassent avec elle. Elle devait être finie en semaine 1.
- **Les contraintes du sujet l'ont dictée :** Python 3.10+, aucun framework LLM (pas de LangChain ni équivalent), et un modèle appelé uniquement par l'**API HTTP d'Ollama**. C'est pourquoi [src/ollama_client.py](../src/ollama_client.py) est un petit client écrit avec `requests`, sans bibliothèque LLM, et pourquoi rien n'est codé en dur : modèle, adresse d'Ollama et prompts vivent dans [config/](../config/) ([voir la configuration](configuration.md)).
- **Cinq observables à passer sans exception** (Notion) : connexion à Ollama, historique de session, réponse en streaming token par token, modèle changeable sans coder, erreurs gérées sans traceback. Les user stories correspondantes sont dans [USER_STORIES.md](../USER_STORIES.md).
- **La latence devait être mesurée dès le début**, car elle contraint tout le reste : une boucle d'agent multiplie les appels au modèle par 3 à 5 par question ([voir la boucle d'agent](boucle-agent.md)).

## Ce que ça fait

```bash
python sensai.py                    # démarre la conversation
python sensai.py --model qwen3:8b   # force un modèle pour les outils et la réponse
```

| Saisie | Effet |
|---|---|
| un message | le modèle répond en streaming, en appelant des outils si besoin |
| `/new`, `/nouveau` ou `/new_chat` | nouvelle conversation vierge, sans quitter ([sessions et profils](sessions-et-profils.md)) |
| `/clear` | efface la mémoire du voyage après confirmation ([mémoire du voyage](memoire-du-voyage.md)) |
| `/doc` ou `/doc FICHIER` | affiche le document d'itinéraire, ou l'écrit dans un fichier sans écraser ([document d'itinéraire](document-itineraire.md)) |
| `/export` ou `/export FICHIER` | affiche la conversation en Markdown, ou l'écrit en Markdown, JSON ou PDF ([export et partage](export-et-partage.md)) |
| `/share`, `/share doc`, `/unshare` | crée ou révoque un lien public en lecture seule ([export et partage](export-et-partage.md)) |
| `/stats` | tableau de bord des interactions ([journal et métriques](journal-et-metriques.md)) |
| `/prompt` | affiche le prompt système envoyé au modèle |
| `/exit`, `/quit` ou `Ctrl+D` | quitte |

Les options de lancement (`--model`, `--profile`, `--session`, `--continue`, `--cache`, `--stats`, `--config`) sont décrites dans le [README](../README.md#ligne-de-commande).

Ce que l'utilisateur voit :

- avant chaque tour, les contrôles d'entrée ([garde-fous](garde-fous.md)) : un message refusé (`[sécurité]`, `[scope check]`), des données personnelles masquées (`[PII check detection]`), puis la durée de ces contrôles (`[sécurité] 1.84 s avant l'envoi au modèle`) ;
- la réponse s'écrit au fil de l'eau après le préfixe `SensAI>` ;
- chaque passage dans la boucle de l'agent est numéroté (`[étape 2/15]`, le total est `max_iterations`) ;
- chaque appel d'outil s'affiche (`[outil] weather(city="Paris")`) avec son résultat (`[résultat] {...}`) ;
- chaque tour se termine par une ligne de latence, `[latence 4.80 s | 1er token 2.10 s | 120 tokens | 2 appels]`, suivie du temps total du tour (`[temps total] ...`, contrôles d'entrée compris), et la moyenne est donnée à la fermeture ;
- les lignes vides sont ignorées, `Ctrl+C` interrompt le tour en cours sans quitter le programme.

## Comment ça marche

### Un tour de conversation

```
saisie ─▶ cli.repl ─▶ contrôles d'entrée ─▶ run_agent_turn ─▶ agent.run_agent ─▶ model_funcs ─▶ OllamaClient.chat_events ─▶ POST /api/chat (Ollama)
                                                    │                                                                          │
                                                    └── affiche les événements (token, outil, résultat) ◀──────────────────────┘
```

1. [src/cli.py](../src/cli.py) lit la saisie (`repl`), la valide, traite les commandes `/...`, sinon fait passer le message par la détection de données personnelles et le contrôle de périmètre ([garde-fous](garde-fous.md)), puis lance un tour d'agent (`run_agent_turn`).
2. [src/agent.py](../src/agent.py) joue la boucle et émet des événements (`step`, `token`, `tool_call`, `tool_result`, `guardrail`, `done_tool`, `error`) que la CLI affiche ([détails](boucle-agent.md)).
3. [src/ollama_client.py](../src/ollama_client.py) envoie la requête en streaming à `POST /api/chat` et renvoie les morceaux de texte au fur et à mesure, avec les mesures de latence (durée totale, délai avant le premier token, tokens générés, temps de chargement du modèle).
4. L'historique est gardé par une `Session` ([src/session.py](../src/session.py)). **Un tour n'y entre que s'il a abouti** : une erreur, le plafond d'itérations, une réponse bloquée par un garde-fou ou un `Ctrl+C` n'ajoutent rien, pour qu'un échec ne pollue pas les tours suivants.

### Choix du modèle

Priorité : `--model` (les deux rôles) > `config/models.yaml` > clé `"model"` de `config/config.json`. Le modèle par défaut est `qwen3:8b`, avec `think: false` dans `config.json`.

Ce choix vient du benchmark maison ([bench/README.md](../bench/README.md)) : `qwen3:8b` sans raisonnement est le seul modèle du panel sans aucun échec en appel d'outils, et couper le raisonnement divise sa latence par 5 (34,3 s → 6,5 s par appel) sans dégrader le tool-calling. Le benchmark précise ses limites : triage sur une seule machine, une seule passe.

### Gestion des erreurs

La règle du fichier est : aucune exception ne doit remonter jusqu'à l'utilisateur sous forme de traceback.

| Situation | Ce qui se passe |
|---|---|
| modèle absent d'Ollama | message avec la commande `ollama pull <modèle>` et la liste des modèles installés, code de sortie 1 |
| Ollama injoignable au lancement | message qui nomme l'adresse et suggère `ollama serve`, code de sortie 1 |
| connexion coupée en plein tour | `[erreur] Connexion avec Ollama perdue...`, le tour est abandonné, la conversation continue |
| Ollama injoignable à l'envoi d'un message | `[erreur] Impossible de joindre Ollama...`, le message n'est pas conservé, la conversation continue |
| délai dépassé (300 s par défaut) | message de délai, même traitement |
| `Ctrl+C` pendant un tour | `[interrompu]`, la conversation reste ouverte |
| message refusé avant l'envoi (injection, trop long, hors sujet, données personnelles si la règle est sur `refuse`) | `[sécurité]`, `[scope check]` ou `[PII check]`, la conversation continue ([garde-fous](garde-fous.md)) |
| réponse bloquée par un garde-fou de sortie | `[sécurité] Réponse bloquée : ...`, le tour n'est pas conservé |
| exception inattendue dans un tour | `[erreur] erreur inattendue (...)`, le programme continue |
| modèle qui refuse les outils (HTTP 400) | repli en chat simple, avec un avertissement ([détails ci-dessous](#repli-en-chat-simple)) |

### Repli en chat simple

Si Ollama répond que le modèle ne gère pas les outils (ou le paramètre `think`), le client retire l'option de la requête, prévient une fois, et refait l'appel. Pour les outils, la conversation continue ensuite sans outils avec le prompt du chat simple ([config/system_prompt.txt](../config/system_prompt.txt)), et le profil voyageur éventuel est conservé.

## Limites connues

- **La machine fixe le rythme.** Sur un GPU de faible mémoire, `qwen3:8b` ne tient qu'en partie en VRAM et la génération ralentit nettement. Une première question peut dépasser le délai de 300 s si le modèle doit se charger.
- **Chaque message paie deux appels au modèle avant l'agent** (données personnelles, périmètre) : la CLI affiche cette durée avant le tour ([garde-fous](garde-fous.md#limites-connues)).
- **`num_ctx` (16 384) ne doit pas être retiré** : sans lui, Ollama tronque le prompt en silence ([configuration](configuration.md)).
- Le streaming s'affiche token par token, mais rien n'est bufferisé ni reformaté : une réponse très longue défile telle quelle.

## Tests

- [tests/test_chatbot_cli.py](../tests/test_chatbot_cli.py) : arguments, affichage d'un tour, historique, saisie (dont le refus d'une injection), erreurs, repli en chat simple.
- [tests/test_ollama_client.py](../tests/test_ollama_client.py) : streaming, mesures de latence, contenu de la requête, erreurs HTTP et réseau, options refusées.

## Fichiers

[sensai.py](../sensai.py) (point d'entrée) · [src/cli.py](../src/cli.py) · [src/ollama_client.py](../src/ollama_client.py) · [src/model_funcs.py](../src/model_funcs.py) · [src/injection_patterns.py](../src/injection_patterns.py) · [src/output_guard.py](../src/output_guard.py) · [src/session.py](../src/session.py) · [src/latency.py](../src/latency.py)

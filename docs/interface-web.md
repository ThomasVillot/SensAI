# Interface web (X1)

> Un navigateur à la place du terminal : la même conversation, avec la réponse qui s'écrit en direct, les appels d'outils visibles, le choix du modèle (avec installation en un clic), du profil et du cache, la reprise des anciennes conversations, le panneau du document d'itinéraire, l'export et le partage. Le cœur de l'agent n'est pas dupliqué : l'interface parle au même code que la CLI.

## Pourquoi cette feature

- **Une démonstration se suit mieux dans un navigateur que dans un terminal.** Le catalogue de features (Notion) est lucide : au barème des étoiles, c'est « le plus mauvais rendement du catalogue » (trois jours pour une étoile), mais à l'évaluation de la démarche c'est peut-être le meilleur investissement, parce que la démonstration est notée sur sa fluidité. À prendre « consciemment ». La checklist d'avancement ajoute un effet de bord voulu : l'interface existe avant la première répétition, donc la démonstration se joue dedans.
- **Un utilisateur non technique ne veut pas ouvrir un terminal** (première user story de X1 dans le catalogue).
- **Le mot qui compte est « découplé ».** Si la logique était réécrite dans le front, il y aurait deux systèmes à maintenir. Ici, l'API appelle les mêmes fonctions que la CLI (contrôles d'entrée, `run_agent`, sessions, profils, export, partage, configuration), et la CLI reste utilisable.
- **Le piège identifié** : une interface « simple » dérape toujours en temps (streaming dans le navigateur, état, erreurs, mise en page). Le catalogue recommande de borner le budget.

## Ce que ça fait

L'en-tête regroupe les réglages et les actions :

| Élément | Rôle |
|---|---|
| **Modèle** (menu) | modèles « Conseillé », « Proposés » et « Déjà installés » ; un modèle proposé mais absent d'Ollama s'installe depuis le menu, après confirmation, avec le pourcentage de téléchargement |
| **Profil** | profil voyageur ([sessions et profils](sessions-et-profils.md)) ; verrouillé dès que la conversation contient un message |
| **Cache** | cache des recherches web, naïf ou sémantique ([cache sémantique](cache-semantique.md)) |
| **Nouvelle conversation** | repart d'une conversation vierge |
| **Exporter** | Markdown, JSON, PDF ou lien de partage de la conversation ([export et partage](export-et-partage.md)) ; actif une fois qu'un tour est terminé |
| **Document** | ouvre le panneau du document d'itinéraire |
| **Prompt système** | affiche le prompt envoyé au modèle, profil compris |

Dans la conversation :

- La réponse s'affiche **token par token**, avec les appels d'outils intercalés (un bloc repliable par outil, « SensAI utilise weather », qui montre son résultat sous forme de liste). Tant que la réponse n'est pas terminée, « Étape n/N » indique où en est la boucle d'agent ([boucle d'agent](boucle-agent.md)).
- Sous chaque réponse : latence, tokens et nombre d'appels au modèle.
- Le bouton **Arrêter** interrompt la réponse en cours ; ce tour n'est pas conservé dans l'historique et le texte tapé revient dans la zone de saisie.
- Une réponse **bloquée par un garde-fou** de sortie apparaît dans un encadré d'alerte avec le message de sécurité, à la place du texte déjà affiché ([garde-fous](garde-fous.md)).
- La liste des **conversations** sauvegardées permet de rouvrir une conversation ; l'application dit combien d'échanges ont été rechargés, et combien d'anciens ne le sont pas.
- Si le modèle choisi ne sait pas utiliser les outils, un avertissement le dit.
- Les avertissements de configuration (par exemple `parameters.stream: false` ignoré dans `models.yaml`) s'affichent quelques secondes au chargement. Les confirmations (modèle installé, lien copié) aussi.

Le **panneau « Document du voyage »** affiche le document d'itinéraire ([document d'itinéraire](document-itineraire.md)) et se met à jour tout seul quand le modèle appelle `document_edit`. Il propose les téléchargements Markdown, JSON et PDF, **Partager** (lien public, voir plus bas) et **Effacer le voyage** (efface la mémoire du voyage après confirmation, comme `/clear` dans la CLI : [mémoire du voyage](memoire-du-voyage.md)). Le panneau « Prompt système », le panneau Document et le panneau du lien de partage s'excluent : un seul est ouvert à la fois.

## Comment ça marche

```
navigateur (React + Vite) ──fetch──▶ API FastAPI (web.py → src/web/server.py) ──▶ contrôles d'entrée ──▶ run_agent ──▶ Ollama / outils MCP
```

### Lancer

```bash
python web.py            # API sur http://127.0.0.1:8765
cd web && npm run dev    # interface sur http://localhost:5173
```

`web.py` accepte les mêmes options que la CLI pour le modèle et le cache (`--model`, `--cache`, `--config`) ; les autres (`--profile`, `--session`, `--continue`, `--stats`) sont comprises mais sans effet.

En développement, le serveur de Vite transmet les requêtes `/api` à l'API (proxy dans [web/vite.config.js](../web/vite.config.js)). Pour un seul serveur, `cd web && npm run build` produit `web/dist`, que l'API sert elle-même sur `http://127.0.0.1:8765`. L'API n'écoute que sur la boucle locale.

`web.py` vérifie au démarrage que les modèles existent dans Ollama (comme la CLI) ; au démarrage de l'application, il connecte une fois le serveur d'outils ([MCP](mcp.md)).

### L'API

| Route | Rôle |
|---|---|
| `GET /api/health` | le serveur tourne |
| `GET /api/warnings` | avertissements de configuration |
| `GET /api/models` | modèle par défaut, modèles conseillés, proposés et installés |
| `POST /api/models/install` | installe un modèle proposé, en flux de progression ; un autre nom est refusé (400) |
| `GET /api/profiles` | profils voyageur disponibles |
| `GET /api/prompt?profile=` | prompt système, avec le profil éventuel |
| `GET /api/cache`, `POST /api/cache` | mode de cache actuel ; changement de mode (redémarre le serveur d'outils, refusé en cours de réponse : 409) |
| `POST /api/conversations` | crée une conversation (renvoie son identifiant) |
| `GET /api/conversations` | liste les conversations sauvegardées, la plus récente d'abord |
| `GET /api/conversations/{id}` | rouvre une conversation et renvoie ses messages |
| `POST /api/chat` | joue un tour, en flux d'événements |
| `GET /api/conversations/{id}/export?format=md\|json\|pdf` | télécharge la conversation |
| `POST /api/conversations/{id}/share` | crée un lien public vers la conversation |
| `GET /api/document` | document d'itinéraire en Markdown |
| `GET /api/document/export?format=md\|json\|pdf` | télécharge le document |
| `POST /api/document/share` | crée un lien public vers le document |
| `DELETE /api/shares/{token}` | révoque un lien |
| `DELETE /api/trip` | efface la mémoire du voyage |

Les modèles proposés à l'installation sont une liste écrite dans [src/web/server.py](../src/web/server.py) (`qwen3:8b` conseillé ; `mistral-nemo:12b`, `qwen2.5:7b`, `mistral:latest`, `granite3.1-moe:latest` et `llama3.1:8b` proposés).

### Avant le flux : les contrôles d'entrée

`POST /api/chat` applique, avant de répondre en flux, les mêmes contrôles que la CLI ([garde-fous](garde-fous.md)) : validation du message, masquage des données personnelles, contrôle de périmètre. Un message refusé reçoit une erreur HTTP 400 avec le motif, et une panne d'Ollama pendant le contrôle de périmètre une erreur 502. Le message vide et le profil inconnu sont aussi des 400.

### Le flux de la réponse

`/api/chat` répond en **Server-Sent Events** (`text/event-stream`) : une suite de blocs `data: {...}` séparés par une ligne vide. Les événements sont ceux de l'agent ([boucle d'agent](boucle-agent.md)) : `step` (numéro de l'étape de la boucle), `token`, `tool_call`, `tool_result`, `done_tool`, `error`, `guardrail` (réponse bloquée : le flux s'arrête, sans statistiques), plus `no_tools` si le modèle ne gère pas les outils et un événement `stats` en fin de tour (latence, tokens, nombre d'appels).

Côté navigateur, [web/src/api.js](../web/src/api.js) lit la réponse avec `fetch` et un lecteur de flux, découpe sur les lignes vides, et passe chaque événement à `App.jsx`, qui met à jour le dernier message. Le bouton Arrêter utilise un `AbortController` ; le serveur y laisse la possibilité de s'apercevoir de la déconnexion entre deux événements.

### Partager depuis le navigateur

**Lien de partage** (menu Exporter) ou **Partager** (panneau Document) demandent confirmation (« Créer un lien public vers ... ? »), puis affichent le lien dans un panneau avec sa date d'expiration, un bouton pour le copier et un bouton pour le **révoquer**. Le serveur démarre lui-même le petit serveur des liens ; s'il ne peut pas (port pris), l'API renvoie un avertissement qui invite à lancer `python share.py`. Voir [export et partage](export-et-partage.md).

### Ce qui est partagé avec la CLI, et ce qui ne l'est pas

| Partagé | Propre à l'interface web |
|---|---|
| contrôles d'entrée, boucle d'agent, appels d'outils, garde-fous | flux SSE, composants React |
| sessions (même dossier, même format), profils | choix du modèle (avec installation) et du profil dans l'en-tête |
| export, partage, document d'itinéraire, effacement du voyage | boutons, panneaux, liste des conversations |
| configuration | choix du cache dans l'en-tête |

Le modèle choisi dans l'en-tête sert aux deux rôles (outils et réponse). Le profil est appliqué à chaque message.

### Une seule génération à la fois

Un verrou global (`generation_lock`) n'autorise qu'une génération en cours. Un message envoyé pendant ce temps reçoit un événement `error` (« Une réponse est déjà en cours... »), un changement de cache reçoit 409. Le verrou est libéré à la fin du tour, même en cas d'erreur.

## Limites connues

- **Un message refusé par les contrôles d'entrée s'affiche mal.** Le front présente l'erreur 400 comme « Le serveur ne répond pas (...) », et la bulle de l'utilisateur reste dans la conversation.
- **Le masquage des données personnelles est silencieux** : le texte masqué part au modèle sans que l'interface le dise (la CLI affiche `[PII check detection]`).
- **Pas d'indicateur de consommation de contexte** : la deuxième user story du catalogue demande de voir en direct les tokens utilisés et le budget de contexte. L'interface n'affiche que la latence, les tokens et les appels de chaque réponse.
- **Les tours web ne sont pas journalisés** : pas de ligne dans `logs/interactions.jsonl` ni dans `logs/latency.jsonl`, donc pas dans `/stats` ([journal et métriques](journal-et-metriques.md)).
- **Un seul utilisateur, une seule génération**, pas d'authentification (l'API reste sur la boucle locale). Toutes les conversations d'un même serveur partagent la même mémoire du voyage.
- **Changer de cache coupe le serveur d'outils** et le relance : le changement prend quelques secondes (plus si le modèle sémantique doit se charger).
- **Le comportement sur une réponse très longue** (l'observable « 2 000 mots » du catalogue) n'a pas été vérifié.
- **Pas de test automatique du front** : le projet n'a pas d'outillage de test JavaScript. Côté API, seules les routes de conversation, de modèles, de profils et de chat sont testées ; l'installation de modèle, le cache, le prompt, le document, l'export, le partage et l'effacement du voyage n'ont pas de test d'API (les fonctions qu'elles appellent, elles, sont testées dans les pages correspondantes).
- Le port de l'API (8765) est écrit dans `web.py` et dans `vite.config.js`, pas dans la configuration.

## Tests

[tests/test_web_ui.py](../tests/test_web_ui.py) teste l'API avec le client de test de FastAPI : santé, modèles, profils, conversations (création, liste, réouverture, identifiants uniques), flux d'événements, outils, modèle et profil choisis, sauvegarde, erreurs, génération déjà en cours. Le serveur n'est pas lancé, Ollama est remplacé par un faux modèle et les conversations sont écrites dans un dossier temporaire. L'événement `guardrail` du flux est testé dans [tests/test_output_guard.py](../tests/test_output_guard.py).

## Fichiers

[web.py](../web.py) · [src/web/server.py](../src/web/server.py) · [web/src/App.jsx](../web/src/App.jsx) · [web/src/api.js](../web/src/api.js) · [web/src/history.js](../web/src/history.js) · [web/src/components/](../web/src/components/) · [web/vite.config.js](../web/vite.config.js)

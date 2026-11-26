# Recherche web (T3)

> Quand une information n'est pas dans le contexte, le modèle peut lancer une recherche web en direct, lire les résultats et les intégrer à sa réponse en citant ses sources. Les résultats sont mis en cache pour que la démonstration fonctionne sans réseau.

## Pourquoi cette feature

- **Un modèle local ne connaît pas l'actualité.** Les user stories de T3 ([USER_STORIES.md](../USER_STORIES.md)) demandent de chercher quand l'information n'est pas disponible dans le contexte, puis d'intégrer les résultats à la conversation pour répondre à partir de ce qui a été trouvé.
- **Les sources doivent être vérifiables.** Le catalogue de features (Notion) fixe le palier à atteindre : parsing propre, **sources citées avec leur URL**, erreurs réseau gérées, requête pilotée par le modèle. C'est pourquoi chaque résultat garde son adresse et que le prompt demande de citer ses sources.
- **Le réseau est le risque numéro un.** Le catalogue le dit sans détour : le réseau de l'école le jour de la soutenance échappe à tout contrôle, donc « cachez systématiquement les résultats » et rendez la démonstration rejouable hors ligne. Le cache et le message « Connexion perdue » répondent à ce risque.
- **Pourquoi Tavily** : c'est un service conçu pour les agents IA. Les résultats arrivent déjà parsés, denses en information pour peu de tokens, et le service protège contre les injections de prompt et les sources malveillantes.
- **Pourquoi deux outils** : `web_search_basic` pour une question simple (3 résultats), `web_search_deep` quand il faut creuser (5 résultats, recherche approfondie). Le modèle choisit selon la question.

## Ce que ça fait

| Outil | Demande à Tavily | Contenu gardé par résultat |
|---|---|---|
| `web_search_basic` | 3 résultats, profondeur « basic » | 500 caractères |
| `web_search_deep` | 5 résultats, profondeur « advanced » | 1 500 caractères |
| `search_activities` | comme `web_search_deep`, avec la requête `Activities in <Ville> in <Mois>`, suivie de `: <préférences>` si le modèle en donne | 1 500 caractères |

Chaque résultat rendu au modèle contient trois champs : `title`, `url`, `content`. Les autres champs de Tavily (comme le score) sont retirés pour économiser des tokens.

`search_activities` a un paramètre facultatif `preferences` : ce que l'utilisateur aime, en mots-clés positifs et en anglais (« adventure sports, outdoor »). La description de l'outil demande de ne jamais y écrire de négation : un goût négatif se traduit par son contraire. L'outil **retire aussi des résultats les lieux que l'utilisateur a refusés** (titres et phrases qui les citent) et ajoute en tête une note qui les signale au modèle : voir [mémoire du voyage](memoire-du-voyage.md#lieux-refusés-dans-les-recherches-dactivités).

## Comment ça marche

Tout est dans [src/tools/web_search.py](../src/tools/web_search.py), exécuté par le serveur d'outils ([MCP](mcp.md)).

```
question ─▶ cache ? ──oui──▶ résultat du cache (aucun appel réseau)
              │
              non
              ▼
         Tavily ──erreur réseau──▶ "Connexion perdue" (rien n'est mis en cache)
              │
              ▼
   titre + url + contenu tronqué ─▶ ajouté au cache ─▶ rendu au modèle
```

1. **Cache d'abord.** Le mode est choisi par l'option `--cache` ou, dans l'interface web, par le sélecteur de cache (`naif` par défaut, `semantic` pour le [cache sémantique](cache-semantique.md)). La question est d'abord mise en minuscules et débarrassée de ses espaces de bord ; le cache simple ne reconnaît ensuite que la **même question, au caractère près**. Une entrée du cache **expire au bout de 6 heures** (`CACHE_TTL_SECONDS`).
2. **Appel Tavily** avec la clé `TAVILY_API_KEY` (fichier `.env`, voir le [README](../README.md)). Sans clé, la bibliothèque Tavily passe en mode sans clé : `web_search_basic` a renvoyé des résultats lors de deux essais. `web_search_deep` et `search_activities` n'ont pas été essayés ainsi, et les limites de ce mode ne sont pas mesurées.
3. **Mise en forme et stockage** : titre, adresse, contenu tronqué. Le résultat est enregistré dans un fichier de cache JSON, `.web_cache_naif.json`, écrit **dans le dossier courant du serveur d'outils, c'est-à-dire `src/`** (ignoré par git).
4. **Panne réseau** : l'outil renvoie la chaîne `Connexion perdue` et écrit un avertissement sur stderr qui suggère le mode `semantic`. Le prompt système explique au modèle quoi faire : arrêter les appels, utiliser ce qu'il a, ou dire qu'il ne peut pas continuer.

Une question déjà posée reste disponible **sans réseau pendant 6 heures** : le cache est consulté avant d'appeler Tavily. Passé ce délai, l'entrée est supprimée et la question repart sur le réseau.

Les textes renvoyés au modèle passent ensuite par la neutralisation des consignes cachées ([MCP](mcp.md), [garde-fous](garde-fous.md)).

## Limites connues

- **Aucun tri des résultats** : les 3 ou 5 premiers sont gardés. Une page très longue est simplement tronquée, pas résumée.
- **« Résultats hors sujet → l'agent le dit »** (observable du catalogue) n'est pas géré par le code : seul le prompt demande de distinguer faits vérifiés et estimations.
- Une recherche qui échoue pour une autre raison qu'une coupure réseau (clé refusée, par exemple `change-me` de `.env.example`) n'a pas de message dédié : l'erreur remonte comme panne d'outil ([MCP](mcp.md)).
- **Le cache ne tient que 6 heures** : une démonstration hors ligne doit être préparée le jour même.

## Tests

[tests/test_web_search.py](../tests/test_web_search.py) : ce qui est demandé à Tavily, résultats rendus au modèle, cache simple (dont le mode hors ligne), panne réseau, option `--cache`. Aucun test n'appelle le vrai Tavily : le client est remplacé par un faux et les fichiers de cache sont écrits dans un dossier temporaire.

## Fichiers

[src/tools/web_search.py](../src/tools/web_search.py) · [src/tools/web_cache.py](../src/tools/web_cache.py) · [src/tools/search_activities.py](../src/tools/search_activities.py) · [config/tools.yaml](../config/tools.yaml)

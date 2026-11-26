# Cache sémantique (A6)

> Une recherche web déjà faite sur une question de même sens, même formulée autrement, est retrouvée dans le cache au lieu de rappeler Tavily.

## Pourquoi cette feature

- **Le cache simple rate les reformulations.** « Quelle est la capitale de la France ? » et « Quelle est la ville qui est la capitale française ? » ont le même sens mais pas les mêmes caractères. Le catalogue de features (Notion) demande d'apparier les requêtes par **similarité d'embeddings**, pour réduire la latence et la charge sur les demandes répétitives.
- **La latence est le problème de fond du projet.** Une question déjà posée « répond instantanément » ; le catalogue y voit aussi un recours si la latence dérape, et un argument de démonstration.
- **Statut dans le catalogue : « Could »**, c'est-à-dire en réserve, et un rendement à égalité du meilleur du catalogue (une étoile par jour d'effort). Il est implémenté dans le dépôt.

> **Ce qui est réellement mis en cache.** Le catalogue décrit un cache des réponses du modèle. L'implémentation actuelle met en cache les **résultats de la recherche web** ([recherche web](recherche-web.md)) : une question reformulée évite un appel à Tavily, pas un appel au modèle.

## Ce que ça fait

```bash
python sensai.py --cache semantic
```

Avec ce mode, quand le modèle lance une recherche web, SensAI compare le sens de la question à celui des questions déjà en cache. Si l'une est assez proche, il rend son résultat sans réseau.

Dans l'interface web, le sélecteur « Cache » de l'en-tête change de mode à chaud (`web.py --cache semantic` le fixe au lancement). Le changement redémarre le serveur d'outils et prend quelques secondes ([interface web](interface-web.md)).

## Comment ça marche

Le code est dans [src/tools/web_cache.py](../src/tools/web_cache.py) et dans `web_search` ([src/tools/web_search.py](../src/tools/web_search.py)).

1. **Un modèle transforme chaque question en vecteur** (un *embedding*) : `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, via la bibliothèque `sentence-transformers`. Ce modèle est multilingue : il rapproche aussi une question en français et sa reformulation. Il est chargé **au premier besoin seulement**, une fois par processus, et le temps de chargement est annoncé sur stderr (`[load semantic cache model]`). Le premier chargement télécharge le modèle depuis Internet.
2. **À chaque recherche**, la question (mise en minuscules) est encodée et comparée, par **similarité cosinus**, à toutes les entrées du cache. L'entrée la plus proche gagne.
3. **Seuil : 0,80** (`CACHE_SIMILARITY_PCT`). En dessous, c'est un échec de cache et Tavily est appelé. À partir de 0,80, le résultat enregistré est rendu.
4. **Sur un échec de cache**, le résultat de Tavily est enregistré avec le vecteur de la question dans `.web_cache_semantic.json` (dans `src/`, ignoré par git).
5. **Durée de vie : 6 heures** (`CACHE_TTL_SECONDS`). À chaque recherche, les entrées plus anciennes sont supprimées du fichier avant la comparaison.
6. Une entrée sans vecteur ou abîmée est ignorée avec un message, sans empêcher de lire les autres.

Le mode `semantic` utilise son propre fichier : il ne touche pas au cache simple.

## Limites connues

- **Le seuil est écrit dans le code** (0,80) et n'est pas réglable depuis `config/`, alors que le catalogue le demande. Il n'a pas été calibré sur des paires de questions mesurées.
- **Le risque principal est un faux positif** : deux questions proches par le vocabulaire mais différentes par le sens (le catalogue donne l'exemple de deux verbes à conjuguer). Aucun taux de faux positifs n'est mesuré.
- **Aucune mesure de taux de succès du cache** (« hit rate ») n'est affichée. Les entrées expirent seules après 6 heures, mais on ne peut pas invalider une entrée précise : il faut supprimer le fichier.
- **Coût de démarrage** : charger `sentence-transformers` tire `torch`, volumineux. C'est pourquoi la bibliothèque est importée par le serveur d'outils même sans le mode `semantic` ([README](../README.md)).
- Le contexte de la conversation n'entre pas dans la comparaison : seule la question de recherche compte.

## Tests

[tests/test_semantic_cache.py](../tests/test_semantic_cache.py) remplace le vrai modèle par un faux encodeur dont les vecteurs sont choisis : on sait exactement quelle similarité a chaque paire de questions. Les tests vérifient le seuil (0,81 accepté, 0,79 refusé), l'entrée la plus proche, la robustesse, le chargement unique du modèle, et le cas d'usage complet (une reformulation est servie par le cache). Un autre test vérifie qu'une entrée de plus de 6 heures est ignorée et retirée du fichier. Le vrai modèle n'est pas testé, car il faut le télécharger.

## Fichiers

[src/tools/web_cache.py](../src/tools/web_cache.py) · [src/tools/web_search.py](../src/tools/web_search.py)

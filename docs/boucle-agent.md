# Boucle d'agent (A1)

> Le modèle ne répond plus une seule fois : il peut demander un outil, lire le résultat, décider de la suite, et recommencer jusqu'à pouvoir répondre. C'est ce qui transforme un chatbot avec des outils en un agent.

## Pourquoi cette feature

- **Sans boucle, un outil n'est qu'une fonction appelée une fois.** Le catalogue de features (Notion) l'écrit ainsi : A1 « donne du sens à tous les outils » ([MCP](mcp.md), [recherche web](recherche-web.md), [mémoire du voyage](memoire-du-voyage.md)). Avec la boucle, le système observe le résultat, décide, recommence. C'est le cœur du sujet (*Intelligent Agent Systems*).
- **Pour le voyage, une question demande presque toujours plusieurs étapes** : « Rome du 12 au 15 mars » appelle la météo, les vols, l'hébergement et les activités, puis enregistre les décisions. Une user story résume l'idée : enchaîner les recherches nécessaires sans que l'utilisateur décompose lui-même sa question.
- **Le piège identifié dès le départ** : la latence est multipliée par 3 à 5, et une boucle infinie en démonstration serait fatale. D'où le plafond d'itérations, présent « dès la première ligne de code » (consigne du catalogue).

## Ce que ça fait

Pour un message de l'utilisateur, l'agent :

1. envoie la conversation et la liste des outils au modèle ;
2. si le modèle demande un ou plusieurs outils, les exécute, ajoute leurs résultats à la conversation, et rappelle le modèle ;
3. s'arrête quand le modèle répond sans demander d'outil, ou qu'un garde-fou le coupe : le plafond d'itérations, le même appel d'outil redemandé d'affilée, ou le garde-fou de sortie qui bloque la réponse ([garde-fous](garde-fous.md)).

Chaque passage dans la boucle (un appel au modèle) est une **étape**, numérotée à l'écran. Exemple, tel que la CLI l'affiche (les deux lignes d'outil viennent d'une vraie exécution ; la phrase finale est illustrative) :

```
Vous> Quel temps fait-il à Lisbonne en ce moment ?
[étape 1/15]
[outil] weather(city="Lisbonne", now=true)
[résultat] {"city": "Lisbonne", "temperature": "24.6°C", "weather_condition": "Clear sky"}
[étape 2/15]
SensAI> À Lisbonne, il fait actuellement 24,6 °C avec un ciel dégagé.
```

Le cycle se lit ainsi : l'étape 1 est l'**action** (le modèle demande l'outil), la ligne `[résultat]` est l'**observation**, et l'étape 2 est la **décision** de conclure. Le total (`/15`) est le plafond `max_iterations`.

## Comment ça marche

La boucle est la fonction `run_agent` de [src/agent.py](../src/agent.py), un générateur asynchrone qui produit des événements : `step` (début d'une étape : `n` et `max`), `token` (morceau de texte), `tool_call` (outil demandé), `tool_result` (résultat), `guardrail` (réponse bloquée par un garde-fou de sortie : `kind`, `category`, `message`), `error`, `done_tool` (réponse finale).

```
iterations = 0
tant que vrai :
    si iterations >= max_iterations : événement "error" (plafond atteint), fin
    iterations += 1, événement "step"
    appeler le modèle avec la conversation + les outils
        chaque morceau de texte passe par le garde-fou de sortie avant d'être émis :
        s'il y a violation : événement "guardrail", fin (sans nouvel essai)
    si le modèle demande des outils (tool_calls) :
        pour chaque outil :
            si c'est le même appel (nom + arguments) que le précédent, max_repeated_actions fois d'affilée :
                événement "error" (action répétée), fin
            exécuter via MCP (résultat nettoyé des consignes cachées), noter le résultat dans la conversation
        recommencer
    sinon, si le texte contient un appel d'outil écrit en JSON (repli texte) : même traitement
    sinon : réponse finale, sortir
```

### Plafond d'itérations

`max_iterations` (15 dans `config/config.json`, obligatoire, entier ≥ 1) borne le nombre d'appels au modèle d'un tour. Au plafond, l'agent s'arrête avec un message qui annonce le chiffre et la clé à modifier, et **le tour n'est pas conservé** dans la conversation.

### Arrêt sur action répétée

Le plafond ne protège qu'à retardement : un modèle qui redemande sans fin le même outil brûle les 15 étapes (jusqu'à plusieurs minutes de latence) avant de s'arrêter. `max_repeated_actions` (3 dans `config/config.json`, obligatoire, entier ≥ 2) coupe plus tôt : quand le modèle demande le **même outil avec les mêmes arguments** ce nombre de fois d'affilée, l'agent s'arrête avec un message qui nomme l'outil, ses arguments et la clé à régler. Le dernier appel n'est ni affiché ni exécuté, et **le tour n'est pas conservé**.

- Deux appels sont « les mêmes » si le nom et les arguments sont identiques, quel que soit l'ordre des clés.
- Un appel différent entre deux appels identiques remet le compte à zéro (A, A, B, A, A ne s'arrête pas).
- Le compte vaut aussi à l'intérieur d'une seule réponse du modèle qui demande trois fois le même appel.
- Dans le journal, le tour est noté avec le statut `error` et le message d'arrêt ([journal et métriques](journal-et-metriques.md)).

### Tour conservé seulement s'il réussit

Le tour est joué sur une copie des messages ; il n'entre dans la `Session` qu'à la fin, d'un seul bloc (question, appels d'outils, résultats, réponse finale). Une panne, `Ctrl+C`, le plafond ou une réponse bloquée par un garde-fou laissent la conversation exactement comme avant.

### Garde-fou de sortie

À chaque appel au modèle, le texte produit est vérifié avant d'être émis (fuite du prompt système, contenu malveillant). Une violation arrête le tour tout de suite, sans nouvel essai, avec un événement `guardrail`. Le texte déjà affiché est du texte vérifié : le garde-fou retient les derniers mots tant qu'il n'a pas pu les contrôler. Détails dans [garde-fous](garde-fous.md).

### Résultats d'outils

Avant d'être remis au modèle, le texte d'un résultat d'outil passe par la détection d'injection : un passage qui ressemble à une consigne (« ignore tes instructions ») est remplacé par `[DONNEE_NEUTRALISEE]` ([garde-fous](garde-fous.md)).

### Pannes du modèle

Si Ollama échoue, l'agent affiche `[timeout]: ...` et réessaie, jusqu'à **3 tentatives au total** (`ACCEPT_TIMEOUT = 2` nouvelles tentatives). Après quoi il renvoie un événement `error`.

### Repli texte

Certains modèles écrivent leur appel d'outil en JSON dans le texte au lieu du champ prévu. `extract_json_in_content` ([src/utils.py](../src/utils.py)) retrouve ce JSON, et `normalize_tool_calls` le convertit en appel d'outil, à une condition : **le nom doit être celui d'un outil connu**. La liste des noms connus est `TOOL_NAMES` dans [src/tools/handler.py](../src/tools/handler.py) ; elle doit rester égale aux outils de `config/tools.yaml` (un test le vérifie). Un JSON qui n'est pas un appel d'outil, ou qui nomme un outil inconnu, est traité comme une réponse ordinaire.

## Limites connues

- **L'arrêt sur action répétée ne voit que les appels strictement identiques et consécutifs.** Un modèle qui alterne deux appels (A, B, A, B…) n'est pas détecté : c'est le plafond d'itérations qui le coupe.
- **Le compteur d'étapes s'affiche partout, mais seulement pendant le travail.** La CLI l'écrit à chaque étape (`[étape 2/15]`) ; l'interface web montre « Étape n/N » tant que la réponse n'est pas terminée, puis l'efface ([interface web](interface-web.md)).
- **Un nouvel essai rejoue tout le tour**, y compris les appels d'outils déjà faits. Si une panne survient après un `trip_save`, l'outil peut être rappelé à la reprise (les écritures de la mémoire du voyage sont conçues pour ne pas créer de doublon).
- **La latence reste le coût principal** : chaque outil ajoute un appel au modèle. La ligne de latence de la CLI cumule tous les appels du tour ([chatbot CLI](chatbot-cli.md)).
- Le choix du bon outil par le modèle n'est pas garanti ; il est mesuré dans le benchmark ([bench/README.md](../bench/README.md)), pas dans les tests.

## Tests

[tests/test_agent_loop.py](../tests/test_agent_loop.py) : plafond d'itérations, enchaînement d'outils, requête envoyée au modèle, réponse simple, pannes et `Ctrl+C`, repli texte (dont l'alignement de `TOOL_NAMES` sur `tools.yaml`), compteur d'étapes, arrêt sur action répétée. L'affichage `[étape n/N]` et le message d'arrêt côté terminal sont dans [tests/test_chatbot_cli.py](../tests/test_chatbot_cli.py). L'arrêt sur violation d'un garde-fou est dans [tests/test_output_guard.py](../tests/test_output_guard.py).

## Fichiers

[src/agent.py](../src/agent.py) · [src/model_funcs.py](../src/model_funcs.py) · [src/utils.py](../src/utils.py) · [src/tools/handler.py](../src/tools/handler.py)

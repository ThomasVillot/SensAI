# Sortie structurée (T5)

> SensAI sait demander au modèle une réponse en JSON conforme à un schéma défini, vérifier qu'elle le respecte, et ne rien accepter d'autre. Le code lit alors le résultat comme une donnée, pas comme du texte libre. Aujourd'hui, deux contrôles de sécurité s'appuient dessus : la détection des données personnelles et le contrôle de périmètre ([garde-fous](garde-fous.md)).

## Pourquoi cette feature

- **Du texte libre ne s'exploite pas.** Les deux user stories de T5 ([USER_STORIES.md](../USER_STORIES.md)) en donnent la raison : le développeur veut des informations exploitables automatiquement, et l'utilisateur veut que les réponses structurées respectent **toujours** le format attendu, pour éviter les erreurs dans la feature qui les utilise.
- **C'est le socle technique d'autres features** (Notion) : les appels d'outils sont du JSON structuré ([MCP](mcp.md)), les écritures en base doivent être contraintes ([mémoire du voyage](memoire-du-voyage.md)), et les contrôles d'entrée doivent obtenir une réponse sans ambiguïté (une liste de types de données personnelles, une classe de périmètre).
- **Le piège identifié** : le modèle entoure son JSON de texte (« Voici le JSON demandé : ... ») ou d'un bloc de code Markdown, et un `json.loads()` strict casse. La parade du catalogue tient en trois mots : **extraire** d'abord, **valider** ensuite, **réessayer** en cas d'échec (le dernier point n'est pas implémenté, voir les limites).
- **Pourquoi passer le schéma dans `format` plutôt que dans le prompt** (constat du benchmark, [bench/README.md](../bench/README.md)) : Ollama accepte un schéma JSON complet dans son champ `format`, et il est alors appliqué à la génération elle-même, comme une contrainte. Sans schéma, le modèle invente ses noms de clés et écrit « 5 jours » au lieu de l'entier 5.

## Ce que ça fait

Les schémas vivent dans [config/schemas.yaml](../config/schemas.yaml), sous la clé `schemas`. Deux sont utilisés par le code :

| Schéma | Forme de la réponse | Utilisé par |
|---|---|---|
| `personal_information` | `{"personal_information": [{"type": "EMAIL", "value": "..."}]}` ; `type` parmi `FIRST_NAME`, `LAST_NAME`, `EMAIL`, `PHONE_NUMBER`, `POSTAL_ADDRESS`, `DATE_OF_BIRTH`, `PASSPORT_NUMBER`, `CREDIT_CARD`, `BANK_ACCOUNT`, `IP_ADDRESS`, `OTHER` ; aucune autre clé acceptée | détection des données personnelles (`sanitize_user_message`) |
| `message_scope` | `{"classification": "IN_SCOPE"}` ; valeurs `IN_SCOPE`, `OUT_OF_SCOPE`, `AMBIGUOUS`, `CONTEXT_AMBIGUOUS`, `INJECTION` | contrôle de périmètre (`detect_message_scope`) |

Ajouter un schéma, c'est l'ajouter sous `schemas:` puis passer `config.schemas["son_nom"]` en `format` à `request_response_model`.

## Comment ça marche

La fonction `request_response_model` de [src/model_funcs.py](../src/model_funcs.py) :

1. envoie au modèle les messages, avec le schéma dans le champ `format` et **aucun outil** ;
2. cumule les morceaux de texte reçus, en rejetant les débris techniques (`{function`, `<nil>`...) ;
3. **extrait le JSON** de la réponse avec `extract_json_in_content` ([src/utils.py](../src/utils.py)) : le texte tel quel, sinon un bloc de code, sinon la plus longue valeur JSON trouvée dans le texte (un objet entouré de phrases est retrouvé en entier) ;
4. **valide** le résultat contre le schéma avec la bibliothèque `jsonschema` (`schema_problems`), y compris les champs imbriqués et les valeurs autorisées (`enum`) ;
5. renvoie le texte de la réponse si tout est conforme ; sinon renvoie `None`.

L'appelant décide quoi faire d'une réponse absente. La détection des données personnelles la traite comme « rien à signaler ». Le contrôle de périmètre refuse le message si la réponse n'est pas conforme, et signale une panne d'Ollama comme une erreur plutôt que comme un refus ([garde-fous](garde-fous.md)).

## Limites connues

- **Pas de nouvel essai.** Une réponse non conforme n'est pas renvoyée au modèle avec la liste de ses écarts : elle est abandonnée. La consigne « réessayer avec l'erreur réinjectée » du catalogue n'est pas implémentée dans le code actuel.
- **Les schémas sont petits** (un tableau, une énumération). Ils ne couvrent pas de structures complexes, et un schéma plus riche n'a pas été éprouvé avec `qwen3:8b`.
- **`schema_problems` ne rend que les 5 premiers écarts.**

## Tests

- [tests/test_structured_output.py](../tests/test_structured_output.py) : le schéma part dans `format` sans outil, une réponse non conforme est rejetée, et l'extracteur de JSON (texte brut, bloc de code, JSON entouré de phrases, liste, plus longue valeur gagnante, accolades après le JSON, texte sans JSON).
- [tests/test_pii_redaction.py](../tests/test_pii_redaction.py) : le schéma `personal_information` part dans `format`, la réponse est lue.
- [tests/test_scope_check.py](../tests/test_scope_check.py) : la réponse du schéma `message_scope` est lue.

## Fichiers

[src/model_funcs.py](../src/model_funcs.py) (`request_response_model`) · [src/utils.py](../src/utils.py) (`extract_json_in_content`, `schema_problems`) · [config/schemas.yaml](../config/schemas.yaml)

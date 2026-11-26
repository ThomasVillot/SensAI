# Journal des interactions, métriques et latence (EV3)

> SensAI note ce qui se passe à chaque tour de conversation, puis le résume dans un tableau de bord : latence, taux d'erreur, usage de chaque outil, et leur évolution dans le temps.

## Pourquoi cette feature

- **Voir au lieu de deviner.** Les user stories d'EV3 ([USER_STORIES.md](../USER_STORIES.md)) : suivre la latence et le taux d'erreur par jour et par version de prompt pour repérer une régression après un changement de prompt ou de modèle, et savoir quels outils servent réellement et lesquels échouent, pour corriger ou retirer ceux qui ne servent pas.
- **Il fallait la brancher tôt.** Le catalogue de features (Notion) insiste : cette feature produit des données historiques, et celles qu'on n'a pas collectées sont perdues pour toujours. Un graphique d'évolution sur plusieurs semaines est parlant ; le même sur trois jours ne dit rien.
- **Une vue, pas seulement un fichier.** Le catalogue précise qu'il faut une *vue de métriques* lisible, sinon on journalise sans jamais regarder. D'où `/stats` et `--stats`.
- **Un fichier exploitable par un script.** Une ligne JSON par tour se lit aussi bien avec la vue qu'avec un petit script.
- **La latence est la donnée qui contraint tout.** Une boucle d'agent multiplie les appels au modèle ([boucle d'agent](boucle-agent.md)) : les mesures de latence disent ce que coûte chaque feature.
- **Reste à produire** (Notion) : laisser le journal se remplir avec de vraies données pour montrer une évolution dans le temps, puis une décision technique prise grâce au tableau de bord.

> **Seule la ligne de commande alimente le journal et le log de latence.** L'interface web affiche la latence, les tokens et le nombre d'appels sous chaque réponse, mais n'écrit rien dans `logs/`. Pour remplir le tableau de bord, il faut discuter avec `python sensai.py`.

## Ce que ça fait

```bash
python sensai.py --stats     # affiche le tableau de bord puis quitte, sans lancer Ollama
```

Dans une conversation, `/stats` affiche la même vue. Sans interaction journalisée, SensAI le dit et invite à discuter d'abord. Chaque tour de la CLI affiche aussi une ligne de latence :

```
[latence 4.80 s | 1er token 2.10 s | 120 tokens | 2 appels]
```

## Comment ça marche

### Le journal

Chaque tour (message de l'utilisateur accepté par les contrôles d'entrée) ajoute une ligne JSON à `logs/interactions.jsonl` (format *JSON Lines* : une ligne égale un objet complet). Le chemin se règle avec `interaction_log` dans `config/config.json` (`null` pour désactiver). `logs/` est ignoré par git. Un message refusé avant l'envoi au modèle (injection, hors sujet...) n'est pas journalisé ([garde-fous](garde-fous.md)).

| Champ | Contenu |
|---|---|
| `ts` | date locale du tour |
| `session` | nom de la session (le même d'un lancement à l'autre, et le même que le fichier de `sessions/`), ou identifiant court de l'exécution si les sessions sont désactivées |
| `kind` | `agent` (tour avec outils) ou `chat` (modèle sans outils) |
| `model`, `prompt_version` | modèle d'outils ; version du prompt système (`prompt_versions` de `config/config.json`), pour comparer avant et après un changement de prompt |
| `prompt`, `response` | message de l'utilisateur, **tel qu'il est envoyé au modèle** (données personnelles masquées, sans accents : [garde-fous](garde-fous.md)) ; réponse finale (ou texte reçu avant l'échec) |
| `status`, `error` | `ok`, `error`, `interrupted` (`Ctrl+C`), `max_iterations` (plafond atteint) ou `blocked` (réponse bloquée par un garde-fou) ; message d'erreur éventuel, `guardrail:system_prompt_leak` ou `guardrail:malicious_content:<catégorie>` pour un blocage |
| `latency_s`, `ttft_s`, `tokens`, `model_calls` | somme des appels au modèle du tour ; délai avant le premier token ; tokens générés ; nombre d'appels au modèle |
| `wall_s` | durée réelle du tour, outils compris (l'écart avec `latency_s` est le coût des outils) |
| `tool_calls` | pour chaque outil appelé : `name`, `arguments`, `ok`, `error`, `duration_s` |

Un appel d'outil reste sans résultat (le serveur d'outils a planté) est noté comme un échec en fin de tour. Un échec d'outil est reconnu quand le résultat est un dict avec une clé `error`, ou une réponse MCP marquée en erreur ([MCP](mcp.md)). Une écriture impossible (disque plein) désactive le journal avec un avertissement, sans interrompre la conversation.

### Le tableau de bord

La vue ([src/metrics.py](../src/metrics.py)) montre :

- le volume et la répartition des tours ;
- le taux d'erreur, qui compte les statuts `error` et `max_iterations` (une interruption volontaire n'en est pas une, un blocage par un garde-fou non plus : il est compté à part, sur la ligne « bloquées ») ;
- la latence : moyenne, médiane, p95 (la durée qu'on dépasse dans 5 % des cas) et pire cas, avec les tokens et les appels au modèle par tour ;
- pour chaque outil : nombre d'appels, échecs et durée moyenne ;
- l'évolution par jour, par version de prompt et par modèle.

Les nombres sont écrits à la française (virgule décimale). Les lignes illisibles du journal sont ignorées, avec un message qui indique combien.

### La latence

- La **latence** est le temps entre l'envoi de la requête et le dernier token reçu. Pour un tour d'agent, les valeurs sont cumulées sur tous les appels au modèle.
- Chaque tour ajoute aussi une ligne JSON à `logs/latency.jsonl` (`latency_log` dans `config/config.json`) avec `ts`, `model`, `latency_s`, `ttft_s`, `eval_count`, `load_s` (temps de chargement du modèle) et `calls`.
- À la fermeture, SensAI affiche la latence moyenne, puis la moyenne hors premier tour : le premier tour est plus lent, car il inclut le chargement du modèle en mémoire.

### Mesure du 01/10/2026

Modèle `llama3.2`, 8 tours, réponses de 7 à 180 tokens, machine non précisée. Le modèle par défaut est aujourd'hui `qwen3:8b` : ces chiffres donnent un ordre de grandeur, pas une mesure actuelle.

| | Moyenne | Hors 1er tour |
|---|---|---|
| Latence d'un tour | 2,80 s | 2,47 s |
| Délai avant le 1er token | 2,44 s | 2,08 s |

- Le premier tour (5,08 s) paie le chargement du modèle (`load_s` = 2,85 s). Ensuite `load_s` tombe à 0,002 s : le modèle reste en mémoire.
- Le délai avant le premier token est quasi constant (environ 2,08 s), même pour une réponse de 7 tokens. Ce délai fixe se paie à chaque appel : une feature qui fait 3 à 5 appels par question coûte donc au moins 6 à 10 s avant la fin de la réponse.

Pour la comparaison des modèles (latence comprise), voir [bench/README.md](../bench/README.md).

La latence d'un tour ne contient que les appels au modèle de l'agent. Les deux appels des contrôles d'entrée (données personnelles, périmètre) n'y sont pas ; la CLI les affiche à part (`[sécurité] ... s avant l'envoi au modèle`), et `[temps total]` les inclut.

## Limites connues

- **L'interface web n'alimente ni le journal ni le log de latence.**
- **Le journal contient les messages et les réponses en entier**, donc des données personnelles (destinations, budgets). Les données que l'utilisateur tape sont masquées avant l'envoi (nom, e-mail, téléphone...), donc elles ne sont pas dans le journal ; celles que le modèle ou un outil produit y sont. Le fichier reste en local et n'est pas chiffré.
- Le journal grandit sans limite : il n'y a pas de rotation, et la vue relit tout le fichier à chaque fois.
- `prompt_version` ne change que si l'on modifie `prompt_versions` dans `config/config.json` : changer le texte d'un prompt sans changer sa version mélange les mesures.
- Le taux d'erreur compte les échecs de tour (`error`, `max_iterations`), pas les échecs d'outil isolés ; ceux-ci ont leur propre colonne dans la table des outils.

## Tests

[tests/test_journal_metrics.py](../tests/test_journal_metrics.py) : reconnaissance d'un échec d'outil, enregistreur de tour, ligne de journal et pannes d'écriture, ce que la CLI écrit pour chaque sorte de tour, lecture du journal et agrégats, texte du tableau de bord, `/stats` et `--stats`, mesure de latence. Les journaux sont écrits dans des dossiers temporaires.

## Fichiers

[src/interaction_log.py](../src/interaction_log.py) · [src/metrics.py](../src/metrics.py) · [src/latency.py](../src/latency.py) · [src/cli.py](../src/cli.py) (`/stats`, `--stats`)

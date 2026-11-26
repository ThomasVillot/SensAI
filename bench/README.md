# bench — choix des modeles Ollama de SensAI

Deux benchmarks maison, un par etape de la reponse de l'agent :

1. **outils** — le modele recoit la question et doit choisir le bon outil (T1, T3, A1),
   produire un JSON conforme (T5) et raisonner juste. C'est le modele qui pilote l'agent.
2. **reponse** — le modele recoit la question et les donnees renvoyees par les outils,
   et redige ce que l'utilisateur lit : dans la bonne langue, sans le faire attendre.

Aucune bibliotheque LLM : tous les appels passent par l'API HTTP d'Ollama, via `requests`.

## Resultats

Mesures du 24/09/2026, sur un ASUS TUF Dash F15 (FX517ZE) sous Linux.
Donnees brutes et rapports complets dans [`results/`](results/).

### 1. Le modele qui pilote l'agent — [`results/outils/`](results/outils/)

Triage : 14 cas sur 31, 1 essai par cas, temperature 0.

| Modele | Global | Tool-calling | Raisonnement | Latence mediane | Boucle A1 (5 appels) | Appels natifs / en texte |
|---|---|---|---|---|---|---|
| `qwen3:8b (nothink)` | **83.0** | **100** | **100** | 6,5 s | 32 s | 6 / 0 |
| `qwen3:8b` | 74.9 | 88.9 | 100 | 34,3 s | 172 s | 5 / 0 |
| `mistral-nemo:12b` | 72.8 | 77.8 | 100 | 11,7 s | 58 s | 4 / 1 |
| `qwen2.5:7b` | 72.0 | 88.9 | 50 | 7,1 s | 35 s | 6 / 0 |
| `mistral` | 69.2 | 100 | 0 | 5,9 s | 29 s | 6 / 0 |
| `granite3.1-moe` | 65.0 | 33.3 | 0 | 0,9 s | 5 s | 0 / 0 |
| `llama3.1:8b` | 64.2 | 33.3 | 100 | 5,5 s | 27 s | 5 / 4 |

- **`qwen3:8b (nothink)` est le seul modele sans aucun echec.**
- **Couper le raisonnement ne coute rien et divise la latence par 5** (34,3 s → 6,5 s).
  Avec raisonnement, qwen3:8b depasse meme le timeout de 300 s sur un cas (T-meteo-1).
- `mistral` choisit parfaitement ses outils mais rate les deux calculs
  (R-budget : 115 au lieu de 220, R-contrainte : 3 au lieu de 2).
- `llama3.1:8b` ecrit une partie de ses appels en texte au lieu de `tool_calls`, et y met
  la description de l'outil a la place de son nom.
- `granite3.1-moe` est de loin le plus rapide mais n'appelle jamais aucun outil.
- `mistral-nemo:12b`, le plus gros que la machine supporte, est deux fois plus lent sans
  etre meilleur : il rate un appel meteo et le cas multi-outils.
- JSON, consignes et francais : 100 pour tout le monde, ces categories ne departagent personne.

### 2. Le modele qui redige la reponse — [`results/reponse/`](results/reponse/)

8 cas : 6 questions en francais (dont 5 avec des donnees d'API en anglais), 1 en anglais
avec des donnees en francais, 1 en espagnol. 1 essai, temperature 0.

| Modele | Langue FR | Langue EN/ES | 1er mot | Reponse complete | Debit | Chargement |
|---|---|---|---|---|---|---|
| `qwen3:4b-instruct` | 100 % | 100 % | 0,5 s | 14,3 s | 20,7 tok/s | 6,4 s |
| `qwen3:8b (nothink)` | 100 % | 100 % | 0,8 s | 25,4 s | 7,5 tok/s | 8,6 s |
| `mistral` | 100 % | 100 % | 0,8 s | 25,2 s | 6,1 tok/s | 6,2 s |
| `llama3.2:3b` | 100 % | 50 % | 0,4 s | 9,0 s | 31,3 tok/s | 6,9 s |
| `ministral-3:3b` | 100 % | 50 % | 0,5 s | 18,9 s | 13,7 tok/s | 10,1 s |
| `gemma3:4b` | 100 % | 50 % | 0,5 s | 5,4 s | 26,4 tok/s | 64,3 s |
| `qwen2.5:7b` | 100 % | 50 % | 1,0 s | 38,7 s | 5,6 tok/s | 8,6 s |
| `llama3.1:8b` | 100 % | 50 % | 1,0 s | 26,1 s | 6,4 tok/s | 10,4 s |
| `granite3.1-moe` | 100 % | 0 % | 0,2 s | 2,2 s | 152,2 tok/s | 4,1 s |
| `mistral-nemo:12b` | 83 % | 100 % | 1,2 s | 36,6 s | 4,1 tok/s | 10,7 s |

- Trois modeles sans faute de langue : `qwen3:4b-instruct`, `qwen3:8b (nothink)`, `mistral`.
- Le cas EN-meteo-fr (question en anglais, donnees en francais) fait tomber 6 modeles sur 10 :
  ils repondent en francais, la langue des donnees et du gabarit.
- `mistral-nemo:12b` repond en anglais a une question francaise : il recopie la langue des donnees.
- Tout le monde affiche son premier mot en moins de 1,2 s (mediane). Ce qui separe les
  modeles, c'est le debit : on lit environ 6 a 8 tok/s. `qwen3:4b-instruct` (20,7) est
  largement au-dessus, `qwen3:8b (nothink)` (7,5) et `mistral` (6,1) sont a la limite.

### Ce que les mesures justifient

- **Pour l'agent : `qwen3:8b` avec `think: false`.** C'est le seul modele du panel qui tient a
  la fois le tool-calling et le raisonnement, et il le fait en 6,5 s par appel.
- **Pour la reponse finale, le meme `qwen3:8b (nothink)` suffit** : aucune faute de langue,
  premier mot en 0,8 s, texte a la vitesse de lecture. Un seul modele en memoire, donc pas
  de chargement a payer entre les deux etapes. `qwen3:4b-instruct` est l'alternative si la
  duree de la reponse complete compte (14 s au lieu de 25 s), au prix d'un deuxieme modele
  a garder en VRAM.

### Ce qui manque pour que la justification tienne

- **Le bench outils n'a tourne qu'en triage** (14 cas, 1 essai). Sans la suite complete a
  3 essais, pas de score de stabilite : rien ne dit que les 100 de qwen3:8b (nothink) tiennent
  d'un essai a l'autre. Compter 10 a 20 min par modele :
  `python bench/run.py outils --models qwen3:8b!nothink mistral qwen2.5:7b --repeat 3`
- **`qwen3:4b-instruct` n'est passe que par le bench reponse.** S'il tenait aussi le
  tool-calling, ce serait un modele unique plus rapide :
  `python bench/run.py outils --models qwen3:4b-instruct --quick --repeat 1 --resume`
- **Une seule machine.** Les latences ne valent que pour ce poste.

## Garde-fous : conformite des sorties structurees — [`garde_fous/`](garde_fous/)

Les deux controles d'entree (detection des donnees personnelles, controle de perimetre) demandent
a Ollama un JSON conforme a un schema (`format`). Ce bench rejoue leurs appels tels que la
production les fait, **20 fois par message**, et compte les reponses conformes.

```bash
ollama serve
python bench/run.py garde_fous                          # 23 messages x 20 essais, modele de controle de la config
python bench/run.py garde_fous --guards pii --repeat 5  # essai rapide, un seul garde-fou
python bench/run.py garde_fous --think off              # compare avec think=false (la prod ne l'envoie pas)
python bench/run.py garde_fous --report-only            # regenere le rapport sans rien relancer
```

- **Fidele a la production** : prompt, schema, options (temperature 0,7 : les essais varient vraiment),
  client HTTP et criteres de conformite viennent de `src/` et de `config/`, rien n'est recopie.
- **Conforme** = les memes controles que `request_response_model` (pas de debris technique, JSON extractible,
  valide contre le schema). **Strict** = la reponse entiere est deja un JSON. **Plausible** (secondaire) =
  en plus, la valeur est sensee : une sortie toujours vide serait conforme a 100 % sans rien detecter.
- **IC95 (Wilson)** : 20/20 sur un message n'est pas « 100 % » (borne basse ≈ 84 %). Le chiffre qui
  conclut est celui de la ligne de synthese, qui regroupe tous les messages.
- Les appels sans reponse (Ollama coupe, timeout) sont comptes a part, pas comme des non-conformites.
- Sortie : `results/garde_fous/resultats.json` (reponses brutes incluses) et `rapport.txt`.
- Duree : plusieurs dizaines de minutes sur un 8B (460 appels). Non mesuree, a verifier au premier run.
- Pas encore de resultats versionnes : ce bench n'a pas tourne.
- Le harnais est teste sans Ollama : `python -m unittest tests.test_bench_garde_fous`.

## Organisation

```
bench/
├── run.py                  point d'entree : python bench/run.py {outils,reponse}
├── common/
│   ├── ollama_client.py    appels HTTP a Ollama, metriques, separation thinking/reponse
│   └── helpers.py          normalisation de texte, lecture/ecriture des resultats
├── outils/                 bench 1 : le modele qui pilote l'agent
│   ├── cases.py            cas de test, verificateurs, ponderation, sous-ensemble --quick
│   ├── runner.py           panel de modeles, execution, calcul des scores
│   ├── report.py           -> rapport.txt
│   └── charts.py           -> rapport.png
├── reponse/                bench 2 : le modele qui redige la reponse finale
│   ├── cases.py            cas de test, detection de langue
│   ├── runner.py           panel de modeles, execution en streaming
│   └── report.py           -> rapport.txt
├── garde_fous/             bench 3 : conformite des sorties structurees des garde-fous
│   ├── cases.py            23 messages (PII, perimetre) et leurs attendus
│   ├── runner.py           rejoue les appels de production, verdicts, IC95
│   └── report.py           -> rapport.txt
└── results/                versionne : c'est le livrable
    ├── outils/             resultats.json (brut), rapport.txt, rapport.png
    └── reponse/            resultats.json (brut, reponses completes incluses), rapport.txt
```

| Je veux... | Ou |
|---|---|
| changer la liste des modeles testes | `MODELS` dans `outils/runner.py` ou `reponse/runner.py` |
| ajouter un cas de test | `outils/cases.py` ou `reponse/cases.py` |
| changer la ponderation du score global | `WEIGHTS` dans `outils/cases.py` |
| changer l'adresse d'Ollama ou les timeouts | `common/ollama_client.py` |

## Relancer

```bash
ollama serve                       # dans un autre terminal
pip install -r bench/requirements.txt

# bench outils
python bench/run.py outils --quick --repeat 1                     # triage : 14 cas, 1 essai
python bench/run.py outils --models qwen3:8b!nothink mistral --repeat 3   # complet : 31 cas, 3 essais
python bench/run.py outils --models llama3.1:8b --quick --repeat 1 --resume  # ajoute un modele
python bench/run.py outils --report-only                          # regenere les rapports

# bench reponse
python bench/run.py reponse
python bench/run.py reponse --models gemma3:4b mistral
python bench/run.py reponse --repeat 3 --temperature 0.7          # plus proche d'une vraie conversation
```

- **Chaque run ecrase `results/<bench>/resultats.json`.** Commite avant de relancer :
  l'historique des mesures, c'est git.
- Un modele absent est signale et ignore. Suffixe `!nothink` pour couper le raisonnement.
- 93 appels par modele en complet, 14 en triage : multiplie par la latence mediane.
- Ne lance pas deux benchs en parallele (Ollama serialise les requetes) et ne mesure pas
  pendant un `ollama pull` : les latences seraient fausses.

## Methode

### Bench outils

Le panel est construit pour que chaque modele reponde a une question differente :

| Modele | Taille | Ce qu'il sert a savoir |
|---|---|---|
| `granite3.1-moe` | 3,3 B | ou est le plancher — un MoE tres petit tient-il le tool-calling ? |
| `mistral` | 7,2 B | la reference generaliste francophone |
| `qwen2.5:7b` | 7,6 B | un modele repute solide en outils, sans surcout de raisonnement |
| `llama3.1:8b` | 8,0 B | tool-calling officiellement supporte en amont |
| `mistral-nemo:12b` | 12,2 B | le plafond de la machine — le gain justifie-t-il la latence ? |
| `qwen3:8b` / `qwen3:8b!nothink` | 8,2 B | le meme modele, avec et sans raisonnement |

| Categorie | Poids | Ce qu'on teste |
|---|---|---|
| **Tool-calling** | 30 % | bon outil choisi, aucun outil quand il ne faut pas, arguments presents, multi-outils, outil absent |
| **Vitesse** | 25 % | moitie debit (40 tok/s = 100), moitie latence mediane (3 s = 100) |
| **JSON structure** | 15 % | conformite a un schema et valeurs extraites correctes (feature T5) |
| **Raisonnement** | 15 % | arithmetique de budget, contraintes, dates — reponses **verifiables** |
| **Consignes** | 8 % | respect strict du format demande |
| **Francais** | 7 % | pas de derive vers l'anglais |
| *Stabilite* | — | accord entre essais d'un meme cas (informatif, seulement avec `--repeat` > 1) |

La ponderation se defend par le scope : T1, T3 et A1 reposent entierement sur le
tool-calling, et A1 multiplie les appels modele par 3 a 5, donc la vitesse pese lourd.

Un LLM est stochastique : un cas passe une fois ne prouve rien. Chaque cas est rejoue
`--repeat` fois a `temperature=0` et `seed=42`, et le score est le taux de reussite.

### Bench reponse

Chaque cas simule l'etape qui suit l'appel d'outil : question de l'utilisateur + donnees
renvoyees par les outils, dans le gabarit du prototype. La plupart des API de voyage
repondent en anglais : c'est le piege principal, un modele qui recopie les donnees derive
vers l'anglais en cours de reponse.

- **Langue** : detection par mots-outils FR/EN/ES. Echec si la reponse est dans la mauvaise
  langue, melange deux langues (moins de 75 % dans la langue attendue) ou contient des
  caracteres non latins (les qwen basculent parfois en chinois).
- **1er mot** : mesure en streaming. C'est ce que l'utilisateur ressent, la reponse finale
  s'affichant au fil de l'eau.
- **Chargement** : si l'agent et la reponse utilisent deux modeles qui ne tiennent pas
  ensemble en VRAM, chaque question paie ce temps en plus (a verifier avec `ollama ps`).
- Plafond de 700 tokens par reponse, pour qu'un modele bavard ne bloque pas le bench.

## Constats techniques

A sortir en soutenance.

**Structured outputs : le schema passe dans `format`.** Ollama accepte un schema JSON complet
dans `format`, pas seulement `"json"`. C'est une contrainte appliquee au decodage, pas une
consigne dans le prompt (qwen3:8b, meme question, 24/09/2026) :

```
format: <schema>  ->  {"ville":"Lisbonne","duree_jours":5,"budget_eur":600}
format: "json"    ->  {"destination":"Lisbonne","duree_sejour":"5 jours"}
```

Sans schema, le modele invente ses noms de cles et renvoie "5 jours" en texte au lieu de
l'entier 5. C'est exactement ce que T5 doit garantir.

**Les modeles qui reflechissent.** qwen3, deepseek-r1... emettent des tokens de raisonnement
avant de repondre (champ `thinking` ou balises `<think>`). `split_thinking()` les separe de
la reponse utile, sinon la latence ne veut plus rien dire. `think: false` divise la latence
de qwen3:8b par 5 sans degrader son tool-calling.

**Tool-calling natif ou en texte.** llama3.1:8b et mistral-nemo:12b n'alimentent pas toujours
`message.tool_calls` : ils ecrivent l'appel en JSON dans le texte, chacun sous une forme
differente. Le bench les rattrape (`_calls_from_text` dans `outils/runner.py`) et compte a
part les appels natifs et en texte. A poids egal, preferer un modele qui remplit
`tool_calls`, sinon T1 devra embarquer un parseur de repli.

## Ajouter un cas de test

Tout est declaratif, dans `outils/cases.py` :

```python
# un outil attendu
{"id": "T-hotel", "msg": "Trouve-moi un hotel a Porto", "expected": "activity_search"}

# aucun outil ne doit etre appele
{"id": "T-none-5", "msg": "Tu es sympa comme assistant", "expected": None}

# raisonnement verifiable
{"id": "R-taxi", "msg": "Le taxi coute 18 euros l'aller. Aller-retour pour 2 personnes ? "
                        "Reponds uniquement par le nombre.",
 "check": _expect_number(72)}
```

Dans `reponse/cases.py`, un cas = une question, sa langue attendue et les donnees des outils.

## Limites assumees

- Les seuils de vitesse (40 tok/s = 100, 3 s = 100) sont arbitraires : ils servent a comparer
  des modeles entre eux sur une meme machine, pas a noter dans l'absolu.
- Les tests de langue sont heuristiques (mots-outils), pas un vrai classifieur.
- Le bench reponse ne juge ni la fidelite aux donnees ni la qualite du texte : relire les
  reponses completes dans `results/reponse/resultats.json` (champ `reponse`).
- **Les resultats ne valent que pour la machine qui les produit.** Fais tourner les benchs
  sur les postes du groupe avant de trancher.

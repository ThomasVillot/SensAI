"""Bench du cache semantique : quel modele d'embeddings pour la recherche web ?

Le cache de src/tools/web_cache.py compare une question a celles deja en cache par
similarite cosinus, et rend le resultat enregistre si elle depasse 0.80. Le modele actuel,
all-MiniLM-L6-v2, est entraine sur de l'anglais : une question posee en italien ou en
allemand a peu de chances de retrouver la meme question posee en francais.

Ce bench repond a trois questions, sur 72 phrases (FR / EN / IT / DE) :
  1. Le modele relie-t-il deux phrases de meme sens ecrites dans deux langues ?   (rappel)
  2. Evite-t-il de confondre deux questions proches mais differentes
     (meteo a Rome / meteo a Berlin) ?                                            (faux hits)
  3. Combien ca coute : taille, chargement, temps d'encodage d'une question ?     (cout)

Le seuil de similarite n'a pas la meme echelle d'un modele a l'autre : comparer deux
modeles au meme seuil 0.80 serait injuste. Le bench les compare donc aussi a budget de
faux hits egal, en recalibrant le seuil de chaque modele.

    python bench/cache_semantique.py                          # panel complet
    python bench/cache_semantique.py --models sentence-transformers/all-MiniLM-L6-v2 BAAI/bge-m3
    python bench/cache_semantique.py --report-only            # refait rapport + graphiques, sans rien recalculer
    python bench/cache_semantique.py --report-only --max-faux-hits 0.01   # autre budget de faux hits

Pas de dependance en plus du projet : sentence-transformers, numpy, matplotlib.
Resultats : bench/results/cache/ (resultats.json, rapport.txt, 5 graphiques PNG).
Chaque run ecrase resultats.json : commite avant de relancer.
"""

from __future__ import annotations

import argparse
import gc
import json
import re
import textwrap
import time
from itertools import combinations
from pathlib import Path

import numpy as np

from common.helpers import load_results, machine_meta, save_results

RESULTS = Path(__file__).resolve().parent / "results" / "cache"

# Meme valeur que CACHE_SIMILARITY_PCT dans src/tools/web_cache.py.
CURRENT_THRESHOLD = 0.80
# Part de "faux hits" tolere pour calibrer le seuil de chaque modele (--max-faux-hits).
DEFAULT_BUDGET = 0.05
# Budgets affiches dans le tableau de comparaison du rapport.
BUDGETS = (0.01, 0.05, 0.10, 0.20)
# Seuils testes : 0.00, 0.01 ... 1.00.
TS = np.arange(101) / 100.0

# Le premier est la reference (le modele actuel), les suivants sont les candidats.
MODELS = [
    "sentence-transformers/all-MiniLM-L6-v2",                       # actuel, anglais seulement
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",  # multilingue, meme famille que l'actuel
    "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",  # multilingue, version plus grosse
    "sentence-transformers/distiluse-base-multilingual-cased-v2",   # multilingue, distille
    "intfloat/multilingual-e5-small",                               # multilingue, petit
    "sentence-transformers/LaBSE",                                  # fait pour apparier des traductions
]
REFERENCE = MODELS[0]


# ---------------------------------------------------------------------------
# Jeu de phrases
# ---------------------------------------------------------------------------
# 3 sujets x 3 lieux = 9 intentions. Chaque intention est ecrite dans 4 langues, en 2
# formulations. Deux phrases de la meme intention ont le meme sens : le cache devrait les
# relier. Deux intentions qui partagent le sujet (meteo Rome / meteo Berlin) ou le lieu
# (meteo Rome / restaurants Rome) sont les pires cas : ce sont des "faux hits" en puissance.

LANGS = ["fr", "en", "it", "de"]

PLACES = {  # lieu -> son nom dans chaque langue
    "rome": {"fr": "Rome", "en": "Rome", "it": "Roma", "de": "Rom"},
    "berlin": {"fr": "Berlin", "en": "Berlin", "it": "Berlino", "de": "Berlin"},
    "lisbonne": {"fr": "Lisbonne", "en": "Lisbon", "it": "Lisbona", "de": "Lissabon"},
}

TOPICS = {  # sujet -> langue -> 2 formulations ({} = le lieu)
    "meteo": {
        "fr": ["Quel temps fait-il à {} en juillet ?",
               "Quelle est la météo à {} au mois de juillet ?"],
        "en": ["What is the weather like in {} in July?",
               "What are the weather conditions in {} during July?"],
        "it": ["Che tempo fa a {} a luglio?",
               "Quali sono le condizioni meteo a {} nel mese di luglio?"],
        "de": ["Wie ist das Wetter in {} im Juli?",
               "Welche Wetterbedingungen herrschen im Juli in {}?"],
    },
    "restaurants": {
        "fr": ["Où manger de la cuisine locale à {} ?",
               "Quels sont les meilleurs restaurants typiques de {} ?"],
        "en": ["Where can I eat local food in {}?",
               "What are the best traditional restaurants in {}?"],
        "it": ["Dove posso mangiare cucina locale a {}?",
               "Quali sono i migliori ristoranti tipici di {}?"],
        "de": ["Wo kann ich in {} die lokale Küche probieren?",
               "Was sind die besten traditionellen Restaurants in {}?"],
    },
    "aeroport": {
        "fr": ["Comment aller de l'aéroport au centre-ville de {} ?",
               "Quel est le moyen le plus simple de rejoindre le centre de {} depuis l'aéroport ?"],
        "en": ["How do I get from the airport to the city centre of {}?",
               "What is the easiest way to get from the airport to downtown {}?"],
        "it": ["Come si arriva dall'aeroporto al centro di {}?",
               "Qual è il modo più semplice per raggiungere il centro di {} dall'aeroporto?"],
        "de": ["Wie komme ich vom Flughafen ins Stadtzentrum von {}?",
               "Was ist der einfachste Weg vom Flughafen ins Zentrum von {}?"],
    },
}


def build_phrases() -> list[dict]:
    phrases = []
    for sujet, by_lang in TOPICS.items():
        for lieu, names in PLACES.items():
            for lang in LANGS:
                for variante, template in enumerate(by_lang[lang]):
                    phrases.append({
                        "intent": sujet + "-" + lieu,
                        "sujet": sujet,
                        "lieu": lieu,
                        "lang": lang,
                        "variante": variante,
                        "text": template.format(names[lang]),
                    })
    return phrases


def short(name: str) -> str:
    return name.split("/")[-1]


def prefix_for(name: str) -> str:
    """Les modeles e5 attendent "query: " devant chaque texte (comparaison question/question)."""
    return "query: " if re.search(r"(^|[/-])e5([-_]|$)", name.lower()) else ""


# ---------------------------------------------------------------------------
# Mesure : embeddings, similarites, cout
# ---------------------------------------------------------------------------

def embed(name: str, texts: list[str], device: str):
    """Charge le modele, encode les phrases, mesure ce que le cache paierait en production."""
    from sentence_transformers import SentenceTransformer

    # 1er chargement : telecharge le modele si besoin, non chronometre. Le second est celui
    # qu'on mesure, fichiers deja presents : c'est ce que paie le serveur apres le 1er lancement.
    SentenceTransformer(name, device=device)
    gc.collect()
    t0 = time.perf_counter()
    model = SentenceTransformer(name, device=device)
    load_s = time.perf_counter() - t0

    inputs = [prefix_for(name) + t for t in texts]
    emb = model.encode(inputs, normalize_embeddings=True, batch_size=32, show_progress_bar=False)

    # Une question a la fois, comme web_cache.search_in_cache_semantic.
    for t in inputs[:3]:
        model.encode(t)
    times_ms = []
    for t in inputs:
        t0 = time.perf_counter()
        model.encode(t)
        times_ms.append((time.perf_counter() - t0) * 1000)

    params = sum(p.numel() for p in model.parameters())
    # Taille d'une entree du cache JSON, ecrite comme web_cache.save_cache (indent=4).
    entry = json.dumps([{"query": texts[0], "embedding": emb[0].tolist(), "result": []}],
                       ensure_ascii=False, indent=4)
    info = {
        "params_m": round(params / 1e6, 1),
        "dim": int(emb.shape[1]),
        "taille_fp32_mo": round(params * 4 / 1024 / 1024),
        "chargement_s": round(load_s, 2),
        "encode_ms_median": round(float(np.median(times_ms)), 1),
        "encode_ms_p95": round(float(np.percentile(times_ms, 95)), 1),
        "cache_ko_par_entree": round(len(entry) / 1024, 1),
    }
    return emb @ emb.T, info


def _versions() -> dict:
    import sentence_transformers
    import torch
    return {"sentence_transformers": sentence_transformers.__version__, "torch": torch.__version__}


def run(models: list[str], device: str, out_dir: Path) -> dict:
    phrases = build_phrases()
    texts = [p["text"] for p in phrases]
    print(f"{len(texts)} phrases, {len(LANGS)} langues, calcul sur {device}")
    print("A mesurer : " + ", ".join(short(m) for m in models))

    payload = {
        "meta": {
            **machine_meta(),
            "device": device,
            "versions": _versions(),
            "seuil_actuel": CURRENT_THRESHOLD,
            "phrases": phrases,
        },
        "models": {},
    }
    for name in models:
        print("\n=== " + name + " ===", flush=True)
        try:
            sim, info = embed(name, texts, device)
        except Exception as exc:  # modele introuvable, pas de reseau, memoire...
            print("  ignore : " + type(exc).__name__ + " : " + str(exc)[:200])
            continue
        payload["models"][name] = {"info": info, "sim": np.round(sim.astype(np.float64), 4).tolist()}
        # Sauvegarde apres chaque modele : un plantage plus loin ne perd pas les mesures.
        save_results(payload, out_dir)
        print(f"  {info['params_m']} M params, dim {info['dim']}, chargement {info['chargement_s']}s, "
              f"encodage {info['encode_ms_median']} ms (p95 {info['encode_ms_p95']} ms)")
    return payload


# ---------------------------------------------------------------------------
# Analyse : tout est calcule depuis les matrices de similarite de resultats.json
# ---------------------------------------------------------------------------

def _curve(values: np.ndarray) -> np.ndarray:
    """Pour chaque seuil de TS : % des paires dont la similarite l'atteint."""
    return 100.0 * (values[:, None] >= TS[None, :]).mean(axis=0)


def _first_under(curve: np.ndarray, budget_pct: float) -> int:
    """Indice du plus petit seuil dont le taux de faux hits tient dans le budget."""
    ok = np.nonzero(curve <= budget_pct)[0]
    return int(ok[0]) if len(ok) else len(TS) - 1


def _auc(pos: np.ndarray, neg: np.ndarray) -> float:
    """Probabilite qu'une paire de meme sens soit plus similaire qu'une paire differente."""
    return float((pos[:, None] > neg[None, :]).mean() + 0.5 * (pos[:, None] == neg[None, :]).mean())


def _top1(sim: np.ndarray, intent: np.ndarray, lang: np.ndarray) -> dict:
    """Pour chaque phrase et chaque langue : la phrase la plus proche a-t-elle le meme sens ?"""
    n = len(intent)
    ok = {"cross": [], "mono": []}
    for i in range(n):
        for lg in LANGS:
            cand = np.nonzero((lang == lg) & (np.arange(n) != i))[0]
            j = cand[np.argmax(sim[i, cand])]
            ok["mono" if lg == lang[i] else "cross"].append(bool(intent[j] == intent[i]))
    return {k: 100.0 * float(np.mean(v)) for k, v in ok.items()}


def _extremes(s, a, b, texts, mask, highest: bool, n: int = 3) -> list[dict]:
    idx = np.nonzero(mask)[0]
    idx = idx[np.argsort(s[idx])]
    pick = idx[::-1][:n] if highest else idx[:n]
    return [{"sim": float(s[k]), "a": texts[a[k]], "b": texts[b[k]]} for k in pick]


def analyse(payload: dict, budget: float) -> dict:
    phrases = payload["meta"]["phrases"]
    texts = [p["text"] for p in phrases]
    intent = np.array([p["intent"] for p in phrases])
    lang = np.array([p["lang"] for p in phrases])
    sujet = np.array([p["sujet"] for p in phrases])
    lieu = np.array([p["lieu"] for p in phrases])

    a, b = np.triu_indices(len(phrases), k=1)
    same_intent = intent[a] == intent[b]
    same_lang = lang[a] == lang[b]
    same_sujet = sujet[a] == sujet[b]
    same_lieu = lieu[a] == lieu[b]
    masks = {
        "pos_cross": same_intent & ~same_lang,         # meme sens, langues differentes
        "pos_mono": same_intent & same_lang,           # meme sens, meme langue
        "neg_lieu": ~same_intent & same_sujet,         # meme sujet, autre lieu
        "neg_sujet": ~same_intent & same_lieu,         # meme lieu, autre sujet
        "neg_autre": ~same_intent & ~same_sujet & ~same_lieu,
    }
    i_now = int(round(CURRENT_THRESHOLD * 100))

    models = {}
    for name, m in payload["models"].items():
        sim = np.array(m["sim"])
        s = sim[a, b]
        vals = {k: s[mask] for k, mask in masks.items()}
        hard = np.concatenate([vals["neg_lieu"], vals["neg_sujet"]])
        curves = {k: _curve(v) for k, v in vals.items()}
        curves["faux_hits"] = _curve(hard)

        def point(i, curves=curves):
            return {
                "seuil": float(TS[i]),
                "rappel_cross": float(curves["pos_cross"][i]),
                "rappel_mono": float(curves["pos_mono"][i]),
                "faux_hits": float(curves["faux_hits"][i]),
                "faux_lieu": float(curves["neg_lieu"][i]),
                "faux_sujet": float(curves["neg_sujet"][i]),
                "faux_autre": float(curves["neg_autre"][i]),
            }

        i_cal = _first_under(curves["faux_hits"], budget * 100)
        t_cal = float(TS[i_cal])

        # Rappel de chaque paire de langues au seuil calibre (diagonale : meme langue).
        langues = []
        for lx in LANGS:
            row = []
            for ly in LANGS:
                pair = same_intent & (((lang[a] == lx) & (lang[b] == ly))
                                      | ((lang[a] == ly) & (lang[b] == lx)))
                row.append(100.0 * float((s[pair] >= t_cal).mean()))
            langues.append(row)

        models[name] = {
            "info": m["info"],
            "valeurs": vals,
            "courbes": curves,
            "seuil_cal": t_cal,
            "actuel": point(i_now),
            "calibre": point(i_cal),
            "budgets": {bd: point(_first_under(curves["faux_hits"], bd * 100)) for bd in BUDGETS},
            "auc_cross": _auc(vals["pos_cross"], hard),
            "top1": _top1(sim, intent, lang),
            "langues": langues,
            "exemples": {
                "faux_hits": _extremes(s, a, b, texts, ~same_intent, highest=True),
                "rates": _extremes(s, a, b, texts, masks["pos_cross"], highest=False),
            },
        }
    return {
        "budget": budget,
        "n_phrases": len(phrases),
        "n_pairs": {k: int(mask.sum()) for k, mask in masks.items()},
        "models": models,
    }


# ---------------------------------------------------------------------------
# Rapport texte
# ---------------------------------------------------------------------------

def build_report(payload: dict, ana: dict) -> str:
    meta, models, budget, npairs = payload["meta"], ana["models"], ana["budget"], ana["n_pairs"]
    phrases = meta["phrases"]
    w = 110
    lines: list[str] = []

    def rule(char="-"):
        lines.append(char * w)

    def section(title):
        lines.append("")
        rule()
        lines.append(title)
        rule()

    def label(nm):
        return (short(nm) + (" (actuel)" if nm == REFERENCE else ""))[:37].ljust(38)

    rule("=")
    lines.append("BENCH CACHE SEMANTIQUE - choix du modele d'embeddings - SensAI")
    rule("=")
    lines.append("Date      : " + meta["date"])
    lines.append("Machine   : " + meta["machine"] + "  (" + meta["os"] + ")")
    versions = ", ".join(k + " " + v for k, v in meta.get("versions", {}).items())
    lines.append("Calcul    : " + meta.get("device", "?") + ("  |  " + versions if versions else ""))
    lines.append(f"Phrases   : {ana['n_phrases']} = {len({p['sujet'] for p in phrases})} sujets x "
                 f"{len({p['lieu'] for p in phrases})} lieux x {len({p['lang'] for p in phrases})} langues "
                 f"(FR, EN, IT, DE) x {len({p['variante'] for p in phrases})} formulations")
    lines.append(f"Paires    : {npairs['pos_cross']} de meme sens en langues differentes, {npairs['pos_mono']} de meme "
                 f"sens dans la meme langue,")
    lines.append(f"            {npairs['neg_lieu']} autre lieu, {npairs['neg_sujet']} autre sujet, "
                 f"{npairs['neg_autre']} tout different")
    lines.append(f"Seuil actuel {CURRENT_THRESHOLD:.2f} (web_cache.py). Budget de calibration : "
                 f"{budget * 100:g} % de faux hits.")
    lines.append("")
    lines.append("  Rappel      % des paires de meme sens que le cache relierait (x-langue : langues differentes).")
    lines.append("  Faux hit    paire de sens different que le cache relierait a tort : il rendrait le resultat d'une")
    lines.append("              autre question. Mesure sur les paires \"difficiles\" (meme sujet ou meme lieu).")
    lines.append("  Faux lieu   % des paires \"meme sujet, autre lieu\" reliees (meteo Rome / meteo Berlin).")
    lines.append("  Faux sujet  % des paires \"meme lieu, autre sujet\" reliees (meteo Rome / restaurants Rome).")
    lines.append("  AUC         separation meme sens / sens different, sans seuil : 1.00 parfait, 0.50 hasard.")
    lines.append("  Top-1       % des phrases dont la plus proche voisine (dans une autre langue) a le meme sens.")

    # 1. Le cache tel qu'il est : seuil 0.80 pour tout le monde.
    section(f"1. AU SEUIL ACTUEL ({CURRENT_THRESHOLD:.2f}) - ce que donnerait un simple changement de modele")
    lines.append("".ljust(38) + "Rappel".rjust(10) + "Rappel".rjust(10) + "Faux hits".rjust(11)
                 + "Faux".rjust(12) + "Faux".rjust(12))
    lines.append("Modele".ljust(38) + "x-langue".rjust(10) + "mono".rjust(10) + "difficiles".rjust(11)
                 + "lieu".rjust(12) + "sujet".rjust(12))
    rule()
    for nm in sorted(models, key=lambda n: -models[n]["actuel"]["rappel_cross"]):
        p = models[nm]["actuel"]
        lines.append(label(nm) + f"{p['rappel_cross']:.0f}%".rjust(10) + f"{p['rappel_mono']:.0f}%".rjust(10)
                     + f"{p['faux_hits']:.0f}%".rjust(11) + f"{p['faux_lieu']:.0f}%".rjust(12)
                     + f"{p['faux_sujet']:.0f}%".rjust(12))

    # 2. A armes egales : le seuil est recalibre pour chaque modele.
    section("2. A BUDGET DE FAUX HITS EGAL - seuil recalibre pour chaque modele (rappel x-langue @ seuil)")
    head = "".ljust(38) + "AUC".rjust(9) + "Top-1".rjust(9)
    head2 = "Modele".ljust(38) + "x-langue".rjust(9) + "x-langue".rjust(9)
    for bd in BUDGETS:
        head += f"{bd * 100:g}% de".rjust(14)
        head2 += "faux hits".rjust(14)
    lines.append(head)
    lines.append(head2)
    rule()
    for nm in sorted(models, key=lambda n: -models[n]["calibre"]["rappel_cross"]):
        m = models[nm]
        row = label(nm) + f"{m['auc_cross']:.2f}".rjust(9) + f"{m['top1']['cross']:.0f}%".rjust(9)
        for bd in BUDGETS:
            p = m["budgets"][bd]
            row += f"{p['rappel_cross']:.0f}% @{p['seuil']:.2f}".rjust(14)
        lines.append(row)

    best = max(models, key=lambda n: models[n]["calibre"]["rappel_cross"])
    pb = models[best]["calibre"]
    lines.append("")
    lines.append(f"Avec {budget * 100:g} % de faux hits tolere, le meilleur rappel entre langues est {short(best)} : "
                 f"{pb['rappel_cross']:.0f} % (seuil {pb['seuil']:.2f}).")
    if REFERENCE in models and best != REFERENCE:
        pr = models[REFERENCE]["calibre"]
        lines.append(f"Le modele actuel ({short(REFERENCE)}) : {pr['rappel_cross']:.0f} % "
                     f"(seuil {pr['seuil']:.2f}).")

    # 3. Cout.
    section("3. COUT - ce que le modele coute au serveur")
    lines.append("".ljust(38) + "Params".rjust(9) + "Dim.".rjust(7) + "Taille".rjust(9) + "Chargement".rjust(12)
                 + "Encodage".rjust(10) + "Encodage".rjust(10) + "Cache JSON".rjust(12))
    lines.append("Modele".ljust(38) + "(M)".rjust(9) + "".rjust(7) + "fp32 (Mo)".rjust(9) + "a chaud".rjust(12)
                 + "median".rjust(10) + "p95".rjust(10) + "Ko/entree".rjust(12))
    rule()
    for nm in models:
        i = models[nm]["info"]
        lines.append(label(nm) + f"{i['params_m']}".rjust(9) + f"{i['dim']}".rjust(7)
                     + f"{i['taille_fp32_mo']}".rjust(9) + f"{i['chargement_s']}s".rjust(12)
                     + f"{i['encode_ms_median']}ms".rjust(10) + f"{i['encode_ms_p95']}ms".rjust(10)
                     + f"{i['cache_ko_par_entree']}".rjust(12))
    lines.append("")
    lines.append("Encodage : une question a la fois, comme le cache. Cache JSON : taille d'un embedding dans")
    lines.append(".web_cache_semantic.json (indent=4) ; le fichier entier est relu a chaque recherche.")

    # 4. Langues.
    pairs = list(combinations(range(len(LANGS)), 2))
    section(f"4. RAPPEL PAR PAIRE DE LANGUES - au seuil calibre ({budget * 100:g} % de faux hits)")
    lines.append("Modele".ljust(38) + "Seuil".rjust(7)
                 + "".join((LANGS[x] + "-" + LANGS[y]).upper().rjust(8) for x, y in pairs))
    rule()
    for nm in sorted(models, key=lambda n: -models[n]["calibre"]["rappel_cross"]):
        m = models[nm]
        lines.append(label(nm) + f"{m['seuil_cal']:.2f}".rjust(7)
                     + "".join(f"{m['langues'][x][y]:.0f}%".rjust(8) for x, y in pairs))

    # 5. Exemples.
    section("5. EXEMPLES - les pires paires de chaque modele (au sens de la similarite brute)")
    for nm in models:
        ex = models[nm]["exemples"]
        lines.append(short(nm) + f"  (seuil calibre {models[nm]['seuil_cal']:.2f})")
        lines.append("  Sens different mais tres similaires (faux hits les plus probables) :")
        for e in ex["faux_hits"]:
            lines.append(f"    {e['sim']:.2f}  {e['a'][:46]}  <>  {e['b'][:46]}")
        lines.append("  Meme sens mais peu similaires (reformulations les plus dures a retrouver) :")
        for e in ex["rates"]:
            lines.append(f"    {e['sim']:.2f}  {e['a'][:46]}  <>  {e['b'][:46]}")
        lines.append("")

    rule()
    lines.append("LIMITES")
    rule()
    for text in [
        "72 phrases et 9 intentions : des ordres de grandeur, pas des intervalles de confiance. Deux modeles a "
        "quelques points d'ecart ne se departagent pas.",
        "Les phrases viennent de gabarits ou seul le lieu change : ce sont les paires les plus dures pour un cache. "
        "Un vrai trafic aura moins de quasi-doublons, donc moins de faux hits.",
        "Le seuil calibre est choisi sur les memes paires que celles qui le mesurent : le rappel annonce est optimiste. "
        "Refaire la calibration sur de vraies requetes avant de figer le seuil.",
        "Latences, chargement : cette machine et ce device uniquement (cpu par defaut, pour ne pas prendre de VRAM a "
        "Ollama). Chargement a chaud : fichiers du modele deja telecharges.",
        "Une fusion de deux caches (anglais / francais) ou une traduction avant l'embedding sont d'autres pistes, non testees ici.",
    ]:
        lines.append(textwrap.fill(text, w - 2, initial_indent="- ", subsequent_indent="  "))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Graphiques
# ---------------------------------------------------------------------------

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
GRID = "#e6e5e1"
MUTED = "#9a9892"
# Palette categorielle, dans cet ordre : bleu, orange, aqua, jaune, magenta, vert, violet, rouge.
CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SEQUENTIAL = ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]
POS, NEG = CATEGORICAL[0], CATEGORICAL[1]

CLASSES = [  # (cle, etiquette, couleur)
    ("pos_cross", "Meme sens\nlangues differentes", POS),
    ("pos_mono", "Meme sens\nmeme langue", POS),
    ("neg_lieu", "Autre lieu\nmeme sujet", NEG),
    ("neg_sujet", "Autre sujet\nmeme lieu", NEG),
    ("neg_autre", "Tout\ndifferent", MUTED),
]


def model_colors(names: list[str]) -> dict:
    """Une couleur par modele, fixe d'un run a l'autre : --models ne repeint pas les autres."""
    colors, extra = {}, len(MODELS) - 1
    for nm in names:
        if nm == REFERENCE:
            colors[nm] = INK2
        elif nm in MODELS:
            colors[nm] = CATEGORICAL[MODELS.index(nm) - 1]
        else:
            colors[nm] = CATEGORICAL[extra] if extra < len(CATEGORICAL) else MUTED
            extra += 1
    return colors


def _style(plt) -> None:
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": "#c3c2b7", "axes.labelcolor": INK2, "text.color": INK,
        "xtick.color": INK2, "ytick.color": INK2,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
        "axes.titlesize": 11, "axes.titleweight": "bold", "font.size": 10,
        "legend.frameon": False,
    })


def _grid_shape(n: int, max_cols: int = 3) -> tuple[int, int]:
    cols = min(max_cols, n)
    return -(-n // cols), cols


def _distributions(plt, ana, colors, out_dir) -> Path:
    """Chaque point est une paire de phrases : on voit directement ou tombe chaque type de paire."""
    names = list(ana["models"])
    nrows, ncols = _grid_shape(len(names))
    fig, axes = plt.subplots(nrows, ncols, figsize=(5.8 * ncols, 4.0 * nrows + 0.8),
                             sharex=True, sharey=True, squeeze=False, constrained_layout=True)
    rng = np.random.default_rng(0)
    for ax, name in zip(axes.flat, names):
        m = ana["models"][name]
        for row, (key, _, color) in enumerate(CLASSES):
            v = m["valeurs"][key]
            ax.scatter(v, row + rng.uniform(-0.3, 0.3, len(v)), s=7, color=color, alpha=0.35, linewidths=0)
            ax.plot([np.median(v)] * 2, [row - 0.38, row + 0.38], color=INK, linewidth=2)
        ax.axvline(CURRENT_THRESHOLD, color=INK2, linestyle="--", linewidth=1.2)
        ax.axvline(m["seuil_cal"], color=colors[name], linewidth=2.2)
        ax.set_title(textwrap.fill(short(name), 28) + f"\nseuil calibre : {m['seuil_cal']:.2f}", fontsize=10)
        ax.set_yticks(range(len(CLASSES)))
        ax.set_yticklabels([lab for _, lab, _ in CLASSES], fontsize=8)
        ax.set_ylim(len(CLASSES) - 0.5, -0.5)
        ax.set_xlim(-0.15, 1.02)
        ax.set_xlabel("similarite cosinus")
        ax.grid(False, axis="y")
    for ax in axes.flat[len(names):]:
        ax.axis("off")
    fig.suptitle("Ou tombent les paires de phrases selon le modele", fontsize=14, fontweight="bold")
    fig.supxlabel("un point = une paire de phrases | trait noir : mediane | tirets : seuil actuel "
                  f"{CURRENT_THRESHOLD:.2f} | trait colore : seuil calibre pour {ana['budget'] * 100:g} % de faux hits",
                  fontsize=9, color=INK2)
    path = out_dir / "distributions.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def _thresholds(plt, ana, colors, out_dir) -> Path:
    names = list(ana["models"])
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.2), sharex=True, constrained_layout=True)
    for name in names:
        c = ana["models"][name]["courbes"]
        style = {"color": colors[name], "linestyle": "--" if name == REFERENCE else "-", "linewidth": 2}
        ax1.plot(TS, c["pos_cross"], label=short(name), **style)
        ax2.plot(TS, c["faux_hits"], **style)
    for ax in (ax1, ax2):
        ax.axvline(CURRENT_THRESHOLD, color=INK2, linestyle=":", linewidth=1.3)
        ax.text(CURRENT_THRESHOLD + 0.008, 97, f"seuil actuel {CURRENT_THRESHOLD:.2f}",
                fontsize=8, color=INK2, va="top")
        ax.set_xlim(0.2, 1.0)
        ax.set_ylim(0, 100)
        ax.set_xlabel("seuil de similarite")
    ax1.set_title("Reformulations retrouvees entre langues differentes")
    ax1.set_ylabel("% des paires de meme sens reliees")
    ax1.legend(loc="lower left", fontsize=8)
    ax2.set_title("Questions differentes confondues (faux hits)")
    ax2.set_ylabel("% des paires proches mais differentes reliees")
    fig.suptitle("Effet du seuil de similarite", fontsize=14, fontweight="bold")
    path = out_dir / "seuil.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def _tradeoff(plt, ana, colors, out_dir) -> Path:
    """Rappel contre faux hits quand on fait varier le seuil : la courbe la plus haute gagne."""
    names = list(ana["models"])
    i_now = int(round(CURRENT_THRESHOLD * 100))
    xmax = min(100, max(30, max(ana["models"][n]["courbes"]["faux_hits"][i_now] for n in names) + 4))
    fig, ax = plt.subplots(figsize=(8.5, 6.2), constrained_layout=True)
    for name in names:
        c = ana["models"][name]["courbes"]
        color = colors[name]
        ax.plot(c["faux_hits"], c["pos_cross"], color=color, linewidth=2,
                linestyle="--" if name == REFERENCE else "-", label=short(name))
        ax.scatter([c["faux_hits"][i_now]], [c["pos_cross"][i_now]], s=80, color=color,
                   edgecolors=SURFACE, linewidths=1.5, zorder=3)
    ax.scatter([], [], s=80, color=INK2, label=f"ronds : seuil actuel {CURRENT_THRESHOLD:.2f}")
    ax.axvline(ana["budget"] * 100, color=INK2, linestyle=":", linewidth=1.3)
    ax.text(ana["budget"] * 100 + 0.4, 3, f"budget {ana['budget'] * 100:g} %", fontsize=8, color=INK2)
    ax.set_xlim(0, xmax)
    ax.set_ylim(0, 100)
    ax.set_xlabel("faux hits : % des paires proches mais differentes reliees a tort")
    ax.set_ylabel("rappel : % des paires de meme sens reliees entre langues")
    ax.set_title("Rappel contre faux hits en faisant varier le seuil\n(plus la courbe est haut et a gauche, mieux c'est)")
    ax.legend(loc="lower right", fontsize=8)
    path = out_dir / "compromis.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def _languages(plt, ana, out_dir) -> Path:
    from matplotlib.colors import LinearSegmentedColormap

    cmap = LinearSegmentedColormap.from_list("seq", SEQUENTIAL)
    names = list(ana["models"])
    nrows, ncols = _grid_shape(len(names))
    fig, axes = plt.subplots(nrows, ncols, figsize=(4.4 * ncols + 1.4, 4.3 * nrows + 0.6),
                             squeeze=False, constrained_layout=True)
    used = list(axes.flat[:len(names)])
    labels = [lg.upper() for lg in LANGS]
    im = None
    for ax, name in zip(used, names):
        m = ana["models"][name]
        mat = np.array(m["langues"])
        im = ax.imshow(mat, cmap=cmap, vmin=0, vmax=100)
        for i in range(len(LANGS)):
            for j in range(len(LANGS)):
                ax.text(j, i, f"{mat[i, j]:.0f}", ha="center", va="center", fontsize=10,
                        color="white" if mat[i, j] >= 55 else INK)
        ax.set_xticks(range(len(LANGS)))
        ax.set_xticklabels(labels)
        ax.set_yticks(range(len(LANGS)))
        ax.set_yticklabels(labels)
        ax.tick_params(length=0)
        ax.grid(False)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.set_title(textwrap.fill(short(name), 28) + f"\nseuil {m['seuil_cal']:.2f}", fontsize=10)
    for ax in axes.flat[len(names):]:
        ax.axis("off")
    fig.colorbar(im, ax=used, shrink=0.7, label="% de paires reliees")
    fig.suptitle("Le cache fait-il le lien entre les langues ?", fontsize=14, fontweight="bold")
    fig.supxlabel(f"paires de meme sens reliees au seuil calibre de chaque modele ({ana['budget'] * 100:g} % de "
                  "faux hits) | diagonale : meme langue, autre formulation", fontsize=9, color=INK2)
    path = out_dir / "langues.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def _summary(plt, ana, colors, out_dir) -> Path:
    names = list(ana["models"])
    n = len(names)
    y = np.arange(n)
    cols = [colors[nm] for nm in names]
    now = [ana["models"][nm]["actuel"]["rappel_cross"] for nm in names]
    cal = [ana["models"][nm]["calibre"]["rappel_cross"] for nm in names]
    med = [ana["models"][nm]["info"]["encode_ms_median"] for nm in names]
    p95 = [ana["models"][nm]["info"]["encode_ms_p95"] for nm in names]

    fig = plt.figure(figsize=(19, 5.6), constrained_layout=True)
    gs = fig.add_gridspec(1, 3, width_ratios=[1.45, 1, 1.05])
    ax1 = fig.add_subplot(gs[0, 0])
    ax2 = fig.add_subplot(gs[0, 1], sharey=ax1)
    ax3 = fig.add_subplot(gs[0, 2])

    b1 = ax1.barh(y - 0.2, now, height=0.36, color=cols, alpha=0.35)
    b2 = ax1.barh(y + 0.2, cal, height=0.36, color=cols)
    ax1.bar_label(b1, labels=[f"{v:.0f}%" for v in now], padding=3, fontsize=8, color=INK2)
    ax1.bar_label(b2, labels=[f"{v:.0f}%" for v in cal], padding=3, fontsize=8, color=INK2)
    ax1.set_yticks(y)
    ax1.set_yticklabels([short(nm) for nm in names], fontsize=9)
    ax1.set_xlim(0, 115)
    ax1.set_xlabel("% des paires de meme sens reliees entre langues")
    ax1.set_title(f"Rappel entre langues\nclair : seuil actuel {CURRENT_THRESHOLD:.2f} | "
                  f"plein : seuil calibre ({ana['budget'] * 100:g} % de faux hits)", fontsize=10)
    ax1.grid(False, axis="y")

    bars = ax2.barh(y, med, height=0.5, color=cols)
    ax2.bar_label(bars, labels=[f"{m:.0f} ms (p95 {p:.0f})" for m, p in zip(med, p95)],
                  padding=3, fontsize=8, color=INK2)
    ax2.set_xlim(0, max(med) * 1.7)
    ax1.set_ylim(n - 0.5, -0.5)  # ax2 partage l'axe y : une seule inversion, apres les deux barres
    ax2.tick_params(labelleft=False)
    ax2.set_xlabel("ms")
    ax2.set_title("Temps d'encodage d'une question\n(median, une question a la fois)", fontsize=10)
    ax2.grid(False, axis="y")

    for nm, x, yy in zip(names, med, cal):
        ax3.scatter([x], [yy], s=100, color=colors[nm], edgecolors=SURFACE, linewidths=1.5, zorder=3)
        ax3.annotate(short(nm), (x, yy), xytext=(7, 6), textcoords="offset points", fontsize=8, color=INK)
    ax3.margins(x=0.45, y=0.2)
    ax3.set_xlabel("temps d'encodage (ms, median)")
    ax3.set_ylabel("rappel entre langues au seuil calibre (%)")
    ax3.set_title("Qualite contre cout\n(en haut a gauche : bon et rapide)", fontsize=10)

    fig.suptitle("Synthese : quel modele pour le cache semantique ?", fontsize=14, fontweight="bold")
    path = out_dir / "synthese.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def build_charts(ana: dict, out_dir: Path) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    _style(plt)
    out_dir.mkdir(parents=True, exist_ok=True)
    colors = model_colors(list(ana["models"]))
    return [
        _distributions(plt, ana, colors, out_dir),
        _thresholds(plt, ana, colors, out_dir),
        _tradeoff(plt, ana, colors, out_dir),
        _languages(plt, ana, out_dir),
        _summary(plt, ana, colors, out_dir),
    ]


# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Bench du modele d'embeddings du cache semantique (FR / EN / IT / DE)",
        epilog="Les modeles sont telecharges au premier passage (de 90 Mo a 1,8 Go chacun).")
    ap.add_argument("--models", nargs="+", default=MODELS,
                    help="modeles sentence-transformers a tester. Defaut : le panel complet")
    ap.add_argument("--device", default="cpu",
                    help="cpu (defaut : ne prend pas de VRAM a Ollama) ou cuda")
    ap.add_argument("--max-faux-hits", type=float, default=DEFAULT_BUDGET,
                    help="part de faux hits tolere pour calibrer le seuil de chaque modele "
                         "(0.05 = 5 %%, defaut)")
    ap.add_argument("--out", type=Path, default=RESULTS, help="dossier de sortie (defaut : results/cache/)")
    ap.add_argument("--report-only", action="store_true",
                    help="ne recalcule rien, refait le rapport et les graphiques depuis resultats.json")
    ap.add_argument("--no-charts", action="store_true", help="rapport texte uniquement")
    args = ap.parse_args()

    payload = load_results(args.out) if args.report_only else run(args.models, args.device, args.out)
    if not payload["models"]:
        raise SystemExit("Aucun modele mesure.")

    ana = analyse(payload, args.max_faux_hits)
    report = build_report(payload, ana)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "rapport.txt").write_text(report + "\n", encoding="utf-8")
    print("\nRapport texte : " + str(args.out / "rapport.txt"))

    if not args.no_charts:
        try:
            for path in build_charts(ana, args.out):
                print("Graphique     : " + str(path))
        except ImportError as exc:
            print("Graphiques ignores (" + str(exc) + "). pip install matplotlib numpy")
    print("\n" + report)


if __name__ == "__main__":
    main()

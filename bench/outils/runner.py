"""Moteur du bench outils : execute les cas de test et calcule les scores."""

from __future__ import annotations

import statistics
from pathlib import Path

from common import ollama_client as oc
from common.helpers import RESULTS_FILE, load_results, machine_meta, save_results

from . import cases

# Le panel. Chaque entree repond a une question differente (voir README).
# Un modele absent est signale et ignore, pas une erreur.
MODELS = [
    "granite3.1-moe",     # 3.3B  — le plus petit, il situe le plancher
    "mistral",            # 7.2B  — la reference generaliste francophone
    "qwen2.5:7b",         # 7.6B  — reputee solide en tool-calling, sans raisonnement
    "llama3.1:8b",        # 8.0B  — tool-calling officiellement supporte
    "mistral-nemo:12b",   # 12.2B — le plus gros, il situe le plafond de la machine
    "qwen3:8b",           # 8.2B  — avec raisonnement
    "qwen3:8b!nothink",   # 8.2B  — le meme, raisonnement coupe
]


# --------------------------------------------------------------------------
# Un cas de test = un appel modele + une verification
# --------------------------------------------------------------------------

def _ask(spec: oc.Spec, case: dict, **kwargs) -> dict:
    return oc.chat(spec.model, [{"role": "user", "content": case["msg"]}],
                   think=spec.think, **kwargs)


def _failure(data: dict) -> dict:
    return {"ok": False, "why": data["_error"], "metrics": oc.metrics(data)}


def _calls_from_text(content: str) -> list[dict]:
    """Repli pour les modeles qui n'alimentent pas message.tool_calls.

    Mesure du 24/09/2026 : ni llama3.1:8b ni mistral-nemo:12b n'emettent de
    tool_calls natifs sur cette machine. Ils ecrivent l'appel en JSON dans le
    texte, et pas sous la meme forme :

        mistral-nemo -> [{"name": "weather", "arguments": {...}}]   (une liste)
        llama3.1     -> {"name": "<la description>", "parameters": {...}}

    D'ou les trois tolerances ici : objet ou liste, 'arguments' ou 'parameters'.
    Sans ca on comptait un echec a un modele qui avait choisi le bon outil.
    """
    guess = oc.extract_json(content)
    items = guess if isinstance(guess, list) else [guess]
    out = []
    for item in items:
        if isinstance(item, dict) and item.get("name"):
            args = item.get("arguments") or item.get("parameters") or {}
            out.append({"name": item["name"],
                        "args": args if isinstance(args, dict) else {}})
    return out


def _tool_calls(data: dict, content: str) -> tuple[list[dict], str]:
    """Appels d'outils emis, et leur format : 'natif', 'texte' ou 'aucun'.

    On distingue l'appel natif de l'appel bricole en texte : c'est une
    information de conception, pas un detail. Si le modele retenu n'emet pas
    de tool_calls natifs, T1 doit prevoir un parseur de repli.
    """
    calls = oc.tool_calls(data)
    if calls:
        return calls, "natif"
    calls = _calls_from_text(content)
    return calls, ("texte" if calls else "aucun")


def _run_tool_case(spec: oc.Spec, case: dict) -> dict:
    data = _ask(spec, case, tools=case.get("tools", cases.ALL_TOOLS))
    if "_error" in data:
        return _failure(data)
    thinking, content = oc.split_thinking(data)
    calls, fmt = _tool_calls(data, content)

    expected = case["expected"]
    got = calls[0]["name"] if calls else None

    if expected is None:
        ok = not calls
        why = "" if ok else "a appele " + str(got) + " alors qu'aucun outil n'etait attendu"
    else:
        ok = got == expected
        why = "" if ok else "attendu " + str(expected) + ", obtenu " + str(got)
        if ok and case.get("args"):
            missing = [k for k in case["args"] if k not in calls[0]["args"]]
            if missing:
                ok = False
                why = "bon outil mais arguments manquants : " + ", ".join(missing)

    return {"ok": ok, "why": why, "metrics": oc.metrics(data), "tool_format": fmt,
            "thinking_chars": len(thinking)}


def _run_multi_case(spec: oc.Spec, case: dict) -> dict:
    data = _ask(spec, case, tools=cases.ALL_TOOLS)
    if "_error" in data:
        return _failure(data)
    _, content = oc.split_thinking(data)
    calls, fmt = _tool_calls(data, content)
    names = {c["name"] for c in calls}
    ok = case["expected_set"].issubset(names)
    why = "" if ok else "outils obtenus : " + str(sorted(names) or "aucun")
    return {"ok": ok, "why": why, "metrics": oc.metrics(data), "tool_format": fmt}


def _run_json_case(spec: oc.Spec, case: dict) -> dict:
    # On passe le schema complet a Ollama (structured outputs) et pas seulement
    # format:"json". C'est une contrainte au decodage, pas une priere dans le
    # prompt : sans ca, le modele invente ses propres noms de cles et le test
    # mesure sa capacite a deviner, pas sa capacite a respecter un schema.
    data = _ask(spec, case, fmt=case["schema"])
    if "_error" in data:
        return _failure(data)
    _, content = oc.split_thinking(data)
    obj = oc.extract_json(content)
    if obj is None:
        return {"ok": False, "why": "JSON illisible", "metrics": oc.metrics(data)}
    ok, why = cases.validate_schema(obj, case["schema"])
    if ok and case.get("expect"):
        ok, why = cases.check_json_values(obj, case["expect"])
    return {"ok": ok, "why": why, "metrics": oc.metrics(data)}


def _run_text_case(spec: oc.Spec, case: dict) -> dict:
    data = _ask(spec, case)
    if "_error" in data:
        return _failure(data)
    thinking, content = oc.split_thinking(data)
    ok, why = case["check"](content)
    return {"ok": ok, "why": why, "metrics": oc.metrics(data),
            "thinking_chars": len(thinking)}


SUITES = [
    ("outils", cases.TOOL_CASES, _run_tool_case),
    ("outils", cases.MULTI_TOOL_CASES, _run_multi_case),
    ("json", cases.JSON_CASES, _run_json_case),
    ("raisonnement", cases.REASONING_CASES, _run_text_case),
    ("consignes", cases.INSTRUCTION_CASES, _run_text_case),
    ("francais", cases.FRENCH_CASES, _run_text_case),
]


def _suites(quick: bool):
    for category, suite, runner in SUITES:
        if quick:
            suite = [c for c in suite if c["id"] in cases.QUICK_IDS]
        yield category, suite, runner


# --------------------------------------------------------------------------
# Un modele = tous les cas, repetes, puis les scores
# --------------------------------------------------------------------------

def _score(ok: int, total: int) -> float:
    return round(100.0 * ok / total, 1) if total else 0.0


def run_model(spec: oc.Spec, repeat: int, quick: bool = False) -> dict:
    print("\n=== " + spec.label + " ===")
    print("  prechauffage...", flush=True)
    oc.warmup(spec.model, think=spec.think)

    per_case: list[dict] = []
    gen_speeds: list[float] = []
    latencies: list[float] = []
    thinking_total = 0
    fmt_counts = {"natif": 0, "texte": 0}

    for category, suite, runner in _suites(quick):
        for case in suite:
            runs = []
            for _ in range(repeat):
                r = runner(spec, case)
                runs.append(r)
                m = r["metrics"]
                if m.get("gen_tok_s"):
                    gen_speeds.append(m["gen_tok_s"])
                if m.get("total_s"):
                    latencies.append(m["total_s"])
                thinking_total += r.get("thinking_chars", 0)
                if r.get("tool_format") in fmt_counts:
                    fmt_counts[r["tool_format"]] += 1

            successes = sum(1 for r in runs if r["ok"])
            rate = successes / len(runs)
            per_case.append({
                "categorie": category,
                "id": case["id"],
                "reussites": successes,
                "essais": len(runs),
                "taux": rate,
                "pourquoi": next((r["why"] for r in runs if not r["ok"] and r["why"]), ""),
            })
            mark = "OK " if rate == 1 else ("~~ " if rate > 0 else "KO ")
            detail = "" if rate == 1 else "   " + per_case[-1]["pourquoi"][:70]
            print("  " + mark + case["id"].ljust(16) + str(successes) + "/" + str(len(runs)) + detail, flush=True)

    scores: dict[str, float | None] = {}
    for category in ("outils", "json", "raisonnement", "consignes", "francais"):
        rows = [c for c in per_case if c["categorie"] == category]
        scores[category] = _score(sum(c["reussites"] for c in rows),
                                  sum(c["essais"] for c in rows))

    # Vitesse : moitie debit (40 tok/s = 100), moitie latence mediane (3 s = 100).
    med_gen = statistics.median(gen_speeds) if gen_speeds else 0.0
    med_lat = statistics.median(latencies) if latencies else 999.0
    debit_score = min(100.0, 100.0 * med_gen / 40.0)
    latence_score = min(100.0, 100.0 * 3.0 / med_lat) if med_lat else 0.0
    scores["vitesse"] = round(0.5 * debit_score + 0.5 * latence_score, 1)

    # Stabilite : accord entre essais d'un meme cas. N'existe qu'avec --repeat > 1.
    multi = [c for c in per_case if c["essais"] > 1]
    if multi:
        agreements = [max(c["taux"], 1 - c["taux"]) for c in multi]
        scores["stabilite"] = round((statistics.mean(agreements) - 0.5) * 200, 1)
    else:
        scores["stabilite"] = None

    global_score = round(sum(scores[k] * w for k, w in cases.WEIGHTS.items()), 1)

    return {
        "cases": per_case,
        "scores": scores,
        "global": global_score,
        "perf": {
            "gen_tok_s_median": round(med_gen, 2),
            "latence_s_median": round(med_lat, 2),
            "latence_s_p90": round(sorted(latencies)[int(len(latencies) * 0.9)], 2) if latencies else None,
            "latence_s_max": round(max(latencies), 2) if latencies else None,
            "appels": len(latencies),
            "thinking_chars": thinking_total,
            "outils_natifs": fmt_counts["natif"],
            "outils_texte": fmt_counts["texte"],
        },
    }


# --------------------------------------------------------------------------
# Le panel complet
# --------------------------------------------------------------------------

def _previous_results(out_dir: Path, mode: str, repeat: int) -> dict:
    """Modeles deja mesures dans le meme mode, pour --resume."""
    if not (out_dir / RESULTS_FILE).exists():
        return {}
    old = load_results(out_dir)
    old_mode = old["meta"].get("mode")
    old_repeat = old["meta"].get("repetitions")
    # Reprendre des mesures faites dans un autre mode melangerait 14 cas et
    # 31 cas dans un meme classement : les scores ne seraient plus comparables
    # et rien ne le signalerait dans le rapport. On prefere remesurer.
    if old_mode != mode or old_repeat != repeat:
        print("[resume] ignore : " + RESULTS_FILE + " a ete produit en mode "
              + str(old_mode) + "/" + str(old_repeat) + " essai(s), "
              "on demande " + mode + "/" + str(repeat) + ". "
              "Melanger les deux rendrait le classement faux.")
        return {}
    return old["models"]


def run(models: list[str], repeat: int, out_dir: Path,
        quick: bool = False, resume: bool = False) -> dict:
    specs = oc.resolve_specs(models)
    mode = "triage" if quick else "complet"

    results = _previous_results(out_dir, mode, repeat) if resume else {}
    already = [s.label for s in specs if s.label in results]
    if already:
        print("[resume] deja mesures, on ne relance pas : " + ", ".join(already))
    specs = [s for s in specs if s.label not in results]

    n_cases = sum(len(suite) for _, suite, _ in _suites(quick))
    print("Mode : " + ("TRIAGE (sous-ensemble)" if quick else "COMPLET"))
    print("A mesurer : " + (", ".join(s.label for s in specs) or "rien"))
    print("Repetitions : " + str(repeat) + "  |  " + str(n_cases * repeat) + " appels par modele\n")

    payload = {
        "meta": {**machine_meta(), "repetitions": repeat, "mode": mode,
                 "ponderation": cases.WEIGHTS},
        "models": results,
    }
    for spec in specs:
        results[spec.label] = run_model(spec, repeat, quick=quick)
        # Ecrit apres chaque modele : un plantage au 6e ne fait pas perdre les 5 premiers.
        path = save_results(payload, out_dir)
        print("  -> " + str(path))
    return payload

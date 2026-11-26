"""Moteur du bench reponse : vitesse percue et langue de la reponse finale.

Le bench outils mesure le choix d'outil. Celui-ci mesure l'etape d'apres :
le modele recoit la question et les donnees renvoyees par les outils, et
redige ce que l'utilisateur lit. Ce modele n'a pas besoin de tool-calling —
il peut etre different de celui qui choisit les outils — d'ou un panel qui
inclut des generalistes sans tools.
"""

from __future__ import annotations

import statistics
from pathlib import Path

from common import ollama_client as oc
from common.helpers import machine_meta, save_results

from . import cases
from .report import fmt_s

MODELS = [
    # generalistes de 2 a 3,3 Go, candidats pour la seule reponse finale
    "gemma3:4b",          # Google, multilingue, pas de tool-calling dans Ollama
    "ministral-3:3b",     # Mistral, le plus petit de la famille
    "llama3.2:3b",        # Meta, multilingue officiel (fr, es...)
    "qwen3:4b-instruct",  # qwen3 sans raisonnement, version instruct
    "granite3.1-moe",     # le plus rapide du bench outils
    # deja au bench outils : pour savoir si un seul modele suffit
    "mistral",
    "qwen2.5:7b",
    "llama3.1:8b",
    "mistral-nemo:12b",
    "qwen3:8b!nothink",
]

# Au-dela, la reponse est coupee et marquee tronquee. Sans plafond, un 8B a
# 8 tok/s qui se lance dans un roman bloque le bench plusieurs minutes par cas.
MAX_TOKENS = 700


def _median(values):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 2) if values else None


def _rate(rows: list[dict]):
    total = sum(c["essais"] for c in rows)
    return round(100.0 * sum(c["reussites"] for c in rows) / total, 1) if total else None


def run_case(spec: oc.Spec, case: dict, temperature: float, seed: int) -> dict:
    data = oc.chat_stream(spec.model, cases.messages(case), think=spec.think,
                          temperature=temperature, seed=seed, max_tokens=MAX_TOKENS)
    if "_error" in data:
        return {"ok": False, "why": data["_error"], "metrics": {}, "answer": "",
                "truncated": False, "thinking_chars": 0}
    thinking, answer = oc.split_thinking(data)
    ok, why = cases.check_language(answer, case["lang"])
    return {"ok": ok, "why": why, "metrics": oc.metrics(data), "answer": answer,
            "truncated": data.get("done_reason") == "length",
            "thinking_chars": len(thinking)}


def run_model(spec: oc.Spec, repeat: int, temperature: float) -> dict:
    print("\n=== " + spec.label + " ===")
    print("  chargement...", flush=True)
    # On mesure le chargement plutot que de l'ignorer : si la reponse finale
    # est confiee a un autre modele que le tool-calling, et que les deux ne
    # tiennent pas ensemble en VRAM, chaque question paie ce chargement.
    load_s = oc.warmup(spec.model, think=spec.think)

    per_case: list[dict] = []
    runs_all: list[dict] = []
    for case in cases.CASES:
        runs = [run_case(spec, case, temperature, seed=oc.SEED + i) for i in range(repeat)]
        runs_all.extend(runs)
        ok = sum(1 for r in runs if r["ok"])
        failed = next((r for r in runs if not r["ok"]), None)
        per_case.append({
            "id": case["id"],
            "langue": case["lang"],
            "reussites": ok,
            "essais": len(runs),
            "pourquoi": failed["why"] if failed else "",
            "extrait_echec": failed["answer"][:160] if failed else "",
            "tronquees": sum(1 for r in runs if r["truncated"]),
            "ttft_s": _median([r["metrics"].get("ttft_s") for r in runs]),
            "total_s": _median([r["metrics"].get("total_s") for r in runs]),
            # La premiere reponse en entier : la langue ne dit rien de la
            # qualite, il faut pouvoir relire.
            "reponse": runs[0]["answer"],
        })
        mark = "OK " if ok == len(runs) else ("~~ " if ok else "KO ")
        tok = runs[0]["metrics"].get("output_tokens")
        line = ("  " + mark + case["id"].ljust(18)
                + "1er mot " + fmt_s(per_case[-1]["ttft_s"]).rjust(7)
                + "   total " + fmt_s(per_case[-1]["total_s"]).rjust(7)
                + "   " + (str(tok) if tok is not None else "?").rjust(4) + " tok")
        if per_case[-1]["pourquoi"]:
            line += "   " + per_case[-1]["pourquoi"][:70]
        print(line, flush=True)

    metrics = [r["metrics"] for r in runs_all]
    return {
        "cases": per_case,
        "scores": {
            "langue_fr": _rate([c for c in per_case if c["langue"] == "fr"]),
            "langue_autres": _rate([c for c in per_case if c["langue"] != "fr"]),
        },
        "perf": {
            "ttft_s_median": _median([m.get("ttft_s") for m in metrics]),
            "ttft_s_max": max((m["ttft_s"] for m in metrics if m.get("ttft_s") is not None), default=None),
            "total_s_median": _median([m.get("total_s") for m in metrics]),
            "gen_tok_s_median": _median([m.get("gen_tok_s") for m in metrics]),
            "tokens_median": _median([m.get("output_tokens") for m in metrics]),
            "chargement_s": round(load_s, 2) if load_s is not None else None,
            "tronquees": sum(1 for r in runs_all if r["truncated"]),
            "thinking_chars": sum(r["thinking_chars"] for r in runs_all),
            "appels": len(runs_all),
        },
    }


def run(models: list[str], repeat: int, temperature: float, out_dir: Path) -> dict:
    specs = oc.resolve_specs(models)
    print("A mesurer : " + ", ".join(s.label for s in specs))
    print("Repetitions : " + str(repeat) + "  |  temperature " + str(temperature)
          + "  |  " + str(len(cases.CASES) * repeat) + " appels par modele")

    payload = {
        "meta": {
            **machine_meta(),
            "repetitions": repeat,
            "temperature": temperature,
            "max_tokens": MAX_TOKENS,
            "min_share": cases.MIN_SHARE,
            "cas": [{"id": c["id"], "langue": c["lang"]} for c in cases.CASES],
        },
        "models": {},
    }
    for spec in specs:
        payload["models"][spec.label] = run_model(spec, repeat, temperature)
        # Ecrit apres chaque modele : un plantage au 8e ne fait pas perdre les 7 premiers.
        path = save_results(payload, out_dir)
        print("  -> " + str(path))
    return payload

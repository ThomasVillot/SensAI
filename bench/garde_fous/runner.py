"""Moteur du bench garde-fous : conformite des sorties structurees des deux controles d'entree.

Les deux controles (detection des donnees personnelles, controle de perimetre) demandent a
Ollama un JSON conforme a un schema, passe dans le champ `format`. Ce bench rejoue leurs
appels tels que le code de production les fait, N fois chacun, et compte les reponses
conformes.

Fidelite a la production — rien n'est recode, tout vient de src/ :
  * prompt systeme et version  : config/prompts.yaml via model_funcs._load_prompt
  * schema                     : config/schemas.yaml (config.schemas)
  * options (temperature 0,7...) : config/config.json  -> les essais varient vraiment ;
    un bench a temperature 0 + graine fixe renverrait 20 fois la meme reponse
  * client HTTP                : src.ollama_client.OllamaClient.chat_events
  * criteres de conformite     : contains_invalid_response_pattern, extract_json_in_content
    et schema_problems, dans l'ordre de request_response_model
  * `think` : le code de production ne l'envoie PAS pour ces deux appels (--think prod).
    --think off envoie think=false, pour mesurer ce que changerait de le couper.

Pourquoi ne pas appeler request_response_model directement : il renvoie None en cas
d'echec et jette le texte recu. Ici on garde la reponse brute pour expliquer chaque echec.
tests/test_bench_garde_fous.py verifie que les deux verdicts concordent.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

# Le bench vit dans bench/ ; le code de production est a la racine du depot.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import active_config  # noqa: E402
from src.model_funcs import _load_prompt, contains_invalid_response_pattern  # noqa: E402
from src.ollama_client import OllamaClient, OllamaError  # noqa: E402
from src.utils import extract_json_in_content, schema_problems  # noqa: E402

from common.helpers import machine_meta, save_results  # noqa: E402

from . import cases  # noqa: E402

THINK_MODES = {"prod": None, "off": False, "on": True}
RAW_CAP = 1500  # caracteres de reponse brute gardes par essai


# --------------------------------------------------------------------------
# Verdicts
# --------------------------------------------------------------------------

def evaluate_structure(raw: str, schema: dict) -> dict:
    """Meme verdict que request_response_model, mais en gardant la raison.

    status : ok | invalid_pattern | no_json | schema
    strict : la reponse entiere est un JSON valide, sans extraction (json.loads direct).
             La production tolere du texte autour du JSON ; `strict` dit si elle en a besoin.
    """
    try:
        json.loads(raw)
        strict = True
    except (json.JSONDecodeError, TypeError):
        strict = False

    if contains_invalid_response_pattern(raw):
        return {"status": "invalid_pattern", "detail": "debris technique ({function, <nil>...)",
                "strict": strict, "parsed": None}

    parsed = extract_json_in_content(raw)
    if parsed is None:
        return {"status": "no_json", "detail": "aucune donnee JSON valide", "strict": strict, "parsed": None}

    problems = schema_problems(parsed, schema)
    if problems:
        return {"status": "schema", "detail": problems[0], "strict": strict, "parsed": parsed}

    return {"status": "ok", "detail": "", "strict": strict, "parsed": parsed}


def judge_scope(parsed: dict, case: dict) -> tuple[bool | None, str]:
    """(plausible ?, classe rendue). None : cas sans attendu, juge sur la structure seule."""
    label = str(parsed.get("classification", "")).upper()
    if case["expected"] is None:
        return None, label
    return label in case["expected"], label


def judge_pii(parsed: dict, case: dict) -> tuple[bool | None, str]:
    items = [i for i in parsed.get("personal_information", []) if isinstance(i, dict)]
    found = [(str(i.get("type", "")).upper(), str(i.get("value", ""))) for i in items]
    problems = []

    if case["vide"] and found:
        problems.append("attendu vide, recu " + ", ".join(t + "=" + v for t, v in found))

    for needle, types in case["trouver"]:
        by_value = [(t, v) for t, v in found if needle.lower() in v.lower()]
        if not by_value:
            problems.append("non detecte : " + needle)
        elif types is not None and not any(t in types for t, _ in by_value):
            problems.append("mauvais type pour " + needle + " : " + by_value[0][0])

    for banned in case["eviter"]:
        if any(banned.lower() in v.lower() for _, v in found):
            problems.append("a tort masque : " + banned)

    return (not problems), "; ".join(problems)


JUDGES = {"pii": judge_pii, "scope": judge_scope}


# --------------------------------------------------------------------------
# Statistiques
# --------------------------------------------------------------------------

def wilson(k: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    """Intervalle de confiance a 95 % d'une proportion (Wilson).

    Sert a ne pas lire 20/20 comme « 100 % » : sur 20 essais, la vraie conformite peut
    encore etre d'environ 84 %.
    """
    if n == 0:
        return None, None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _pct(k: int, n: int) -> float | None:
    return round(100.0 * k / n, 1) if n else None


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1)], 2)


# --------------------------------------------------------------------------
# Appels
# --------------------------------------------------------------------------

def messages_for(guard: str, case: dict, version_of: dict) -> list[dict]:
    """Les deux messages que detect_sensitive_information / detect_message_scope envoient."""
    prompt = cases.GUARDS[guard]["prompt"]
    return [
        {"role": "system", "content": _load_prompt(prompt, version_of[prompt])},
        {"role": "user", "content": case["text"]},
    ]


def run_call(client, model: str, messages: list[dict], options: dict, schema: dict,
             think: bool | None) -> dict:
    started = time.perf_counter()
    try:
        raw = "".join(
            event["content"]
            for event in client.chat_events(model, messages, options, think=think, format=schema)
            if event["type"] == "token"
        )
    except OllamaError as exc:
        return {"status": "transport", "detail": str(exc)[:200], "strict": False, "parsed": None,
                "raw": "", "total_s": time.perf_counter() - started}

    verdict = evaluate_structure(raw, schema)
    verdict.update(raw=raw, total_s=time.perf_counter() - started)
    return verdict


def run_case(client, model: str, guard: str, case: dict, repeat: int, config, think) -> dict:
    schema = config.schemas[cases.GUARDS[guard]["schema"]]
    version_of = {"pii_check": config.pii_check_prompt_version,
                  "scope_check": config.scope_check_prompt_version}
    messages = messages_for(guard, case, version_of)

    trials = []
    for _ in range(repeat):
        trial = run_call(client, model, messages, config.options, schema, think)
        if trial["status"] == "ok":
            trial["sem_ok"], trial["sem_detail"] = JUDGES[guard](trial["parsed"], case)
        else:
            trial["sem_ok"], trial["sem_detail"] = None, ""
        trials.append(trial)

    answered = [t for t in trials if t["status"] != "transport"]
    statuses = Counter(t["status"] for t in trials)
    judged = [t for t in trials if t["sem_ok"] is not None]
    failures = Counter(
        t["status"] + " : " + t["detail"] for t in trials if t["status"] not in ("ok", "transport")
    )

    return {
        "id": case["id"],
        "famille": case["famille"],
        "essais": len(trials),
        "repondus": len(answered),
        "conformes": statuses["ok"],
        "json_strict": sum(1 for t in answered if t["strict"]),
        "statuts": dict(statuses),
        "transport": statuses["transport"],
        "semantique_ok": sum(1 for t in judged if t["sem_ok"]),
        "semantique_jugees": len(judged),
        "echecs": dict(failures),
        "ecarts_semantiques": dict(Counter(t["sem_detail"] for t in judged if not t["sem_ok"])),
        "avec_balise_think": sum(1 for t in answered if "<think" in t["raw"].lower()),
        "latence_s": [round(t["total_s"], 3) for t in answered],
        # Les reponses brutes : une conformite se discute sur pieces.
        "reponses": [{"statut": t["status"], "detail": t["detail"], "brut": t["raw"][:RAW_CAP]}
                     for t in trials],
    }


def totals(case_rows: list[dict]) -> dict:
    repondus = sum(c["repondus"] for c in case_rows)
    conformes = sum(c["conformes"] for c in case_rows)
    judged = sum(c["semantique_jugees"] for c in case_rows)
    sem_ok = sum(c["semantique_ok"] for c in case_rows)
    lat = [x for c in case_rows for x in c["latence_s"]]
    low, high = wilson(conformes, repondus)
    statuses: Counter = Counter()
    for c in case_rows:
        statuses.update(c["statuts"])
    return {
        "essais": sum(c["essais"] for c in case_rows),
        "repondus": repondus,
        "transport": sum(c["transport"] for c in case_rows),
        "conformes": conformes,
        "conformite_pct": _pct(conformes, repondus),
        "ic95_bas_pct": round(100 * low, 1) if low is not None else None,
        "ic95_haut_pct": round(100 * high, 1) if high is not None else None,
        "json_strict_pct": _pct(sum(c["json_strict"] for c in case_rows), repondus),
        "semantique_pct": _pct(sem_ok, judged),
        "semantique_jugees": judged,
        "statuts": dict(statuses),
        "avec_balise_think": sum(c["avec_balise_think"] for c in case_rows),
        "latence_mediane_s": round(statistics.median(lat), 2) if lat else None,
        "latence_p95_s": _percentile(lat, 0.95),
    }


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def _fingerprint(config) -> dict:
    """Empreinte des prompts et schemas mesures : rattache le chiffre a la config qui l'a produit."""
    def short(text: str) -> str:
        return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]

    out = {}
    for guard, info in cases.GUARDS.items():
        version = (config.pii_check_prompt_version if guard == "pii" else config.scope_check_prompt_version)
        out[guard] = {
            "prompt": info["prompt"] + ":" + version,
            "prompt_sha": short(_load_prompt(info["prompt"], version)),
            "schema": info["schema"],
            "schema_sha": short(json.dumps(config.schemas[info["schema"]], sort_keys=True)),
        }
    return out


def _warmup(client, model: str, options: dict) -> float | None:
    """Charge le modele pour que le chargement ne gonfle pas la latence du 1er essai."""
    try:
        for event in client.chat_events(model, [{"role": "user", "content": "ok"}], options):
            if event["type"] == "done":
                return event["result"].load_s
    except OllamaError as exc:
        print("  [avertissement] chargement : " + str(exc))
    return None


def run(models: list[str] | None, repeat: int, out_dir: Path, think_mode: str = "prod",
        guards: list[str] | None = None, case_ids: list[str] | None = None,
        threshold: float = 99.0, client=None) -> dict:
    config = active_config()
    client = client or OllamaClient(config.ollama_host, config.connect_timeout_s, config.read_timeout_s)
    think = THINK_MODES[think_mode]
    guards = guards or list(cases.GUARDS)
    models = models or [config.pii_check_model]

    selected = {
        g: [c for c in cases.GUARDS[g]["cases"] if not case_ids or c["id"] in case_ids]
        for g in guards
    }
    n_cases = sum(len(v) for v in selected.values())
    if not n_cases:
        raise SystemExit("Aucun cas ne correspond a --cases.")

    # Ollama injoignable : l'erreur est deja lisible (OllamaError), on la laisse remonter.
    try:
        client.list_models()
    except OllamaError as exc:
        raise SystemExit(str(exc))

    payload = {
        "meta": {
            **machine_meta(),
            "repetitions": repeat,
            "options": config.options,
            "think": think_mode,
            "seuil_pct": threshold,
            "empreintes": _fingerprint(config),
            "cas": {g: [{"id": c["id"], "famille": c["famille"]} for c in selected[g]] for g in guards},
            "partiel": True,
        },
        "models": {},
    }
    print("Garde-fous : " + ", ".join(cases.GUARDS[g]["label"] for g in guards))
    print("Repetitions : " + str(repeat) + "  |  options " + json.dumps(config.options)
          + "  |  think : " + think_mode)
    print(str(n_cases * repeat) + " appels par modele")

    try:
        for name in models:
            try:
                client.ensure_model(name)
            except OllamaError as exc:
                print("[skip] " + name + " : " + str(exc).splitlines()[0])
                continue

            print("\n=== " + name + " ===")
            print("  chargement...", flush=True)
            entry = {"chargement_s": _warmup(client, name, config.options), "guards": {}}
            payload["models"][name] = entry

            for guard in guards:
                print("\n  -- " + cases.GUARDS[guard]["label"])
                rows = []
                entry["guards"][guard] = {"cases": rows, "totals": totals(rows)}
                for case in selected[guard]:
                    row = run_case(client, name, guard, case, repeat, config, think)
                    rows.append(row)
                    entry["guards"][guard]["totals"] = totals(rows)
                    _print_case(row)
                    save_results(payload, out_dir)  # une coupure ne perd pas les cas deja mesures
    except KeyboardInterrupt:
        print("\nInterrompu : les cas deja mesures sont conserves.")

    payload["meta"]["partiel"] = _is_partial(payload, selected, models)
    save_results(payload, out_dir)
    return payload


def _is_partial(payload: dict, selected: dict, models: list[str]) -> bool:
    for name in models:
        entry = payload["models"].get(name)
        if entry is None:
            continue  # modele ignore (absent) : pas une coupure
        for guard, wanted in selected.items():
            if len(entry["guards"].get(guard, {}).get("cases", [])) < len(wanted):
                return True
    return False


def _print_case(row: dict) -> None:
    mark = "OK " if row["conformes"] == row["essais"] else ("~~ " if row["conformes"] else "KO ")
    line = ("    " + mark + row["id"].ljust(24)
            + (str(row["conformes"]) + "/" + str(row["essais"])).rjust(6) + " conformes"
            + "   strict " + (str(row["json_strict"]) + "/" + str(row["repondus"])).rjust(6))
    if row["semantique_jugees"]:
        line += "   plausibles " + (str(row["semantique_ok"]) + "/" + str(row["semantique_jugees"])).rjust(6)
    if row["transport"]:
        line += "   ERREURS RESEAU " + str(row["transport"])
    if row["echecs"]:
        line += "   " + next(iter(row["echecs"]))[:60]
    print(line, flush=True)

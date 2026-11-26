"""Rapport texte du bench garde-fous."""

from __future__ import annotations

from pathlib import Path

from . import cases

# Ce que devient un echec de conformite dans src/model_funcs.py. Constat lu dans le code,
# pas une mesure : request_response_model renvoie None, et chaque appelant le traite a sa facon.
CONSEQUENCES = {
    "pii": "echec = detect_sensitive_information renvoie [] : le message part SANS masquage "
           "du LLM (seule la regex email / telephone / IP peut encore le rattraper).",
    "scope": "echec = detect_message_scope renvoie OUT_OF_SCOPE : le message est refuse "
             "(faux refus, pas de fuite).",
}


def _pct(v) -> str:
    return "n/a" if v is None else (str(v) + "%")


def _s(v) -> str:
    return "n/a" if v is None else (str(v) + "s")


def _verdict(t: dict, threshold: float) -> str:
    if t["conformite_pct"] is None:
        return "non mesure (aucune reponse)"
    ok = t["conformite_pct"] >= threshold
    text = ("ATTEINT" if ok else "NON ATTEINT") + " (" + str(t["conformite_pct"]) + "% pour un seuil de "
    text += str(threshold) + "%), IC95 [" + _pct(t["ic95_bas_pct"]) + " ; " + _pct(t["ic95_haut_pct"]) + "]"
    return text


def build(payload: dict) -> str:
    meta = payload["meta"]
    threshold = meta["seuil_pct"]
    lines: list[str] = []
    w = 92

    def rule(char="="):
        lines.append(char * w)

    rule()
    lines.append("BENCH GARDE-FOUS — conformite des sorties structurees — SensAI")
    rule()
    lines.append("Date          : " + meta["date"])
    lines.append("Machine       : " + meta["machine"] + "  (" + meta["os"] + ")")
    lines.append("Repetitions   : " + str(meta["repetitions"]) + " essais par message")
    lines.append("Options       : " + str(meta["options"]) + "   think : " + meta["think"]
                 + ("  (= comme la production : non envoye)" if meta["think"] == "prod" else ""))
    for guard, fp in meta["empreintes"].items():
        lines.append("  " + guard.ljust(5) + " prompt " + fp["prompt"] + " [" + fp["prompt_sha"]
                     + "]   schema " + fp["schema"] + " [" + fp["schema_sha"] + "]")
    if meta.get("partiel"):
        lines.append("")
        lines.append("/!\\ MESURE PARTIELLE : le bench a ete interrompu, certains cas manquent.")
    lines.append("")
    lines.append("Conforme = la reponse passe les memes controles que le code de production")
    lines.append("(pas de debris technique, JSON extractible, valide contre le schema).")
    lines.append("JSON strict = la reponse entiere est deja un JSON (sans extraction ni texte autour).")
    lines.append("Plausible = en plus, la valeur est sensee (secondaire : une sortie toujours vide")
    lines.append("serait conforme a 100 % sans rien detecter).")

    for model, entry in payload["models"].items():
        lines.append("")
        rule()
        load = entry.get("chargement_s")
        lines.append("MODELE " + model + ("   (chargement " + _s(round(load, 1)) + ")" if load else ""))
        rule()

        lines.append("")
        lines.append("Synthese".ljust(24) + "conforme".rjust(14) + "IC95".rjust(16) + "strict".rjust(9)
                     + "plausible".rjust(11) + "med".rjust(8) + "p95".rjust(8))
        lines.append("-" * w)
        for guard, data in entry["guards"].items():
            t = data["totals"]
            ratio = str(t["conformes"]) + "/" + str(t["repondus"])
            ic = "[" + str(t["ic95_bas_pct"]) + "-" + str(t["ic95_haut_pct"]) + "]"
            lines.append(cases.GUARDS[guard]["label"].ljust(24)
                         + (ratio + " " + _pct(t["conformite_pct"])).rjust(14)
                         + ic.rjust(16)
                         + _pct(t["json_strict_pct"]).rjust(9)
                         + _pct(t["semantique_pct"]).rjust(11)
                         + _s(t["latence_mediane_s"]).rjust(8)
                         + _s(t["latence_p95_s"]).rjust(8))
            if t["transport"]:
                lines.append("   /!\\ " + str(t["transport"]) + " appel(s) sans reponse (Ollama, reseau, "
                             "timeout) : exclus du taux, a relancer.")
            if t["avec_balise_think"]:
                lines.append("   " + str(t["avec_balise_think"]) + " reponse(s) contiennent une balise <think> "
                             "dans le contenu.")

        for guard, data in entry["guards"].items():
            t = data["totals"]
            lines.append("")
            lines.append(cases.GUARDS[guard]["label"].upper() + " — par message")
            lines.append("-" * w)
            lines.append("cas".ljust(26) + "famille".ljust(20) + "conformes".rjust(10) + "strict".rjust(9)
                         + "plausibles".rjust(12) + "  motif d'echec")
            for c in data["cases"]:
                sem = (str(c["semantique_ok"]) + "/" + str(c["semantique_jugees"])
                       if c["semantique_jugees"] else "-")
                reason = next(iter(c["echecs"]), "")
                lines.append(c["id"].ljust(26) + c["famille"].ljust(20)
                             + (str(c["conformes"]) + "/" + str(c["repondus"])).rjust(10)
                             + (str(c["json_strict"]) + "/" + str(c["repondus"])).rjust(9)
                             + sem.rjust(12) + "  " + reason[:34])

            gaps = [(c["id"], k, n) for c in data["cases"] for k, n in c["ecarts_semantiques"].items()]
            if gaps:
                lines.append("")
                lines.append("Ecarts de contenu (reponses conformes mais pas plausibles) :")
                for case_id, text, n in gaps[:8]:
                    lines.append("  " + case_id.ljust(24) + str(n) + "x  " + text[:56])

            failures = [(c["id"], k, n) for c in data["cases"] for k, n in c["echecs"].items()]
            lines.append("")
            if failures:
                lines.append("Echecs de conformite (" + str(sum(n for *_, n in failures)) + ") :")
                for case_id, text, n in failures[:8]:
                    lines.append("  " + case_id.ljust(24) + str(n) + "x  " + text[:58])
                sample = next((r for c in data["cases"] for r in c["reponses"] if r["statut"] not in ("ok", "transport")), None)
                if sample:
                    lines.append("  exemple de reponse brute : " + repr(sample["brut"][:140]))
                lines.append("  " + CONSEQUENCES[guard])
            else:
                lines.append("Aucun echec de conformite sur " + str(t["repondus"]) + " reponses.")

            lines.append("")
            lines.append("Objectif " + str(threshold) + "% : " + _verdict(t, threshold))

    lines.append("")
    rule("-")
    lines.append("Lecture de l'IC95 : sur " + str(meta["repetitions"]) + " essais par message, un 100 % observe est "
                 "compatible avec une vraie conformite\nplus basse (20/20 -> borne basse d'environ 84 %). "
                 "La ligne de synthese regroupe tous les messages : c'est elle\nqui permet de conclure, pas un cas isole.")
    return "\n".join(lines)


def write(payload: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "rapport.txt"
    path.write_text(build(payload) + "\n", encoding="utf-8")
    return path

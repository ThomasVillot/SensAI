"""Rapport texte du bench outils, a coller dans l'ADR-001."""

from __future__ import annotations

from pathlib import Path

CATS = [
    ("outils", "Tool-calling"),
    ("vitesse", "Vitesse"),
    ("json", "JSON structure"),
    ("raisonnement", "Raisonnement"),
    ("consignes", "Consignes"),
    ("francais", "Francais"),
    ("stabilite", "Stabilite"),
]


def _bar(value, width: int = 20) -> str:
    if value is None:
        return "?" * width
    filled = int(round(width * value / 100.0))
    return "#" * filled + "." * (width - filled)


def _fmt(value) -> str:
    return "  n/a" if value is None else (str(value).rjust(5))


def build(payload: dict) -> str:
    meta = payload["meta"]
    models = payload["models"]
    names = list(models)
    lines: list[str] = []
    w = 78

    def rule(char="="):
        lines.append(char * w)

    rule()
    lines.append("BENCHMARK MODELES OLLAMA — SensAI")
    rule()
    lines.append("Date          : " + meta["date"])
    lines.append("Machine       : " + meta["machine"] + "  (" + meta["os"] + ")")
    lines.append("Python        : " + meta["python"])
    lines.append("Repetitions   : " + str(meta["repetitions"]) + " par cas de test")
    n_cases = len(next(iter(models.values()))["cases"]) if models else 0
    lines.append("Mode          : " + str(meta.get("mode")) + " (" + str(n_cases) + " cas)")
    lines.append("Modeles       : " + ", ".join(names))
    lines.append("")

    # --- classement general -------------------------------------------------
    rule("-")
    lines.append("CLASSEMENT GENERAL")
    rule("-")
    ranked = sorted(names, key=lambda m: models[m]["global"], reverse=True)
    for rank, m in enumerate(ranked, 1):
        g = models[m]["global"]
        lines.append(str(rank) + ". " + m.ljust(22) + str(g).rjust(5) + " /100   " + _bar(g))
    lines.append("")
    lines.append("Ponderation : " + ", ".join(
        k + " " + str(int(v * 100)) + "%" for k, v in meta["ponderation"].items()))
    lines.append("")

    # --- detail par categorie ----------------------------------------------
    rule("-")
    lines.append("SCORES PAR CATEGORIE")
    rule("-")
    header = "Categorie".ljust(16) + "".join(m[:13].rjust(15) for m in names)
    lines.append(header)
    lines.append("-" * len(header))
    for key, label in CATS:
        row = label.ljust(16)
        best = max((models[m]["scores"].get(key) or -1) for m in names)
        for m in names:
            v = models[m]["scores"].get(key)
            cell = _fmt(v)
            if v is not None and v == best and len(names) > 1:
                cell = cell + " *"
            else:
                cell = cell + "  "
            row += cell.rjust(15)
        lines.append(row)
    lines.append("")
    lines.append("* = meilleur de la categorie")
    lines.append("")

    # --- performances brutes ------------------------------------------------
    rule("-")
    lines.append("PERFORMANCES MESUREES")
    rule("-")
    perf_rows = [
        ("Debit median (tok/s)", "gen_tok_s_median"),
        ("Latence mediane (s)", "latence_s_median"),
        ("Latence p90 (s)", "latence_s_p90"),
        ("Latence max (s)", "latence_s_max"),
        ("Appels mesures", "appels"),
        ("Caracteres de reflexion", "thinking_chars"),
        ("Outils en tool_calls natif", "outils_natifs"),
        ("Outils bricoles en texte", "outils_texte"),
    ]
    lines.append("Mesure".ljust(26) + "".join(m[:13].rjust(15) for m in names))
    lines.append("-" * (26 + 15 * len(names)))
    for label, key in perf_rows:
        row = label.ljust(26)
        for m in names:
            row += str(models[m]["perf"].get(key)).rjust(15)
        lines.append(row)
    lines.append("")

    # --- projection sur une boucle ReAct ------------------------------------
    rule("-")
    lines.append("PROJECTION SUR LA BOUCLE ReAct (feature A1)")
    rule("-")
    lines.append("A1 enchaine 3 a 5 appels modele pour une seule question utilisateur.")
    lines.append("")
    lines.append("Modele".ljust(22) + "1 tour".rjust(10) + "3 appels".rjust(12)
                 + "5 appels".rjust(12) + "   verdict")
    lines.append("-" * 74)
    for m in ranked:
        lat = models[m]["perf"].get("latence_s_median") or 0
        t3, t5 = lat * 3, lat * 5
        if t5 <= 20:
            verdict = "confortable"
        elif t5 <= 45:
            verdict = "jouable, a surveiller"
        else:
            verdict = "INJOUABLE en demo"
        lines.append(m.ljust(22) + (str(round(lat, 1)) + "s").rjust(10)
                     + (str(round(t3, 1)) + "s").rjust(12)
                     + (str(round(t5, 1)) + "s").rjust(12) + "   " + verdict)
    lines.append("")

    # --- echecs par modele --------------------------------------------------
    rule("-")
    lines.append("CAS ECHOUES OU INSTABLES")
    rule("-")
    for m in names:
        bad = [c for c in models[m]["cases"] if c["taux"] < 1]
        lines.append("")
        lines.append(m + "  (" + str(len(bad)) + " cas non parfaits)")
        if not bad:
            lines.append("   tout est passe sur tous les essais")
            continue
        for c in sorted(bad, key=lambda x: x["taux"]):
            tag = "[KO]" if c["taux"] == 0 else "[~~]"
            lines.append("   " + tag + " " + c["id"].ljust(16)
                         + str(c["reussites"]) + "/" + str(c["essais"]) + "  "
                         + c["pourquoi"][:60])
    lines.append("")

    # --- recommandation -----------------------------------------------------
    rule("=")
    lines.append("LECTURE")
    rule("=")
    best = ranked[0]
    best_tools = max(names, key=lambda m: models[m]["scores"].get("outils") or 0)
    best_speed = max(names, key=lambda m: models[m]["scores"].get("vitesse") or 0)
    lines.append("Meilleur score global      : " + best)
    lines.append("Meilleur en tool-calling   : " + best_tools
                 + "  (" + str(models[best_tools]["scores"].get("outils")) + "/100)")
    lines.append("Le plus rapide             : " + best_speed
                 + "  (" + str(models[best_speed]["perf"].get("latence_s_median")) + "s par tour)")
    lines.append("")
    lines.append("Rappel des seuils du projet :")
    lines.append("  - tool-calling > 80/100 sinon T1 est compromis (GO/NO-GO du 02/10)")
    lines.append("  - une question avec boucle A1 doit rester sous ~20s en demo")
    lines.append("")
    lines.append("Un modele a 0 en tool_calls natif a quand meme pu choisir le bon")
    lines.append("outil : il l'a ecrit en JSON dans le texte. C'est jouable, mais T1")
    lines.append("devra embarquer un parseur de repli, et le format varie d'un modele")
    lines.append("a l'autre. A poids egal, preferer un modele qui remplit tool_calls.")
    lines.append("")
    lines.append("Ces chiffres sont valables pour CETTE machine uniquement.")
    lines.append("Refais tourner le benchmark sur chaque poste du groupe.")
    rule("=")
    return "\n".join(lines)


def write(payload: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "rapport.txt"
    path.write_text(build(payload), encoding="utf-8")
    return path

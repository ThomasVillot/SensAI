"""Rapport texte du bench reponse, a coller dans l'ADR-001."""

from __future__ import annotations

from pathlib import Path


def fmt_s(v) -> str:
    return "n/a" if v is None else str(round(v, 1)) + "s"


def _pct(v) -> str:
    return "n/a" if v is None else str(int(round(v))) + "%"


def _num(v) -> str:
    return "n/a" if v is None else str(v)


def build(payload: dict) -> str:
    meta = payload["meta"]
    models = payload["models"]
    cas = meta["cas"]
    n_fr = sum(1 for c in cas if c["langue"] == "fr")
    lines: list[str] = []
    w = 78

    def rule(char="="):
        lines.append(char * w)

    rule()
    lines.append("BENCH REPONSE FINALE — vitesse et langue — SensAI")
    rule()
    lines.append("Date          : " + meta["date"])
    lines.append("Machine       : " + meta["machine"] + "  (" + meta["os"] + ")")
    lines.append("Repetitions   : " + str(meta["repetitions"]) + " par cas, temperature "
                 + str(meta["temperature"]) + ", plafond " + str(meta["max_tokens"]) + " tokens")
    lines.append("Cas           : " + str(len(cas)) + " (" + str(n_fr) + " en francais, "
                 + str(len(cas) - n_fr) + " en EN/ES)")
    lines.append("")

    # Langue d'abord : un modele qui change de langue est inutilisable, quelle
    # que soit sa vitesse. Ensuite le delai avant le premier mot.
    def key(name):
        m = models[name]
        return (-(m["scores"]["langue_fr"] or 0), -(m["scores"]["langue_autres"] or 0),
                m["perf"]["ttft_s_median"] if m["perf"]["ttft_s_median"] is not None else 999)
    ranked = sorted(models, key=key)

    rule("-")
    lines.append("CLASSEMENT  (langue francaise d'abord, puis delai avant le premier mot)")
    rule("-")
    lines.append("Modele".ljust(22) + "Langue".rjust(8) + "EN/ES".rjust(7) + "1er mot".rjust(9)
                 + "Complete".rjust(10) + "Debit".rjust(8) + "Tokens".rjust(8) + "Charg.".rjust(8))
    lines.append("".ljust(22) + "FR".rjust(8) + "".rjust(7) + "median".rjust(9)
                 + "median".rjust(10) + "tok/s".rjust(8) + "median".rjust(8) + "".rjust(8))
    lines.append("-" * w)
    for name in ranked:
        s, p = models[name]["scores"], models[name]["perf"]
        lines.append(name[:21].ljust(22) + _pct(s["langue_fr"]).rjust(8)
                     + _pct(s["langue_autres"]).rjust(7) + fmt_s(p["ttft_s_median"]).rjust(9)
                     + fmt_s(p["total_s_median"]).rjust(10) + _num(p["gen_tok_s_median"]).rjust(8)
                     + _num(p["tokens_median"]).rjust(8) + fmt_s(p["chargement_s"]).rjust(8))
    lines.append("")
    lines.append("1er mot  : delai avant le premier mot affiche (ce que l'utilisateur ressent)")
    lines.append("Complete : duree jusqu'au dernier mot")
    lines.append("Charg.   : chargement du modele en memoire, paye a chaque changement de modele")
    lines.append("")

    # --- grille par cas ---------------------------------------------------
    rule("-")
    lines.append("DETAIL PAR CAS   (OK = bonne langue ; KO = mauvaise langue, melange ou erreur)")
    rule("-")
    lines.append("Modele".ljust(22) + "".join(str(i + 1).rjust(5) for i in range(len(cas)))
                 + "   tronquees")
    lines.append("-" * (22 + 5 * len(cas) + 12))
    for name in ranked:
        by_id = {c["id"]: c for c in models[name]["cases"]}
        row = name[:21].ljust(22)
        for c in cas:
            r = by_id.get(c["id"])
            if r is None:
                cell = "-"
            elif r["reussites"] == r["essais"]:
                cell = "OK"
            elif r["reussites"] == 0:
                cell = "KO"
            else:
                cell = str(r["reussites"]) + "/" + str(r["essais"])
            row += cell.rjust(5)
        lines.append(row + str(models[name]["perf"]["tronquees"]).rjust(12))
    lines.append("")
    for i, c in enumerate(cas):
        lines.append("  " + str(i + 1) + " = " + c["id"])
    lines.append("")

    # --- echecs -------------------------------------------------------------
    rule("-")
    lines.append("ECHECS DE LANGUE")
    rule("-")
    any_fail = False
    for name in ranked:
        bad = [c for c in models[name]["cases"] if c["reussites"] < c["essais"]]
        if not bad:
            continue
        any_fail = True
        lines.append("")
        lines.append(name)
        for c in bad:
            lines.append("   [" + c["id"] + "] " + c["pourquoi"][:90])
            extrait = " ".join(c["extrait_echec"].split())[:110]
            if extrait:
                lines.append("      \"" + extrait + "...\"")
    if not any_fail:
        lines.append("aucun : tous les modeles ont repondu dans la bonne langue partout")
    lines.append("")

    # --- lecture ------------------------------------------------------------
    rule("=")
    lines.append("LECTURE")
    rule("=")
    clean = [n for n in ranked if models[n]["scores"]["langue_fr"] == 100]
    if clean:
        best = clean[0]
        lines.append("Le plus reactif sans faute de francais : " + best + "  ("
                     + fmt_s(models[best]["perf"]["ttft_s_median"]) + " avant le 1er mot, "
                     + _num(models[best]["perf"]["gen_tok_s_median"]) + " tok/s)")
    else:
        lines.append("Aucun modele n'a repondu en francais sur tous les cas francais.")
    lines.append("")
    lines.append("La reponse finale s'affiche en streaming. On lit environ 4 mots/s,")
    lines.append("soit ~6 a 8 tok/s en francais : au-dessus, le texte arrive plus vite")
    lines.append("qu'on ne le lit et seul le delai avant le 1er mot se ressent. En")
    lines.append("dessous, l'utilisateur attend le texte.")
    lines.append("")
    lines.append("Deux modeles (un pour les outils, un pour la reponse) : s'ils ne")
    lines.append("tiennent pas ensemble dans la VRAM, chaque question paie la colonne")
    lines.append("Charg. en plus. Le verifier avec 'ollama ps' avant de trancher.")
    lines.append("")
    lines.append("Ce bench ne juge ni la fidelite aux donnees ni la qualite du texte :")
    lines.append("relire les reponses completes dans resultats.json (champ 'reponse').")
    lines.append("La detection de langue est heuristique (mots-outils), seuil de melange")
    lines.append("a " + str(int(meta["min_share"] * 100)) + "% de mots dans la langue attendue.")
    rule("=")
    return "\n".join(lines)


def write(payload: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "rapport.txt"
    path.write_text(build(payload), encoding="utf-8")
    return path

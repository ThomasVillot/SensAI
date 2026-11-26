"""Cas de test du bench outils, regroupes par categorie, et leur ponderation.

Tout est declaratif : pour ajouter un test, ajoute une entree dans la liste
correspondante. Les verificateurs renvoient (bool, str) — le booleen dit si
c'est reussi, la chaine explique pourquoi en cas d'echec.
"""

from __future__ import annotations

import re

from common.helpers import norm

# --------------------------------------------------------------------------
# Outils exposes au modele (le domaine du projet : planificateur de voyages)
# --------------------------------------------------------------------------

WEATHER = {
    "type": "function",
    "function": {
        "name": "weather",
        "description": "Obtenir la meteo et les previsions d'une ville a une date donnee",
        "parameters": {
            "type": "object",
            "properties": {"ville": {"type": "string"}, "date": {"type": "string"}},
            "required": ["ville"],
        },
    },
}

ACTIVITY = {
    "type": "function",
    "function": {
        "name": "activity_search",
        "description": "Trouver des activites, restaurants, hotels ou musees dans une ville",
        "parameters": {
            "type": "object",
            "properties": {"ville": {"type": "string"}, "activity_type": {"type": "string"}},
            "required": ["ville", "activity_type"],
        },
    },
}

FLIGHTS = {
    "type": "function",
    "function": {
        "name": "flights_search",
        "description": "Trouver des vols entre deux villes a des dates donnees",
        "parameters": {
            "type": "object",
            "properties": {
                "ville_depart": {"type": "string"},
                "destination": {"type": "string"},
                "date_depart": {"type": "string"},
            },
            "required": ["ville_depart", "destination"],
        },
    },
}

BUDGET = {
    "type": "function",
    "function": {
        "name": "budget",
        "description": "Calculer le budget restant d'un voyage a partir des depenses enregistrees",
        "parameters": {
            "type": "object",
            "properties": {"voyage_id": {"type": "string"}},
            "required": ["voyage_id"],
        },
    },
}

GITHUB = {
    "type": "function",
    "function": {
        "name": "github_commit",
        "description": "Recupere les derniers commits d'un depot GitHub",
        "parameters": {
            "type": "object",
            "properties": {"owner": {"type": "string"}, "repo_name": {"type": "string"}},
            "required": ["owner", "repo_name"],
        },
    },
}

ALL_TOOLS = [WEATHER, ACTIVITY, FLIGHTS, BUDGET]

# --------------------------------------------------------------------------
# 1. OUTILS — le modele choisit-il le bon outil, et seulement quand il faut ?
# --------------------------------------------------------------------------
# expected : nom de l'outil attendu, None = aucun appel ne doit etre emis
# tools    : outils exposes pour ce cas (par defaut ALL_TOOLS)
# args     : cles d'arguments qui doivent etre presentes (optionnel)

TOOL_CASES = [
    # -- appel simple, un seul outil disponible
    {"id": "T-simple", "msg": "Quel temps fait-il a Lisbonne ?", "tools": [WEATHER],
     "expected": "weather", "args": ["ville"]},
    # -- bon choix parmi plusieurs
    {"id": "T-meteo-1", "msg": "Quel temps fera-t-il a Lisbonne du 10 au 15 avril ?", "expected": "weather"},
    {"id": "T-meteo-2", "msg": "Est-ce qu'il va pleuvoir a Tokyo demain ?", "expected": "weather"},
    {"id": "T-meteo-3", "msg": "Je pars a Marrakech la semaine prochaine, que dois-je prevoir cote temperatures ?", "expected": "weather"},
    {"id": "T-vol-1", "msg": "Quels sont les vols disponibles entre Paris et New York le 12 novembre ?", "expected": "flights_search"},
    {"id": "T-vol-2", "msg": "Trouve-moi un aller-retour Paris-Lisbonne du 5 au 12 juin.", "expected": "flights_search"},
    {"id": "T-vol-3", "msg": "Je voudrais partir a New York en octobre depuis Paris, tu peux m'aider ?", "expected": "flights_search"},
    {"id": "T-act-1", "msg": "Quels restaurants italiens y a-t-il pres du Colisee a Rome ?", "expected": "activity_search"},
    {"id": "T-act-2", "msg": "Trouve-moi des musees a visiter a Amsterdam.", "expected": "activity_search"},
    {"id": "T-act-3", "msg": "Je vais passer trois jours a Barcelone et j'aimerais savoir quoi faire.", "expected": "activity_search"},
    {"id": "T-budget", "msg": "Combien me reste-t-il sur le budget du voyage VOY-42 ?", "expected": "budget"},
    # -- aucun outil ne doit etre appele
    {"id": "T-none-1", "msg": "Bonjour, peux-tu m'aider a preparer mon prochain voyage ?", "expected": None},
    {"id": "T-none-2", "msg": "J'aime les voyages culturels et les petites villes.", "expected": None},
    {"id": "T-none-3", "msg": "Merci pour ton aide, je reviendrai quand j'aurai choisi.", "expected": None},
    {"id": "T-none-4", "msg": "Peux-tu m'expliquer comment construire un itineraire efficace ?", "expected": None},
    # -- l'outil necessaire n'existe pas : il ne doit rien inventer
    {"id": "T-absent", "msg": "Quelle est la meteo a Oslo le 5 avril ?", "tools": [GITHUB], "expected": None},
    # -- piege : vocabulaire de vol mais intention meteo
    {"id": "T-piege", "msg": "Est-ce que le climat est agreable a Faro au mois de mai ?", "expected": "weather"},
]

# Cas multi-outils : plusieurs appels attendus dans la meme reponse
MULTI_TOOL_CASES = [
    {"id": "M-deux", "expected_set": {"weather", "activity_search"},
     "msg": "Je pars a Lisbonne 5 jours a partir du 6 mai. Donne-moi la meteo et des activites interessantes."},
]

# --------------------------------------------------------------------------
# 2. JSON — sortie structuree conforme a un schema (feature T5)
# --------------------------------------------------------------------------

_ITINERAIRE_SCHEMA = {
    "type": "object",
    "properties": {
        "ville": {"type": "string"},
        "date_depart": {"type": "string"},
        "duree_jours": {"type": "integer"},
        "budget_eur": {"type": "number"},
    },
    "required": ["ville", "duree_jours", "budget_eur"],
}

JSON_CASES = [
    {
        "id": "J-extraction",
        "msg": ("Extrais les informations de ce message et renvoie UNIQUEMENT du JSON : "
                "\"Je pars a Lisbonne le 14 mars pour 5 jours avec 600 euros\""),
        "schema": _ITINERAIRE_SCHEMA,
        "expect": {"ville": "lisbonne", "duree_jours": 5, "budget_eur": 600},
    },
    {
        "id": "J-incomplet",
        "msg": ("Extrais les informations de ce message et renvoie UNIQUEMENT du JSON. "
                "Mets null pour ce qui est absent : \"J'aimerais aller a Porto une semaine\""),
        "schema": {
            "type": "object",
            "properties": {"ville": {"type": "string"}, "duree_jours": {"type": "integer"}},
            "required": ["ville"],
        },
        "expect": {"ville": "porto"},
    },
]


def validate_schema(obj, schema) -> tuple[bool, str]:
    """Validation minimale : type racine, cles requises, types des proprietes."""
    if schema.get("type") == "object" and not isinstance(obj, dict):
        return False, "racine attendue objet, recu " + type(obj).__name__

    missing = [k for k in schema.get("required", []) if k not in obj]
    if missing:
        return False, "cles manquantes : " + ", ".join(missing)

    py = {"string": str, "integer": int, "number": (int, float),
          "array": list, "object": dict, "boolean": bool}
    for key, spec in (schema.get("properties") or {}).items():
        if key not in obj or obj[key] is None:
            continue
        expected = py.get(spec.get("type"))
        if expected and not isinstance(obj[key], expected):
            if spec.get("type") == "integer" and isinstance(obj[key], bool):
                return False, key + " : booleen recu au lieu d'un entier"
            return False, key + " : attendu " + str(spec.get("type")) + ", recu " + type(obj[key]).__name__
    return True, ""


def check_json_values(obj, expect) -> tuple[bool, str]:
    for key, want in expect.items():
        got = obj.get(key)
        if isinstance(want, str):
            if norm(got) != norm(want):
                return False, key + " : attendu " + str(want) + ", recu " + str(got)
        else:
            try:
                if abs(float(got) - float(want)) > 0.01:
                    return False, key + " : attendu " + str(want) + ", recu " + str(got)
            except (TypeError, ValueError):
                return False, key + " : valeur non numerique (" + str(got) + ")"
    return True, ""


# --------------------------------------------------------------------------
# 3. RAISONNEMENT — reponses verifiables, pas de jugement subjectif
# --------------------------------------------------------------------------

def _numbers(text: str) -> list[float]:
    # Espaces insecables utilises comme separateurs de milliers : "1 500".
    cleaned = text.replace("\u202f", "").replace("\u00a0", "")
    cleaned = re.sub(r"(?<=\d)[ ,](?=\d{3}\b)", "", cleaned)
    return [float(n.replace(",", ".")) for n in re.findall(r"-?\d+(?:[.,]\d+)?", cleaned)]


def _expect_number(value: float, tol: float = 0.01):
    def check(answer: str) -> tuple[bool, str]:
        nums = _numbers(answer)
        if not nums:
            return False, "aucun nombre dans la reponse"
        if any(abs(n - value) <= tol for n in nums):
            return True, ""
        return False, "attendu " + str(value) + ", nombres trouves : " + str(nums[:5])
    return check


def _expect_text(*variants: str):
    def check(answer: str) -> tuple[bool, str]:
        a = norm(answer)
        if any(norm(v) in a for v in variants):
            return True, ""
        return False, "attendu l'un de " + str(list(variants))
    return check


REASONING_CASES = [
    {"id": "R-budget",
     "msg": ("Un vol coute 120 euros et l'hotel 65 euros par nuit pour 4 nuits. "
             "Mon budget total est de 600 euros. Combien me reste-t-il ? "
             "Reponds uniquement par le nombre."),
     "check": _expect_number(220)},
    {"id": "R-remise",
     "msg": ("L'hotel coute 80 euros la nuit, je reserve 3 nuits et j'ai 10 pour cent "
             "de remise sur le total. Combien je paie ? Reponds uniquement par le nombre."),
     "check": _expect_number(216)},
    {"id": "R-groupe",
     "msg": ("Un train Lisbonne-Porto coute 25 euros par personne et par trajet. "
             "Nous sommes 3 et nous faisons l'aller-retour. Total ? "
             "Reponds uniquement par le nombre."),
     "check": _expect_number(150)},
    {"id": "R-contrainte",
     "msg": ("J'ai repere 5 activites qui durent chacune une journee entiere, "
             "mais je ne reste que 3 jours. Combien dois-je en retirer ? "
             "Reponds uniquement par le nombre."),
     "check": _expect_number(2)},
    {"id": "R-date",
     "msg": ("Je pars le 14 mars et je reste 5 nuits a l'hotel. "
             "Quelle est la date de ma derniere nuit ? "
             "Reponds uniquement par la date au format JJ/MM."),
     "check": _expect_text("18/03", "18-03", "18 mars")},
    {"id": "R-tri",
     "msg": ("Lisbonne est a 2h de vol, Rome a 2h30, Athenes a 3h15. "
             "Quelle destination est la plus proche ? Reponds uniquement par le nom de la ville."),
     "check": _expect_text("lisbonne")},
]

# --------------------------------------------------------------------------
# 4. CONSIGNES — respect strict du format demande
# --------------------------------------------------------------------------

def _check_oui_non(answer: str) -> tuple[bool, str]:
    a = norm(answer).strip(" .!\n")
    if a in ("oui", "non"):
        return (a == "oui"), ("a repondu " + a if a != "oui" else "")
    return False, "reponse non conforme : " + repr(answer[:60])


def _check_trois_tirets(answer: str) -> tuple[bool, str]:
    lines = [l.strip() for l in answer.splitlines() if l.strip()]
    if len(lines) != 3:
        return False, str(len(lines)) + " lignes au lieu de 3"
    bad = [l for l in lines if not l.startswith(("-", "–", "•", "*"))]
    if bad:
        return False, "ligne sans tiret : " + repr(bad[0][:40])
    return True, ""


def _check_max_mots(n: int):
    def check(answer: str) -> tuple[bool, str]:
        words = [w for w in re.split(r"\s+", answer.strip()) if w]
        if len(words) <= n:
            return True, ""
        return False, str(len(words)) + " mots au lieu de " + str(n) + " max"
    return check


INSTRUCTION_CASES = [
    {"id": "C-oui-non",
     "msg": "Reponds uniquement par le mot OUI ou le mot NON, sans rien ajouter. Lisbonne est-elle la capitale du Portugal ?",
     "check": _check_oui_non},
    {"id": "C-trois-lignes",
     "msg": "Donne exactement 3 activites a faire a Rome. Une par ligne, chaque ligne commencant par un tiret. Rien d'autre.",
     "check": _check_trois_tirets},
    {"id": "C-concision",
     "msg": "En 10 mots maximum : pourquoi visiter Porto au printemps ?",
     "check": _check_max_mots(10)},
]

# --------------------------------------------------------------------------
# 5. FRANCAIS — le modele repond-il en francais, sans deriver vers l'anglais ?
# --------------------------------------------------------------------------

_FR = {"le", "la", "les", "des", "une", "un", "est", "sont", "pour", "dans", "avec",
       "vous", "nous", "que", "qui", "plus", "tres", "sur", "par", "aux", "cette"}
_EN = {"the", "and", "you", "with", "for", "this", "that", "your", "from", "have",
       "will", "can", "there", "here", "about", "would", "should", "they"}


def _check_francais(answer: str) -> tuple[bool, str]:
    words = re.findall(r"[a-zA-ZÀ-ÿ]+", answer.lower())
    if len(words) < 8:
        return False, "reponse trop courte pour juger (" + str(len(words)) + " mots)"
    fr = sum(1 for w in words if w in _FR)
    en = sum(1 for w in words if w in _EN)
    if fr == 0 and en == 0:
        return False, "langue indeterminee"
    if en > fr:
        return False, "derive vers l'anglais (" + str(en) + " marqueurs EN / " + str(fr) + " FR)"
    return True, ""


FRENCH_CASES = [
    {"id": "F-conseil",
     "msg": "Donne-moi trois conseils pour preparer un voyage de 5 jours a Lisbonne.",
     "check": _check_francais},
    {"id": "F-explication",
     "msg": "Explique en quelques phrases pourquoi le mois de mars est une bonne periode pour visiter le Portugal.",
     "check": _check_francais},
]

# --------------------------------------------------------------------------
# Triage et ponderation
# --------------------------------------------------------------------------

# Sous-ensemble utilise par `--quick` : de quoi eliminer un mauvais candidat
# en quelques minutes, avant de lancer la suite complete sur les survivants.
# 14 cas au lieu de 31, en gardant au moins un representant de chaque famille.
QUICK_IDS = {
    "T-simple", "T-meteo-1", "T-vol-1", "T-act-1", "T-budget",
    "T-none-1", "T-none-3", "T-absent", "M-deux",
    "J-extraction",
    "R-budget", "R-contrainte",
    "C-oui-non",
    "F-conseil",
}

# Ponderation du score global. Justifiee par le scope du projet :
# T1/T3/A1 reposent entierement sur le tool-calling, et A1 multiplie les
# appels modele par 3 a 5, donc la vitesse pese lourd. A ajuster et a
# defendre dans l'ADR-001.
WEIGHTS = {
    "outils": 0.30,
    "vitesse": 0.25,
    "json": 0.15,
    "raisonnement": 0.15,
    "consignes": 0.08,
    "francais": 0.07,
}

"""Cas de test du bench reponse, et la detection de langue qui les juge.

Chaque cas simule l'etape qui suit l'appel d'outil : le modele recoit la
question de l'utilisateur et les donnees renvoyees par les outils, et doit
rediger la reponse finale dans la langue de la question.

Les questions et les donnees sont ecrites avec leurs accents, comme un vrai
utilisateur et une vraie API les enverraient : c'est la langue qu'on teste.
"""

from __future__ import annotations

import json
import re

from common.helpers import norm

# Gabarit repris du prototype actuel (question + ressources dans un seul
# message user), plus une consigne de langue explicite. Le gabarit est en
# francais : pour les cas EN et ES, c'est justement ce qu'on teste — le modele
# suit-il la langue de la question ou celle du gabarit ?
PROMPT = (
    "L'utilisateur te demande :\n{question}\n\n"
    "Voici les ressources nécessaires à la réponse que tu dois lui apporter :\n{donnees}\n\n"
    "Formule bien ta réponse avec ces ressources. "
    "Réponds dans la langue de la question de l'utilisateur."
)


def messages(case: dict) -> list[dict]:
    donnees = json.dumps(case["data"], ensure_ascii=False, indent=2)
    return [{"role": "user",
             "content": PROMPT.format(question=case["question"], donnees=donnees)}]


# --------------------------------------------------------------------------
# Cas de test
# --------------------------------------------------------------------------
# lang : langue attendue de la reponse ("fr", "en", "es")
# data : ce que les outils auraient renvoye. La plupart des API de voyage
#        repondent en anglais : c'est le piege principal, un modele qui recopie
#        les donnees derive vers l'anglais en cours de reponse.

CASES = [
    # -- francais, donnees en francais : la base, tout le monde doit passer
    {"id": "FR-meteo", "lang": "fr",
     "question": "Quel temps fera-t-il à Lisbonne ce week-end ?",
     "data": {"weather": {"ville": "Lisbonne", "previsions": [
         {"jour": "samedi 27/09", "min_c": 16, "max_c": 24, "ciel": "ensoleillé", "pluie_mm": 0},
         {"jour": "dimanche 28/09", "min_c": 15, "max_c": 21, "ciel": "averses l'après-midi", "pluie_mm": 4},
     ]}}},

    # -- francais, donnees brutes d'API en anglais
    {"id": "FR-meteo-en", "lang": "fr",
     "question": "Est-ce qu'il va pleuvoir à Porto demain ? Je dois prendre un parapluie ?",
     "data": {"weather": {"location": "Porto, Portugal", "date": "2026-09-25", "forecast": {
         "summary": "Light rain showers in the morning, clearing up in the afternoon",
         "temperature_min_c": 14, "temperature_max_c": 20,
         "precipitation_probability": 70, "precipitation_mm": 3.2,
         "wind": "Moderate breeze from the west, 25 km/h"}}}},

    {"id": "FR-vols-en", "lang": "fr",
     "question": "Trouve-moi un vol Paris-Lisbonne le 5 juin.",
     "data": {"flights_search": {"origin": "CDG", "destination": "LIS", "date": "2027-06-05", "offers": [
         {"carrier": "TAP Air Portugal", "flight": "TP437", "departure": "07:15", "arrival": "08:50",
          "stops": 0, "price": {"total": 142.30, "currency": "EUR"},
          "baggage": "Cabin bag only, checked bag extra"},
         {"carrier": "Air France", "flight": "AF1024", "departure": "12:40", "arrival": "14:20",
          "stops": 0, "price": {"total": 189.00, "currency": "EUR"},
          "baggage": "1 checked bag included"},
         {"carrier": "Transavia", "flight": "TO7630", "departure": "18:05", "arrival": "19:45",
          "stops": 0, "price": {"total": 96.50, "currency": "EUR"},
          "baggage": "Personal item only"},
     ]}}},

    {"id": "FR-activites-en", "lang": "fr",
     "question": "Que faire à Rome avec des enfants ?",
     "data": {"activity_search": {"city": "Rome", "type": "family", "results": [
         {"name": "Explora - Children's Museum of Rome", "rating": 4.5, "price": "9 EUR per child",
          "description": "Interactive museum designed for children under 12, with hands-on exhibits "
                         "about science, the city and the environment."},
         {"name": "Bioparco di Roma", "rating": 4.3, "price": "18 EUR adult, 14 EUR child",
          "description": "The city zoo inside Villa Borghese park, home to more than 1,000 animals. "
                         "Great for a half-day visit."},
         {"name": "Time Elevator Rome", "rating": 4.4, "price": "12 EUR",
          "description": "5D cinema experience that takes you through 3,000 years of Roman history "
                         "in 45 minutes."},
         {"name": "Villa Borghese Gardens", "rating": 4.7, "price": "Free entry",
          "description": "Large landscaped park where you can rent bikes, rowing boats and pedal carts."},
     ]}}},

    # -- reponse longue : la derive vers l'anglais arrive souvent tard
    {"id": "FR-itineraire-en", "lang": "fr",
     "question": "Prépare-moi un itinéraire de 3 jours à Barcelone du 14 au 16 mai en tenant compte de la météo.",
     "data": {
         "weather": {"location": "Barcelona", "forecast": [
             {"date": "2027-05-14", "summary": "Sunny", "max_c": 24},
             {"date": "2027-05-15", "summary": "Thunderstorms expected in the afternoon", "max_c": 21},
             {"date": "2027-05-16", "summary": "Partly cloudy", "max_c": 23}]},
         "activity_search": {"city": "Barcelona", "results": [
             {"name": "Sagrada Familia", "indoor": True,
              "description": "Gaudi's unfinished basilica. Book tickets in advance, visits last about 1.5 hours."},
             {"name": "Park Guell", "indoor": False,
              "description": "Colourful park designed by Gaudi with panoramic views over the city."},
             {"name": "Picasso Museum", "indoor": True,
              "description": "More than 4,000 works from the artist's early years, in the Born district."},
             {"name": "Barceloneta Beach", "indoor": False,
              "description": "The city's most popular beach, lined with seafood restaurants."},
             {"name": "La Boqueria Market", "indoor": True,
              "description": "Famous covered food market on La Rambla, best visited in the morning."}]},
     }},

    # -- l'outil n'a rien trouve et le dit en anglais
    {"id": "FR-vide-en", "lang": "fr",
     "question": "Y a-t-il un vol direct Lille-Tokyo le 3 mars ?",
     "data": {"flights_search": {"status": "NO_RESULTS", "origin": "LIL", "destination": "HND",
                                 "date": "2027-03-03",
                                 "message": "No direct flights found for this route and date. "
                                            "Connecting flights are available via CDG or AMS."}}},

    # -- autres langues : le modele suit-il la question plutot que le gabarit ?
    {"id": "EN-meteo-fr", "lang": "en",
     "question": "What's the weather going to be like in Nice tomorrow? Should I plan a beach day?",
     "data": {"weather": {"ville": "Nice", "date": "demain", "ciel": "grand soleil", "min_c": 19,
                          "max_c": 27, "vent": "brise légère", "temperature_mer_c": 23, "indice_uv": 7}}},

    {"id": "ES-activites-en", "lang": "es",
     "question": "¿Qué puedo visitar en Madrid si solo tengo un día?",
     "data": {"activity_search": {"city": "Madrid", "results": [
         {"name": "Prado Museum", "price": "15 EUR, free 6-8pm",
          "description": "One of the world's great art museums: Velazquez, Goya, El Greco. "
                         "Allow at least 2-3 hours."},
         {"name": "Retiro Park", "price": "Free",
          "description": "Large central park with a lake where you can rent rowing boats."},
         {"name": "Royal Palace of Madrid", "price": "14 EUR",
          "description": "Official residence of the Spanish royal family, with 3,418 rooms."},
         {"name": "Mercado de San Miguel", "price": "Pay per dish",
          "description": "Historic iron market with tapas stalls, ideal for lunch."},
     ]}}},
]


# --------------------------------------------------------------------------
# Detection de langue
# --------------------------------------------------------------------------
# Heuristique par mots-outils, comme outils/cases._check_francais, etendue a trois
# langues. Les mots communs a deux langues (fr/es : "la", "de", "en", "que",
# "un", "y", "son", "si", "se", "te"...) sont exclus, sinon ils brouillent le
# compte. On travaille sur le texte sans accents.

_MARKERS = {
    "fr": {"le", "les", "des", "une", "et", "est", "sont", "pour", "dans", "avec", "vous",
           "nous", "qui", "plus", "tres", "sur", "aux", "cette", "ces", "pas", "ne", "mais",
           "votre", "vos", "du", "au", "il", "elle", "ils", "leur", "sera", "fait", "aussi",
           "peut", "etre", "ou", "je", "ce", "donc", "car"},
    "en": {"the", "and", "you", "with", "for", "this", "that", "your", "from", "have", "will",
           "can", "there", "here", "about", "would", "should", "they", "is", "are", "of", "to",
           "in", "at", "it", "be", "which", "also", "we", "if", "not"},
    "es": {"el", "los", "las", "del", "una", "por", "para", "con", "es", "esta", "estan", "muy",
           "pero", "tambien", "hay", "su", "sus", "lo", "como", "mas", "al", "puedes", "tiene",
           "este", "esto", "dia"},
}

# Lettres qui n'existent que dans une des langues. Elles sauvent les reponses
# en liste ("Depart a 07h15 / Prix : 142 EUR") qui n'ont presque pas de
# mots-outils. Le e accent aigu est commun au francais et a l'espagnol
# ("tambien", "despues") : il ne compte pour personne.
_LETTERS = {
    "fr": set("àâçèêëîïôùûœ"),
    "es": set("áíóúñ¿¡"),
}

# En dessous de ce nombre d'indices, on ne se prononce pas.
MIN_EVIDENCE = 3

# Cyrillique, kana, CJK, hangul. Les modeles qwen basculent parfois en chinois
# au milieu d'une reponse : un seul caractere suffit a la rendre inutilisable.
_NON_LATIN = re.compile(r"[Ѐ-ӿ぀-ヿ㐀-鿿가-힯]")

# Part minimale de mots-outils dans la langue attendue. En dessous, la reponse
# melange deux langues (typiquement des descriptions d'API recopiees telles
# quelles) : on la compte comme un echec, l'utilisateur le verrait.
MIN_SHARE = 0.75


def detect(text: str) -> tuple[str | None, dict[str, int]]:
    counts = {lang: 0 for lang in _MARKERS}
    # Mots entiers sans chiffre : sinon le vol "TO7630" compte un "to" anglais.
    for word in re.findall(r"\b[^\W\d_]+\b", text.lower()):
        plain = norm(word)
        lang = next((l for l, m in _MARKERS.items() if plain in m), None)
        if lang is None:
            lang = next((l for l, chars in _LETTERS.items() if chars & set(word)), None)
        if lang is not None:
            counts[lang] += 1
    if sum(counts.values()) < MIN_EVIDENCE:
        return None, counts
    return max(counts, key=counts.get), counts


def check_language(answer: str, expected: str) -> tuple[bool, str]:
    """Renvoie (reussi, pourquoi)."""
    if not answer.strip():
        return False, "reponse vide"

    alien = _NON_LATIN.findall(answer)
    if alien:
        return False, "caracteres non latins : " + "".join(alien[:12])

    lang, counts = detect(answer)
    detail = " / ".join(k.upper() + " " + str(v) for k, v in counts.items())
    if lang is None:
        return False, "langue indeterminee (" + detail + ")"
    if lang != expected:
        return False, "repond en " + lang.upper() + " au lieu de " + expected.upper() + " (" + detail + ")"

    share = counts[lang] / sum(counts.values())
    if share < MIN_SHARE:
        return False, "melange : " + str(round(100 * share)) + "% " + lang.upper() + " (" + detail + ")"
    return True, ""

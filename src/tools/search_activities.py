import re

from .trip_db import normalize_key
from .trip_memory import rejected_places
from .web_search import web_search_deep

SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\s+·\s+")


def without_rejected_places(results, rejected: list[dict]):
    if not rejected or not isinstance(results, list):
        return results
    keys = [normalize_key(place["name"]) for place in rejected]

    def mentions_rejected(text: str) -> bool:
        padded = f" {normalize_key(text)} "
        return any(f" {key} " in padded for key in keys)

    def cleaned(item):
        if not isinstance(item, dict):
            return item
        if mentions_rejected(str(item.get("title") or "")):
            return None
        content = item.get("content")
        if not isinstance(content, str):
            return item
        lines = []
        for line in content.split("\n"):
            sentences = [part for part in SENTENCE_BREAK.split(line) if not mentions_rejected(part)]
            if sentences:
                lines.append(" ".join(sentences))
        if not lines:
            return None
        return {**item, "content": "\n".join(lines)}

    names = ", ".join(place["name"] for place in rejected)
    note = {"note": f"L'utilisateur a refusé : {names}. Ne propose pas ces lieux, même sous un autre nom."}
    return [note, *[item for item in map(cleaned, results) if item is not None]]


def search_activities(city: str, month: str, preferences: str | None = None):
    query = f"Activities in {city.capitalize()} in {month.capitalize()}"
    if preferences and preferences.strip():
        query += f": {preferences.strip()}"
    return without_rejected_places(web_search_deep(query), rejected_places())


import re

from . import trip_db
from .trip_db import DOCUMENT_MODES, DOCUMENT_SECTIONS, MAX_SECTION_LEN, SECTION_TITLES, TripError, normalize_key

from .trip_memory import _active_trip, _choice, _given, _guarded, _public_trip, _require_trip, _result

_HEADING = re.compile(r"^ {0,3}#{1,6}(\s|$)", re.MULTILINE)


def _section_text(value) -> str:
    if not isinstance(value, str):
        raise TripError("Le champ 'content' doit être un texte.")
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    text = "\n".join(line.rstrip() for line in lines).strip()
    if not text:
        raise TripError("Le champ 'content' ne peut pas être vide.", "Écris le texte à mettre dans la section.")
    if _HEADING.search(text):
        raise TripError(
            "Le contenu d'une section ne peut pas contenir de titre Markdown (ligne qui commence par #).",
            "Les titres sont gérés par le document. Envoie seulement le texte de la section visée, "
            "sans titre et sans le texte des autres sections.",
        )
    return text


_ITEM_SEPARATOR = re.compile(r"\s*[,;]\s*|\s+et\s+")


def _starts_with_capital(text: str) -> bool:
    first = next((char for char in text if char.isalnum()), "")
    return first.isupper()


def _line_parts(line: str) -> tuple[list[str], list[str]]:
    parts, separators, start = [], [], 0
    for match in _ITEM_SEPARATOR.finditer(line):
        before = line[: match.start()]
        if before.count("(") > before.count(")") or not _starts_with_capital(line[match.end() :]):
            continue
        parts.append(line[start : match.start()])
        separators.append(match.group())
        start = match.end()
    parts.append(line[start:])
    return parts, separators


def _join_parts(parts: list[str], separators: list[str], removed: set[int]) -> str:
    kept = [i for i in range(len(parts)) if i not in removed]
    return "".join((separators[i - 1] if n else "") + parts[i] for n, i in enumerate(kept)).strip()


def _contains(key: str, needle: str) -> bool:
    return bool(needle) and f" {needle} " in f" {key} "


def _without(section: str, old: str | None, text: str) -> tuple[str | None, list[str]]:
    if old is None:
        raise TripError(f"La section '{section}' est vide : il n'y a rien à retirer.", "Lis le document avec document_get.")
    lines = old.split("\n")
    keys = [normalize_key(line) for line in lines]
    listed = " | ".join(line.strip() for line in lines if line.strip())
    dropped, removed = set(), []
    for item in text.split("\n"):
        needle = normalize_key(item)
        matches = [i for i, key in enumerate(keys) if i not in dropped and _contains(key, needle)]
        exact = [i for i in matches if keys[i] == needle]
        if len(matches) > 1 and len(exact) == 1:
            matches = exact
        if not matches:
            raise TripError(
                f"Aucune ligne de la section '{section}' ne contient « {item.strip()} ».",
                f"Lignes de la section : {listed}. Renvoie mode=remove avec le texte de la ligne à retirer.",
            )
        if len(matches) > 1:
            raise TripError(
                f"Plusieurs lignes de la section '{section}' contiennent « {item.strip()} ».",
                f"Lignes concernées : {' | '.join(lines[i].strip() for i in matches)}. "
                "Renvoie mode=remove avec le texte complet de la ligne à retirer.",
            )
        index = matches[0]
        parts, separators = _line_parts(lines[index])
        hit = {n for n, part in enumerate(parts) if _contains(normalize_key(part), needle)}
        if hit and len(hit) < len(parts):  # la ligne liste d'autres elements : on ne retire que celui-la
            removed += [parts[n].strip() for n in sorted(hit)]
            lines[index] = _join_parts(parts, separators, hit)
            keys[index] = normalize_key(lines[index])
        else:
            removed.append(lines[index].strip())
            dropped.add(index)
    new = "\n".join(line for i, line in enumerate(lines) if i not in dropped).strip()
    return new or None, removed


def _filled_sections(conn, trip) -> dict[str, int]:
    rows = conn.execute("SELECT section, length(content) AS size FROM document_sections WHERE trip_id = ?", (trip["id"],))
    sizes = {row["section"]: row["size"] for row in rows}
    return {name: sizes[name] for name in DOCUMENT_SECTIONS if name in sizes}



@_guarded
def document_edit(section: str, content: str, mode: str = "replace") -> dict:
    """Modifie UNE section du document d'itinéraire du voyage en cours (remplace son texte, y ajoute à la fin, ou en retire une ligne)."""
    section = _choice(section, "section", DOCUMENT_SECTIONS)
    mode = _choice(mode, "mode", DOCUMENT_MODES) if _given(mode) else "replace"
    text = _section_text(content)

    with trip_db.connect() as conn:
        trip = _require_trip(conn)
        row = conn.execute(
            "SELECT content FROM document_sections WHERE trip_id = ? AND section = ?", (trip["id"], section)
        ).fetchone()
        old = row["content"] if row else None
        removed = []
        if mode == "remove":
            new, removed = _without(section, old, text)
        else:
            new = text if mode == "replace" or old is None else f"{old}\n{text}"
        if new is not None and len(new) > MAX_SECTION_LEN:
            raise TripError(
                f"La section '{section}' dépasserait {MAX_SECTION_LEN} caractères ({len(new)}).",
                "Raccourcis le texte, ou utilise mode=replace avec une version plus courte de la section.",
            )

        if new == old:
            action = "unchanged"
        else:
            if new is None:  # un remove a retire la derniere ligne : la section redevient vide
                conn.execute("DELETE FROM document_sections WHERE trip_id = ? AND section = ?", (trip["id"], section))
            else:
                conn.execute(
                    """INSERT INTO document_sections (trip_id, section, content, updated_at)
                       VALUES (?, ?, ?, datetime('now'))
                       ON CONFLICT (trip_id, section) DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at""",
                    (trip["id"], section, new),
                )
            conn.execute(
                """INSERT INTO document_edits (trip_id, section, mode, old_content, new_content, created_at)
                   VALUES (?, ?, ?, ?, ?, datetime('now'))""",
                (trip["id"], section, mode, old, new),
            )
            action = "created" if old is None else "updated"

        filled = _filled_sections(conn, trip)
        return _result(
            ok=True,
            action=action,
            section=section,
            mode=mode,
            removed=removed,
            characters=len(new or ""),
            filled_sections=filled,
            empty_sections=[name for name in DOCUMENT_SECTIONS if name not in filled],
        )


@_guarded
def document_get(section: str | None = None) -> dict:
    """Lit le document d'itinéraire du voyage en cours : toutes les sections, ou seulement celle demandée."""
    wanted = _choice(section, "section", DOCUMENT_SECTIONS) if _given(section) else None

    with trip_db.connect() as conn:
        trip = _require_trip(conn)
        rows = conn.execute("SELECT section, content FROM document_sections WHERE trip_id = ?", (trip["id"],)).fetchall()
        contents = {row["section"]: row["content"] for row in rows}
        names = (wanted,) if wanted else DOCUMENT_SECTIONS
        return {
            "ok": True,
            "destination": trip["destination"],
            "sections": {name: contents[name] for name in names if name in contents},
            "empty_sections": [name for name in names if name not in contents],
        }



def read_document() -> dict | None:
    with trip_db.connect() as conn:
        trip = _active_trip(conn)
        if trip is None:
            return None
        rows = conn.execute(
            "SELECT section, content, updated_at FROM document_sections WHERE trip_id = ?", (trip["id"],)
        ).fetchall()
    saved = {row["section"]: row for row in rows}
    return {
        "trip": _public_trip(trip),
        "sections": [
            {
                "name": name,
                "title": SECTION_TITLES[name],
                "content": saved[name]["content"] if name in saved else None,
                "updated_at": saved[name]["updated_at"] if name in saved else None,
            }
            for name in DOCUMENT_SECTIONS
        ],
    }


def _trip_line(trip: dict) -> str:
    parts = []
    if trip["start_date"] and trip["end_date"]:
        parts.append(f"Du {trip['start_date']} au {trip['end_date']}")
    elif trip["start_date"]:
        parts.append(f"À partir du {trip['start_date']}")
    elif trip["end_date"]:
        parts.append(f"Jusqu'au {trip['end_date']}")
    parts.append(f"{trip['travelers']} voyageur{'s' if trip['travelers'] > 1 else ''}")
    return " · ".join(parts)


def render_markdown(document: dict) -> str:
    blocks = [f"# {document['trip']['destination']}", _trip_line(document["trip"])]
    for section in document["sections"]:
        blocks.append(f"## {section['title']}\n\n{section['content'] or '_À compléter._'}")
    return "\n\n".join(blocks) + "\n"

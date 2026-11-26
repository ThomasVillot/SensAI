
import functools
import math
import re
import sqlite3
from datetime import date

from . import trip_db
from .trip_db import (
    CATEGORIES,
    COMMITTED_STATUSES,
    DECISIONS,
    EXPENSE_STATUSES,
    MAX_AMOUNT,
    MAX_NAME_LEN,
    MAX_REASON_LEN,
    MAX_TRAVELERS,
    PLACE_KINDS,
    TripError,
    normalize_key,
)

_DATE_FORMAT = re.compile(r"\d{4}-\d{2}-\d{2}")
_CURRENCY_FORMAT = re.compile(r"[A-Z]{3}")


def _today() -> date:
    return date.today()


def _guarded(function):
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except TripError as exc:
            result = {"error": exc.message, "tool": function.__name__}
            if exc.hint:
                result["hint"] = exc.hint
            return result
        except sqlite3.IntegrityError as exc:
            return {"error": f"La base a refusé l'écriture : {exc}", "tool": function.__name__}
        except (sqlite3.Error, OSError) as exc:
            return {"error": f"Base du voyage inaccessible : {exc}", "tool": function.__name__}

    return wrapper



def _given(value) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())


def _text(value, field: str, max_len: int = MAX_NAME_LEN) -> str:
    if not isinstance(value, str):
        raise TripError(f"Le champ '{field}' doit être un texte.")
    value = " ".join(value.split())
    if not value:
        raise TripError(f"Le champ '{field}' ne peut pas être vide.")
    if len(value) > max_len:
        raise TripError(f"Le champ '{field}' dépasse {max_len} caractères.", "Raccourcis le texte.")
    return value


def _choice(value, field: str, allowed: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value.strip().lower() not in allowed:
        raise TripError(f"Valeur invalide pour '{field}' : {value!r}.", f"Valeurs acceptées : {', '.join(allowed)}.")
    return value.strip().lower()


def _date(value, field: str) -> str:
    if not isinstance(value, str) or not _DATE_FORMAT.fullmatch(value.strip()):
        raise TripError(f"'{field}' doit être une date au format YYYY-MM-DD (reçu : {value!r}).")
    try:
        parsed = date.fromisoformat(value.strip())
    except ValueError:
        raise TripError(f"'{field}' n'est pas une date qui existe : {value!r}.") from None
    return parsed.isoformat()


def _trip_date(value, field: str) -> str:
    parsed = _date(value, field)
    today = _today()
    if parsed < today.isoformat():
        raise TripError(
            f"'{field}' ({parsed}) est dans le passé.",
            f"Nous sommes le {today.isoformat()} : vérifie l'année.",
        )
    return parsed


def _check_dates(start: str | None, end: str | None) -> None:
    if start is not None and end is not None and end < start:
        raise TripError(
            f"La date de fin ({end}) précède la date de début ({start}).",
            "Corrige l'une des deux dates.",
        )


def _currency(value) -> str:
    code = value.strip().upper() if isinstance(value, str) else value
    if not isinstance(code, str) or not _CURRENCY_FORMAT.fullmatch(code):
        raise TripError(f"Devise invalide : {value!r}.", "Utilise un code ISO 4217 à 3 lettres, par exemple EUR.")
    return code


def _count(value, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_TRAVELERS:
        raise TripError(f"'{field}' doit être un entier entre 1 et {MAX_TRAVELERS} (reçu : {value!r}).")
    return value


def _cents(value, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise TripError(f"'{field}' doit être un nombre (reçu : {value!r}).")
    if value <= 0:
        raise TripError(f"'{field}' doit être strictement positif (reçu : {value}).")
    if value > MAX_AMOUNT:
        raise TripError(f"'{field}' dépasse le maximum accepté ({MAX_AMOUNT}).", "Vérifie le montant.")
    cents = round(value * 100)
    if abs(value * 100 - cents) > 1e-6:
        raise TripError(f"'{field}' : deux décimales au plus (reçu : {value}).")
    return cents



def _result(**fields) -> dict:
    return {key: value for key, value in fields.items() if value not in (None, {}, [])}


def _money(cents: int | None) -> float | None:
    return None if cents is None else round(cents / 100, 2)


def _public_trip(row: sqlite3.Row) -> dict:
    return {
        "destination": row["destination"],
        "start_date": row["start_date"],
        "end_date": row["end_date"],
        "budget": _money(row["budget_cents"]),
        "currency": row["currency"],
        "travelers": row["travelers"],
    }


def _public_expense(row: sqlite3.Row, currency: str) -> dict:
    return {
        "label": row["label"],
        "category": row["category"],
        "amount": _money(row["amount_cents"]),
        "currency": currency,
        "status": row["status"],
    }


def _public_place(row: sqlite3.Row) -> dict:
    return {"name": row["name"], "kind": row["kind"], "decision": row["decision"], "reason": row["reason"]}


def _active_trip(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM trips WHERE active = 1").fetchone()


def _require_trip(conn: sqlite3.Connection) -> sqlite3.Row:
    trip = _active_trip(conn)
    if trip is None:
        raise TripError(
            "Aucun voyage n'est enregistré.",
            "Appelle d'abord trip_save avec la destination et la devise.",
        )
    return trip


def _summary(conn: sqlite3.Connection, trip: sqlite3.Row) -> dict:
    marks = ", ".join("?" for _ in COMMITTED_STATUSES)
    totals = conn.execute(
        f"""SELECT COALESCE(SUM(CASE WHEN status IN ({marks}) THEN amount_cents END), 0) AS committed,
                   COALESCE(SUM(CASE WHEN status NOT IN ({marks}) THEN amount_cents END), 0) AS planned
            FROM expenses WHERE trip_id = ?""",
        (*COMMITTED_STATUSES, *COMMITTED_STATUSES, trip["id"]),
    ).fetchone()
    per_category = conn.execute(
        f"""SELECT category,
                   COALESCE(SUM(CASE WHEN status IN ({marks}) THEN amount_cents END), 0) AS committed,
                   COALESCE(SUM(CASE WHEN status NOT IN ({marks}) THEN amount_cents END), 0) AS planned
            FROM expenses WHERE trip_id = ? GROUP BY category ORDER BY category""",
        (*COMMITTED_STATUSES, *COMMITTED_STATUSES, trip["id"]),
    ).fetchall()

    budget = trip["budget_cents"]
    committed, planned = totals["committed"], totals["planned"]
    summary = {
        "currency": trip["currency"],
        "budget": _money(budget),
        "committed": _money(committed),
        "planned": _money(planned),
        "remaining": _money(None if budget is None else budget - committed),
        "remaining_after_planned": _money(None if budget is None else budget - committed - planned),
        "by_category": {
            row["category"]: {"committed": _money(row["committed"]), "planned": _money(row["planned"])}
            for row in per_category
        },
    }
    if budget is None:
        summary["note"] = "Aucun budget n'est défini pour ce voyage : demande-le à l'utilisateur."
    return summary


def _budget_warnings(summary: dict) -> list[str]:
    currency = summary["currency"]
    if summary["remaining"] is not None and summary["remaining"] < 0:
        return [f"Le budget est dépassé de {-summary['remaining']:.2f} {currency} par les dépenses réservées ou payées."]
    if summary["remaining_after_planned"] is not None and summary["remaining_after_planned"] < 0:
        return [f"Avec les dépenses prévues, le budget serait dépassé de {-summary['remaining_after_planned']:.2f} {currency}."]
    return []



_TRIP_COLUMNS = ("destination", "start_date", "end_date", "budget_cents", "currency", "travelers")


def _trip_fields(destination, start_date, end_date, budget, currency, travelers) -> dict:
    fields: dict = {}
    if _given(destination):
        fields["destination"] = _text(destination, "destination")
    if _given(start_date):
        fields["start_date"] = _trip_date(start_date, "start_date")
    if _given(end_date):
        fields["end_date"] = _trip_date(end_date, "end_date")
    if _given(budget):
        fields["budget_cents"] = _cents(budget, "budget")
    if _given(currency):
        fields["currency"] = _currency(currency)
    if _given(travelers):
        fields["travelers"] = _count(travelers, "travelers")
    return fields


def _public_change(column: str, value):
    return _money(value) if column == "budget_cents" else value


@_guarded
def trip_save(
    destination: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    budget: float | None = None,
    currency: str | None = None,
    travelers: int | None = None,
    new_trip: bool = False,
) -> dict:
    """Crée le voyage en cours, ou met à jour ses informations (un seul voyage est actif à la fois)."""
    fields = _trip_fields(destination, start_date, end_date, budget, currency, travelers)

    with trip_db.connect() as conn:
        trip = _active_trip(conn)
        if trip is None or new_trip is True:
            return _create_trip(conn, fields, previous=trip)
        return _update_trip(conn, trip, fields)


def _create_trip(conn: sqlite3.Connection, fields: dict, previous: sqlite3.Row | None) -> dict:
    missing = [name for name in ("destination", "currency") if name not in fields]
    if missing:
        raise TripError(
            f"Pour créer un voyage il faut : {', '.join(missing)}.",
            "Demande-les à l'utilisateur si besoin, puis renseigne-les dans cet appel (devise : code à 3 lettres, ex. EUR).",
        )
    _check_dates(fields.get("start_date"), fields.get("end_date"))

    if previous is not None:
        conn.execute("UPDATE trips SET active = 0 WHERE id = ?", (previous["id"],))
    cursor = conn.execute(
        """INSERT INTO trips (destination, start_date, end_date, budget_cents, currency, travelers, active, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 1, datetime('now'))""",
        (
            fields["destination"],
            fields.get("start_date"),
            fields.get("end_date"),
            fields.get("budget_cents"),
            fields["currency"],
            fields.get("travelers", 1),
        ),
    )
    trip = conn.execute("SELECT * FROM trips WHERE id = ?", (cursor.lastrowid,)).fetchone()
    note = None
    if previous is not None:
        note = f"Le voyage précédent ({previous['destination']}) est conservé mais n'est plus le voyage en cours."
    return _result(ok=True, action="created", trip=_public_trip(trip), note=note)


def _update_trip(conn: sqlite3.Connection, trip: sqlite3.Row, fields: dict) -> dict:
    changed = {column: value for column, value in fields.items() if column in _TRIP_COLUMNS and trip[column] != value}

    if "destination" in fields and normalize_key(fields["destination"]) != normalize_key(trip["destination"]):
        attached = conn.execute(
            "SELECT (SELECT COUNT(*) FROM expenses WHERE trip_id = :id)"
            " + (SELECT COUNT(*) FROM places WHERE trip_id = :id)"
            " + (SELECT COUNT(*) FROM document_sections WHERE trip_id = :id)",
            {"id": trip["id"]},
        ).fetchone()[0]
        if attached:
            raise TripError(
                f"Le voyage en cours va à {trip['destination']} et contient déjà des dépenses, des lieux ou un itinéraire : "
                f"le passer à {fields['destination']} les laisserait sous la mauvaise destination.",
                "Si l'utilisateur change vraiment de destination, appelle trip_save avec new_trip=true (le voyage actuel "
                "est conservé). Sinon, n'envoie pas 'destination'.",
            )

    _check_dates(fields.get("start_date", trip["start_date"]), fields.get("end_date", trip["end_date"]))
    if "currency" in changed:
        used = conn.execute("SELECT COUNT(*) FROM expenses WHERE trip_id = ?", (trip["id"],)).fetchone()[0]
        if used:
            raise TripError(
                f"La devise du voyage ne peut plus changer : {used} dépense(s) sont déjà enregistrées en {trip['currency']}.",
                "Aucune conversion automatique. Garde la devise du voyage, ou ouvre un autre voyage avec new_trip=true.",
            )

    changes = {
        ("budget" if column == "budget_cents" else column): {
            "from": _public_change(column, trip[column]),
            "to": _public_change(column, value),
        }
        for column, value in changed.items()
    }
    if changed:
        assignments = ", ".join(f"{column} = ?" for column in changed)
        conn.execute(
            f"UPDATE trips SET {assignments}, updated_at = datetime('now') WHERE id = ?",
            (*changed.values(), trip["id"]),
        )
        trip = conn.execute("SELECT * FROM trips WHERE id = ?", (trip["id"],)).fetchone()

    summary = _summary(conn, trip) if {"budget_cents", "currency"} & set(changed) else None
    return _result(
        ok=True,
        action="updated" if changed else "unchanged",
        trip=_public_trip(trip),
        changes=changes,
        budget=summary,
        warnings=_budget_warnings(summary) if summary else [],
    )


@_guarded
def trip_get() -> dict:
    """Renvoie tout ce qui est enregistré sur le voyage en cours."""
    with trip_db.connect() as conn:
        trip = _active_trip(conn)
        if trip is None:
            return {"ok": True, "trip": None, "message": "Aucun voyage n'est enregistré pour le moment."}
        expenses = conn.execute("SELECT * FROM expenses WHERE trip_id = ? ORDER BY id", (trip["id"],)).fetchall()
        places = conn.execute("SELECT * FROM places WHERE trip_id = ? ORDER BY id", (trip["id"],)).fetchall()
        return {
            "ok": True,
            "trip": _public_trip(trip),
            "expenses": [_public_expense(row, trip["currency"]) for row in expenses],
            "places": {
                decision: [_public_place(row) for row in places if row["decision"] == decision] for decision in DECISIONS
            },
            "budget": _summary(conn, trip),
        }



def _similar_labels(key_a: str, key_b: str) -> bool:
    words_a, words_b = set(key_a.split()), set(key_b.split())
    shorter, longer = (words_a, words_b) if len(words_a) <= len(words_b) else (words_b, words_a)
    return len(shorter) >= 2 and shorter <= longer


@_guarded
def expense_save(
    label: str,
    amount: float,
    category: str,
    status: str | None = None,
    currency: str | None = None,
    distinct: bool = False,
) -> dict:
    """Ajoute une dépense au voyage en cours, ou met à jour celle qui a le même libellé et la même catégorie."""
    label = _text(label, "label")
    cents = _cents(amount, "amount")
    category = _choice(category, "category", CATEGORIES)
    status = _choice(status, "status", EXPENSE_STATUSES) if _given(status) else None
    currency = _currency(currency) if _given(currency) else None
    key = normalize_key(label)
    if not key:
        raise TripError("Le libellé doit contenir au moins une lettre ou un chiffre.")

    with trip_db.connect() as conn:
        trip = _require_trip(conn)
        if currency is not None and currency != trip["currency"]:
            raise TripError(
                f"Le voyage est en {trip['currency']} et la dépense en {currency} : aucune conversion n'est faite.",
                f"Donne le montant en {trip['currency']}.",
            )
        row = conn.execute(
            "SELECT * FROM expenses WHERE trip_id = ? AND category = ? AND label_key = ?",
            (trip["id"], category, key),
        ).fetchone()

        if row is None and distinct is not True:
            for other in conn.execute(
                "SELECT * FROM expenses WHERE trip_id = ? AND category = ?", (trip["id"], category)
            ):
                if _similar_labels(other["label_key"], key):
                    raise TripError(
                        f"Une dépense proche existe déjà ({category}) : '{other['label']}' "
                        f"({_money(other['amount_cents'])} {trip['currency']}, {other['status']}).",
                        f"Si c'est la même dépense, rappelle expense_save avec le libellé exact '{other['label']}'. "
                        "Si c'est bien une autre dépense, ajoute distinct=true.",
                    )

        changes: dict = {}
        if row is None:
            conn.execute(
                "INSERT INTO expenses (trip_id, category, label, label_key, amount_cents, status) VALUES (?, ?, ?, ?, ?, ?)",
                (trip["id"], category, label, key, cents, status or "planned"),
            )
            action = "created"
        else:
            if row["amount_cents"] != cents:
                changes["amount"] = {"from": _money(row["amount_cents"]), "to": _money(cents)}
            if status is not None and row["status"] != status:
                changes["status"] = {"from": row["status"], "to": status}
            if changes:  # le libelle d'origine est conserve : c'est la cle (normalisee) qui identifie la depense
                conn.execute(
                    "UPDATE expenses SET amount_cents = ?, status = ? WHERE id = ?",
                    (cents, status or row["status"], row["id"]),
                )
            action = "updated" if changes else "unchanged"

        saved = conn.execute(
            "SELECT * FROM expenses WHERE trip_id = ? AND category = ? AND label_key = ?",
            (trip["id"], category, key),
        ).fetchone()
        summary = _summary(conn, trip)
        return _result(
            ok=True,
            action=action,
            expense=_public_expense(saved, trip["currency"]),
            changes=changes,
            budget=summary,
            warnings=_budget_warnings(summary),
        )


@_guarded
def expense_delete(label: str, category: str | None = None) -> dict:
    """Supprime une dépense du voyage en cours."""
    label = _text(label, "label")
    category = _choice(category, "category", CATEGORIES) if _given(category) else None
    key = normalize_key(label)

    with trip_db.connect() as conn:
        trip = _require_trip(conn)
        rows = conn.execute(
            "SELECT * FROM expenses WHERE trip_id = ? AND label_key = ? AND (? IS NULL OR category = ?)",
            (trip["id"], key, category, category),
        ).fetchall()
        if not rows:
            existing = [row["label"] for row in conn.execute("SELECT label FROM expenses WHERE trip_id = ?", (trip["id"],))]
            raise TripError(
                f"Aucune dépense ne correspond à '{label}'.",
                f"Dépenses enregistrées : {', '.join(existing)}." if existing else "Aucune dépense n'est enregistrée.",
            )
        if len(rows) > 1:
            raise TripError(
                f"Plusieurs dépenses s'appellent '{label}' ({', '.join(row['category'] for row in rows)}).",
                "Précise la catégorie.",
            )
        conn.execute("DELETE FROM expenses WHERE id = ?", (rows[0]["id"],))
        summary = _summary(conn, trip)
        return {"ok": True, "action": "deleted", "expense": _public_expense(rows[0], trip["currency"]), "budget": summary}


@_guarded
def budget_summary() -> dict:
    """Calcule en SQL le budget total, les dépenses réservées/payées, les dépenses prévues et ce qu'il reste."""
    with trip_db.connect() as conn:
        summary = _summary(conn, _require_trip(conn))
        result = {"ok": True, **summary}  # by_category reste present meme vide : pas de passage par _result
        warnings = _budget_warnings(summary)
        if warnings:
            result["warnings"] = warnings
        return result



@_guarded
def place_save(name: str, decision: str, kind: str | None = None, reason: str | None = None) -> dict:
    """Note un lieu (activité, restaurant, hébergement...) comme retenu ou refusé ; la dernière décision l'emporte."""
    name = _text(name, "name")
    decision = _choice(decision, "decision", DECISIONS)
    kind = _choice(kind, "kind", PLACE_KINDS) if _given(kind) else None
    reason = _text(reason, "reason", MAX_REASON_LEN) if _given(reason) else None
    key = normalize_key(name)
    if not key:
        raise TripError("Le nom doit contenir au moins une lettre ou un chiffre.")

    with trip_db.connect() as conn:
        trip = _require_trip(conn)
        row = conn.execute("SELECT * FROM places WHERE trip_id = ? AND name_key = ?", (trip["id"], key)).fetchone()

        changes: dict = {}
        if row is None:
            conn.execute(
                "INSERT INTO places (trip_id, name, name_key, kind, decision, reason) VALUES (?, ?, ?, ?, ?, ?)",
                (trip["id"], name, key, kind or "other", decision, reason),
            )
            action = "created"
        else:
            for column, value in (("decision", decision), ("kind", kind), ("reason", reason)):
                if value is not None and row[column] != value:
                    changes[column] = {"from": row[column], "to": value}
            if changes:  # le nom d'origine est conserve, comme pour les depenses
                conn.execute(
                    "UPDATE places SET kind = ?, decision = ?, reason = ? WHERE id = ?",
                    (
                        changes.get("kind", {}).get("to", row["kind"]),
                        decision,
                        changes.get("reason", {}).get("to", row["reason"]),
                        row["id"],
                    ),
                )
            action = "updated" if changes else "unchanged"

        saved = conn.execute("SELECT * FROM places WHERE trip_id = ? AND name_key = ?", (trip["id"], key)).fetchone()
        return _result(ok=True, action=action, place=_public_place(saved), changes=changes)


def rejected_places() -> list[dict]:
    try:
        if not trip_db.db_path().exists():
            return []
        with trip_db.connect() as conn:
            trip = _active_trip(conn)
            if trip is None:
                return []
            rows = conn.execute(
                "SELECT * FROM places WHERE trip_id = ? AND decision = 'rejected' ORDER BY id", (trip["id"],)
            ).fetchall()
            return [_public_place(row) for row in rows]
    except (TripError, sqlite3.Error, OSError):
        return []

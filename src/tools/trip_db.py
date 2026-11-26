
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 3

CATEGORIES = ("transport", "lodging", "food", "activity", "other")
EXPENSE_STATUSES = ("planned", "booked", "paid")
PLACE_KINDS = ("activity", "restaurant", "lodging", "sight", "other")
DECISIONS = ("kept", "rejected")
COMMITTED_STATUSES = ("booked", "paid")

SECTION_TITLES = {
    "overview": "Aperçu",
    "transport": "Transport",
    "lodging": "Hébergement",
    "activities": "Activités",
    "practical": "Conseils pratiques",
}
DOCUMENT_SECTIONS = tuple(SECTION_TITLES)
DOCUMENT_MODES = ("replace", "append", "remove")

MAX_NAME_LEN = 120
MAX_REASON_LEN = 300
MAX_TRAVELERS = 50
MAX_AMOUNT = 10_000_000  # en unites de la devise (les montants sont stockes en centimes)
MAX_SECTION_LEN = 4000  # caracteres par section


class TripError(Exception):
    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


_DATE_GLOB = "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'"

_TRIP_SCHEMA = f"""
CREATE TABLE trips (
  id           INTEGER PRIMARY KEY,
  destination  TEXT    NOT NULL CHECK (length(trim(destination)) BETWEEN 1 AND {MAX_NAME_LEN}),
  start_date   TEXT    CHECK (start_date GLOB {_DATE_GLOB}),
  end_date     TEXT    CHECK (end_date GLOB {_DATE_GLOB}),
  budget_cents INTEGER CHECK (budget_cents > 0),
  currency     TEXT    NOT NULL CHECK (currency GLOB '[A-Z][A-Z][A-Z]'),
  travelers    INTEGER NOT NULL DEFAULT 1 CHECK (travelers BETWEEN 1 AND {MAX_TRAVELERS}),
  active       INTEGER NOT NULL DEFAULT 0 CHECK (active IN (0, 1)),
  updated_at   TEXT    NOT NULL,
  CHECK (start_date IS NULL OR end_date IS NULL OR end_date >= start_date)
);
CREATE UNIQUE INDEX one_active_trip ON trips (active) WHERE active = 1;

CREATE TABLE expenses (
  id           INTEGER PRIMARY KEY,
  trip_id      INTEGER NOT NULL REFERENCES trips (id) ON DELETE CASCADE,
  category     TEXT    NOT NULL CHECK (category IN ({_sql_list(CATEGORIES)})),
  label        TEXT    NOT NULL CHECK (length(trim(label)) BETWEEN 1 AND {MAX_NAME_LEN}),
  label_key    TEXT    NOT NULL CHECK (length(label_key) > 0),
  amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
  status       TEXT    NOT NULL DEFAULT 'planned' CHECK (status IN ({_sql_list(EXPENSE_STATUSES)})),
  UNIQUE (trip_id, category, label_key)
);

CREATE TABLE places (
  id       INTEGER PRIMARY KEY,
  trip_id  INTEGER NOT NULL REFERENCES trips (id) ON DELETE CASCADE,
  name     TEXT    NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND {MAX_NAME_LEN}),
  name_key TEXT    NOT NULL CHECK (length(name_key) > 0),
  kind     TEXT    NOT NULL DEFAULT 'other' CHECK (kind IN ({_sql_list(PLACE_KINDS)})),
  decision TEXT    NOT NULL CHECK (decision IN ({_sql_list(DECISIONS)})),
  reason   TEXT    CHECK (reason IS NULL OR length(reason) <= {MAX_REASON_LEN}),
  UNIQUE (trip_id, name_key)
);
"""

_SECTIONS_TABLE = f"""
CREATE TABLE document_sections (
  trip_id    INTEGER NOT NULL REFERENCES trips (id) ON DELETE CASCADE,
  section    TEXT    NOT NULL CHECK (section IN ({_sql_list(DOCUMENT_SECTIONS)})),
  content    TEXT    NOT NULL CHECK (length(trim(content)) > 0 AND length(content) <= {MAX_SECTION_LEN}),
  updated_at TEXT    NOT NULL,
  PRIMARY KEY (trip_id, section)
);
"""

_EDITS_TABLE = f"""
CREATE TABLE document_edits (
  id          INTEGER PRIMARY KEY,
  trip_id     INTEGER NOT NULL REFERENCES trips (id) ON DELETE CASCADE,
  section     TEXT    NOT NULL CHECK (section IN ({_sql_list(DOCUMENT_SECTIONS)})),
  mode        TEXT    NOT NULL CHECK (mode IN ({_sql_list(DOCUMENT_MODES)})),
  old_content TEXT,
  new_content TEXT    CHECK (new_content IS NULL OR length(trim(new_content)) > 0),
  created_at  TEXT    NOT NULL
);
"""

SCHEMA = f"{_TRIP_SCHEMA}\n{_SECTIONS_TABLE}\n{_EDITS_TABLE}\nPRAGMA user_version = {SCHEMA_VERSION};"
_MIGRATION_FROM_1 = f"{_SECTIONS_TABLE}\n{_EDITS_TABLE}\nPRAGMA user_version = {SCHEMA_VERSION};"
_MIGRATION_FROM_2 = f"""
ALTER TABLE document_edits RENAME TO document_edits_v2;
{_EDITS_TABLE}
INSERT INTO document_edits (id, trip_id, section, mode, old_content, new_content, created_at)
  SELECT id, trip_id, section, mode, old_content, new_content, created_at FROM document_edits_v2;
DROP TABLE document_edits_v2;
PRAGMA user_version = {SCHEMA_VERSION};
"""
_TRIP_TABLES = {"trips", "expenses", "places"}
_DOCUMENT_TABLES = {"document_sections", "document_edits"}


def normalize_key(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.sub(r"[\W_]+", " ", stripped.casefold()).split())


_override: Path | None = None


def set_db_path(path: Path | str | None) -> None:
    global _override
    _override = Path(path) if path is not None else None


def db_path() -> Path:
    if _override is not None:
        return _override
    try:
        from config import active_config  # serveur MCP : cwd=src/, donc src/ est dans sys.path
    except ImportError:
        from src.config import active_config
    path = active_config().trip_db
    if path is None:
        raise TripError(
            "La mémoire du voyage est désactivée.",
            "L'utilisateur doit renseigner 'trip_db' dans config/config.json.",
        )
    return path


def _ensure_schema(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == SCHEMA_VERSION:
        return
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if version == 0 and not tables:
        conn.executescript(f"BEGIN;\n{SCHEMA}\nCOMMIT;")
        return
    if version == 1 and _TRIP_TABLES <= tables:
        conn.executescript(f"BEGIN;\n{_MIGRATION_FROM_1}\nCOMMIT;")
        return
    if version == 2 and _TRIP_TABLES | _DOCUMENT_TABLES <= tables:
        conn.executescript(f"BEGIN;\n{_MIGRATION_FROM_2}\nCOMMIT;")
        return
    raise TripError(
        f"Base du voyage inutilisable : version de schéma {version}, {SCHEMA_VERSION} attendue.",
        "L'utilisateur doit supprimer ou déplacer le fichier de la base.",
    )


@contextmanager
def connect():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        _ensure_schema(conn)
        with conn:
            yield conn
    finally:
        conn.close()


def clear_all() -> int:
    if not db_path().exists():
        return 0
    with connect() as conn:
        return conn.execute("DELETE FROM trips").rowcount

# Garde-fou de la suite de tests : aucun test ne doit ouvrir la vraie base du voyage (data/trips.db), qui contient les
# donnees reelles de l'utilisateur. Toute tentative d'ouvrir un fichier SQLite du dossier data/ leve une AssertionError
# au lieu d'y lire ou d'y ecrire. Les tests de la memoire du voyage utilisent une base temporaire (trip_db.set_db_path).

import sqlite3
from pathlib import Path

_PROTECTED = Path(__file__).resolve().parent.parent / "data"
_real_connect = sqlite3.connect


def _guarded_connect(database, *args, **kwargs):
    try:
        path = Path(database).resolve()
    except (TypeError, ValueError, OSError):
        path = None
    if path is not None and (path == _PROTECTED or _PROTECTED in path.parents):
        raise AssertionError(f"Un test a tente d'ouvrir la vraie base du voyage ({path}). Utilise une base temporaire.")
    return _real_connect(database, *args, **kwargs)


sqlite3.connect = _guarded_connect

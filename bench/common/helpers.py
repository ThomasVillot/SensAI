"""Petites fonctions partagees par les deux benchs."""

from __future__ import annotations

import json
import platform
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

# Un seul fichier de resultats par bench. L'historique, c'est git.
RESULTS_FILE = "resultats.json"


def norm(s) -> str:
    """Minuscules, sans accents ni espaces autour : ' Lisbônne' == 'lisbonne'."""
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.lower().strip()


def machine_meta() -> dict:
    """Date et machine de la mesure : les chiffres ne valent que pour ce poste."""
    return {
        "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "machine": platform.node(),
        "os": platform.platform(),
        "python": platform.python_version(),
    }


def load_results(out_dir: Path) -> dict:
    return json.loads((out_dir / RESULTS_FILE).read_text(encoding="utf-8"))


def save_results(payload: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / RESULTS_FILE
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path

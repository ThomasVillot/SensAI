
from __future__ import annotations

import json
from pathlib import Path

from .utils import NAME_PATTERN


class ProfileError(Exception):
    pass


def available_profiles(profiles_dir: Path) -> list[str]:
    try:
        return sorted(path.stem for path in profiles_dir.glob("*.json"))
    except OSError:
        return []


def load_profile(profiles_dir: Path | None, name: str) -> dict:
    if profiles_dir is None:
        raise ProfileError("Aucun dossier de profils : 'profiles_dir' est absent de config/config.json.")
    if not NAME_PATTERN.match(name):
        raise ProfileError(
            f"Nom de profil invalide : '{name}' (lettres, chiffres, '.', '_' et '-' uniquement)."
        )

    path = profiles_dir / f"{name}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        found = available_profiles(profiles_dir)
        hint = f" Profils disponibles : {', '.join(found)}." if found else f" Aucun profil dans {profiles_dir}."
        raise ProfileError(f"Profil '{name}' introuvable ({path}).{hint}") from None
    except (OSError, ValueError) as exc:
        raise ProfileError(f"Profil illisible ({path}) : {exc}") from None

    if not isinstance(data, dict):
        raise ProfileError(f"{path} doit contenir un objet JSON.")
    if not data:
        raise ProfileError(f"Le profil {path} est vide.")
    return data


def render_profile(profile: dict) -> str:
    lines = ["", "", "Profil du voyageur (à respecter dans chaque réponse, sans qu'il ait à le rappeler) :"]
    for key, value in profile.items():
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value)
        elif isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False)
        lines.append(f"- {key} : {value}")
    return "\n".join(lines)

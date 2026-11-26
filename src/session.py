
from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Container
from pathlib import Path

SESSION_VERSION = 1
ROLES = {"system", "user", "assistant", "tool"}
MESSAGE_ANNOTATIONS = ("model", "profile")


class SessionError(Exception):
    pass


def latest_session_name(sessions_dir: Path) -> str | None:
    try:
        files = sorted(sessions_dir.glob("*.json"), key=lambda path: path.stat().st_mtime)
    except OSError:
        return None
    return files[-1].stem if files else None


def new_session_name(sessions_dir: Path, stamp: str | None = None, reserved: Container[str] = ()) -> str:
    base = stamp or time.strftime("%Y-%m-%d_%H-%M-%S")
    name, n = base, 2
    while (sessions_dir / f"{name}.json").exists() or name in reserved:
        name = f"{base}-{n}"
        n += 1
    return name


class Session:
    def __init__(self, system_prompt: str = "", path: Path | None = None) -> None:
        self._messages: list[dict] = []
        self._archive: list[dict] = []
        self.turns = 0
        self.path = path
        self.profile: str | None = None
        self._save_failed = False
        if system_prompt:
            self._messages.append({"role": "system", "content": system_prompt})

    @classmethod
    def load(cls, path: Path, system_prompt: str, max_turns: int | None = None) -> Session:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return cls(system_prompt, path)
        except (OSError, ValueError) as exc:
            raise SessionError(
                f"Session illisible ({path}) : {exc}. Supprime le fichier ou choisis un autre nom."
            ) from None

        session = cls("", path)
        session._messages = cls._conversation(data, path)
        session._keep_last_turns(max_turns)
        session.turns = sum(1 for m in session._messages if m["role"] == "user")
        if system_prompt:
            session.set_system_prompt(system_prompt)
        return session

    @staticmethod
    def _conversation(data, path: Path) -> list[dict]:
        if not isinstance(data, dict) or data.get("version") != SESSION_VERSION:
            raise SessionError(
                f"Session invalide ({path}) : format inconnu (version {SESSION_VERSION} attendue)."
            )
        messages = data.get("messages")
        if not isinstance(messages, list) or not all(
            isinstance(m, dict) and m.get("role") in ROLES for m in messages
        ):
            raise SessionError(f"Session invalide ({path}) : la liste 'messages' est corrompue.")
        conversation = [m for m in messages if m["role"] != "system"]
        if conversation and conversation[0]["role"] != "user":
            raise SessionError(f"Session invalide ({path}) : la conversation doit commencer par un message user.")
        return conversation

    def _keep_last_turns(self, n: int | None) -> None:
        starts = [i for i, m in enumerate(self._messages) if m["role"] == "user"]
        if n is None or len(starts) <= n:
            return
        cut = starts[-n]
        self._archive = self._messages[:cut]
        self._messages = self._messages[cut:]

    def restart(self, system_prompt: str, path: Path | None) -> None:
        self._messages = [{"role": "system", "content": system_prompt}] if system_prompt else []
        self._archive = []
        self.turns = 0
        self.path = path
        self._save_failed = False

    @property
    def archived_turns(self) -> int:
        return sum(1 for m in self._archive if m["role"] == "user")

    def save(self) -> None:
        if self.path is None or self._save_failed:
            return
        data = {"version": SESSION_VERSION, "messages": self.conversation}
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            self._save_failed = True
            print(f"[avertissement] sauvegarde de la session désactivée : {exc}", file=sys.stderr)

    def messages_with(self, user_text: str) -> list[dict]:
        return [*self._messages, {"role": "user", "content": user_text}]

    def add_turn(self, user_text: str, reply: str, model: str | None = None) -> None:
        self._messages.extend(self._annotated(
            [{"role": "user", "content": user_text}, {"role": "assistant", "content": reply}], model
        ))
        self.turns += 1
        self.save()

    @property
    def messages(self) -> list[dict]:
        return list(self._messages)

    @property
    def conversation(self) -> list[dict]:
        return [m for m in (*self._archive, *self._messages) if m.get("role") != "system"]

    def add_messages(self, messages: list[dict], model: str | None = None) -> None:
        self._messages.extend(self._annotated(messages, model))
        self.turns += 1
        self.save()

    def _annotated(self, messages: list[dict], model: str | None) -> list[dict]:
        notes = {"model": model, "profile": self.profile}
        notes = {key: value for key, value in notes.items() if value}
        return [{**message, **notes} for message in messages]

    def set_system_prompt(self, system_prompt: str) -> None:
        if self._messages and self._messages[0].get("role") == "system":
            self._messages[0] = {"role": "system", "content": system_prompt}
        else:
            self._messages.insert(0, {"role": "system", "content": system_prompt})

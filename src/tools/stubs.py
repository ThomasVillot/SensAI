
from __future__ import annotations

import sys
from types import SimpleNamespace

_warned: set[str] = set()


def make_stub(name: str, reason: str) -> SimpleNamespace:
    def tool(args: dict) -> dict:
        if name not in _warned:
            _warned.add(name)
            print(
                f"[avertissement] outil '{name}' : {reason}. Résultat factice (_stub) renvoyé au modèle.",
                file=sys.stderr,
            )
        return {
            "_stub": True,
            "tool": name,
            "message": f"L'outil '{name}' n'est pas encore implémenté : aucune donnée réelle.",
            "arguments": args,
        }

    return SimpleNamespace(tool=tool, STUB=True)

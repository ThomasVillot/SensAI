
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from .latency import combine_results
from .ollama_client import ChatResult

STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_INTERRUPTED = "interrupted"
STATUS_MAX_ITERATIONS = "max_iterations"
STATUS_BLOCKED = "blocked"

KIND_AGENT = "agent"
KIND_CHAT = "chat"

ERROR_PREVIEW_CHARS = 300


def tool_failure(result) -> tuple[bool, str | None]:
    if isinstance(result, dict):
        if result.get("error"):
            return True, str(result["error"])[:ERROR_PREVIEW_CHARS]
        return False, None
    if getattr(result, "isError", False) or getattr(result, "is_error", False):
        try:
            return True, str(result.content[0].text)[:ERROR_PREVIEW_CHARS]
        except (AttributeError, IndexError, TypeError):
            return True, "erreur renvoyee par l'outil"
    return False, None


class TurnRecorder:
    def __init__(self, kind: str, model: str, prompt: str, started_at: float | None = None) -> None:
        self.kind = kind
        self.model = model
        self.prompt = prompt
        self.tools: list[dict] = []
        self._started = time.perf_counter() if started_at is None else started_at
        self._pending: dict | None = None

    def tool_called(self, name: str, arguments) -> None:
        self._pending = {"name": name, "arguments": arguments, "t0": time.perf_counter()}

    def tool_returned(self, name: str, result) -> None:
        pending, self._pending = self._pending, None
        started = pending["t0"] if pending else time.perf_counter()
        failed, error = tool_failure(result)
        self.tools.append(
            {
                "name": name,
                "arguments": pending["arguments"] if pending else None,
                "ok": not failed,
                "error": error,
                "duration_s": round(time.perf_counter() - started, 3),
            }
        )

    def close_pending(self, error: str | None) -> None:
        pending, self._pending = self._pending, None
        if pending:
            self.tools.append(
                {
                    "name": pending["name"],
                    "arguments": pending["arguments"],
                    "ok": False,
                    "error": error or "aucun résultat reçu",
                    "duration_s": round(time.perf_counter() - pending["t0"], 3),
                }
            )

    def wall_s(self) -> float:
        return time.perf_counter() - self._started


class InteractionLog:
    def __init__(self, path: Path | None, session_id: str, prompt_version: str = "") -> None:
        self.path = path
        self.session_id = session_id
        self.prompt_version = prompt_version
        self._write_failed = False

    def build_entry(
        self,
        recorder: TurnRecorder,
        status: str,
        response: str,
        error: str | None,
        calls: list[ChatResult],
    ) -> dict:
        recorder.close_pending(error)
        combined = combine_results(calls) if calls else None
        return {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "session": self.session_id,
            "kind": recorder.kind,
            "model": recorder.model,
            "prompt_version": self.prompt_version,
            "prompt": recorder.prompt,
            "response": response,
            "status": status,
            "error": error,
            "latency_s": None if combined is None else round(combined.total_s, 3),
            "wall_s": round(recorder.wall_s(), 3),
            "ttft_s": None if combined is None or combined.ttft_s is None else round(combined.ttft_s, 3),
            "tokens": None if combined is None else combined.eval_count,
            "model_calls": len(calls),
            "tool_calls": recorder.tools,
        }

    def write(
        self,
        recorder: TurnRecorder,
        status: str,
        response: str = "",
        error: str | None = None,
        calls: list[ChatResult] | None = None,
    ) -> dict:
        entry = self.build_entry(recorder, status, response, error, calls or [])
        if self.path is None or self._write_failed:
            return entry
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as log:
                log.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except (OSError, TypeError, ValueError) as exc:
            self._write_failed = True
            print(f"[avertissement] journal des interactions désactivé : {exc}", file=sys.stderr)
        return entry

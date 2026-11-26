
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from .ollama_client import ChatResult


def combine_results(results: list[ChatResult]) -> ChatResult:
    def total(values: list) -> float | None:
        known = [v for v in values if v is not None]
        return sum(known) if known else None

    return ChatResult(
        content=results[-1].content,
        total_s=sum(r.total_s for r in results),
        ttft_s=next((r.ttft_s for r in results if r.ttft_s is not None), None),
        eval_count=total([r.eval_count for r in results]),
        load_s=total([r.load_s for r in results]),
    )


class LatencyTracker:
    def __init__(self, model: str, log_path: Path | None) -> None:
        self.model = model
        self.log_path = log_path
        self.samples: list[float] = []
        self._log_failed = False

    def record(self, result: ChatResult, calls: int | None = None) -> None:
        self.samples.append(result.total_s)
        if self.log_path is None or self._log_failed:
            return
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "model": self.model,
            "latency_s": round(result.total_s, 3),
            "ttft_s": None if result.ttft_s is None else round(result.ttft_s, 3),
            "eval_count": result.eval_count,
            "load_s": None if result.load_s is None else round(result.load_s, 3),
        }
        if calls is not None:
            entry["calls"] = calls
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as log:
                log.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            self._log_failed = True
            print(f"[avertissement] log de latence désactivé : {exc}", file=sys.stderr)

    def format_turn(self, result: ChatResult, calls: int | None = None) -> str:
        parts = [f"latence {result.total_s:.2f} s"]
        if result.ttft_s is not None:
            parts.append(f"1er token {result.ttft_s:.2f} s")
        if result.eval_count is not None:
            parts.append(f"{result.eval_count} tokens")
        if calls is not None:
            parts.append(f"{calls} appel{'s' if calls > 1 else ''}")
        return "[" + " | ".join(parts) + "]"

    def summary(self) -> str | None:
        if not self.samples:
            return None
        count = len(self.samples)
        text = f"Latence moyenne : {sum(self.samples) / count:.2f} s sur {count} tour(s)"
        if count > 1:
            warm = self.samples[1:]
            text += f" ({sum(warm) / len(warm):.2f} s hors 1er tour)"
        return text

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from .config import Config, OutputGuardrails

KIND_PROMPT_LEAK = "system_prompt_leak"
KIND_MALICIOUS = "malicious_content"

TAIL_CHARS = 2000

ASSUMED_REGEX_WORDS = 8


@dataclass(frozen=True)
class Violation:
    kind: str
    category: str | None
    message: str

    def event(self) -> dict:
        return {"type": "guardrail", "kind": self.kind, "category": self.category, "message": self.message}

    def reason(self) -> str:
        return f"guardrail:{self.kind}" + (f":{self.category}" if self.category else "")


def normalize(text: str) -> str:
    text = html.unescape(text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if unicodedata.category(char) not in ("Mn", "Cf"))
    for apostrophe in ("’", "‘", "ʼ", "`", "´"):
        text = text.replace(apostrophe, "'")
    return text.casefold()


def _words(normalized: str) -> list[str]:
    return re.findall(r"\w+", normalized)


@lru_cache(maxsize=16)
def _shingles(texts: tuple[str, ...], size: int) -> frozenset[tuple[str, ...]]:
    found: set[tuple[str, ...]] = set()
    for text in texts:
        words = _words(normalize(text))
        for start in range(len(words) - size + 1):
            found.add(tuple(words[start:start + size]))
    return frozenset(found)


def _compile_entry(entry: str) -> re.Pattern:
    if entry.startswith("re:"):
        return re.compile(entry[3:], re.IGNORECASE)
    words = normalize(entry).split()
    body = r"\s+".join(re.escape(word) for word in words)
    start = r"(?<!\w)" if re.match(r"\w", words[0]) else ""
    end = r"(?!\w)" if re.search(r"\w$", words[-1]) else ""
    return re.compile(start + body + end)


def _entry_words(entry: str) -> int:
    return ASSUMED_REGEX_WORDS if entry.startswith("re:") else len(entry.split())


class GuardrailBlocked(Exception):
    def __init__(self, violation: Violation) -> None:
        super().__init__(violation.message)
        self.violation = violation


def event_reason(event: dict) -> str:
    return f"guardrail:{event.get('kind')}" + (f":{event['category']}" if event.get("category") else "")


class OutputGuard:
    def __init__(self, settings: OutputGuardrails, protected_texts: tuple[str, ...] = ()) -> None:
        self._settings = settings
        self._shingle_size = settings.leak_shingle_words
        self._shingles: frozenset = frozenset()
        if settings.leak_enabled and protected_texts:
            self._shingles = _shingles(protected_texts, self._shingle_size)

        self._rules: list[tuple[str, list[re.Pattern]]] = []
        longest = 0
        if settings.malicious_enabled:
            for category, entries in settings.malicious_categories.items():
                self._rules.append((category, [_compile_entry(entry) for entry in entries]))
                longest = max([longest, *(_entry_words(entry) for entry in entries)])

        if settings.holdback_words is not None:
            self._holdback = settings.holdback_words
        else:
            longest = max(longest, self._shingle_size if self._shingles else 0)
            self._holdback = longest + 2 if longest else 0

        self.reset()

    def reset(self) -> None:
        self._text = ""
        self._released = 0

    def feed(self, token: str) -> tuple[str, Violation | None]:
        if not token:
            return "", None
        self._text += token
        tail = self._text[-TAIL_CHARS:]
        if len(self._text) > TAIL_CHARS:
            tail = re.sub(r"^\S*\s?", "", tail, count=1)
        violation = self._scan(tail, final=False)
        if violation:
            return "", violation
        return self._release(final=False), None

    def finish(self, full_text: str | None = None) -> tuple[str, Violation | None]:
        violation = self._scan(self._text, final=True)
        if violation is None and full_text and full_text != self._text:
            violation = self._scan(full_text, final=True)
        if violation:
            return "", violation
        return self._release(final=True), None

    def _release(self, final: bool) -> str:
        pending = self._text[self._released:]
        if final or self._holdback == 0:
            boundary = len(self._text)
        else:
            starts = [match.start() for match in re.finditer(r"\w+", pending)]
            if len(starts) <= self._holdback:
                return ""
            boundary = self._released + starts[-self._holdback]
        chunk = self._text[self._released:boundary]
        self._released = boundary
        return chunk

    def _scan(self, text: str, final: bool) -> Violation | None:
        normalized = normalize(text)
        if not final and re.search(r"\w$", normalized):
            normalized = re.sub(r"\w+$", "", normalized)

        if self._shingles:
            words = _words(normalized)
            size = self._shingle_size
            for start in range(len(words) - size + 1):
                if tuple(words[start:start + size]) in self._shingles:
                    return Violation(KIND_PROMPT_LEAK, None, self._settings.leak_message)

        for category, patterns in self._rules:
            if any(pattern.search(normalized) for pattern in patterns):
                return Violation(KIND_MALICIOUS, category, self._settings.malicious_message)
        return None

def build_output_guard(config: Config, messages: list[dict] | tuple = ()) -> OutputGuard | None:
    settings = config.output_guardrails
    if settings is None:
        return None
    texts = [config.tool_system_prompt, config.system_prompt]
    texts += [
        message["content"]
        for message in messages
        if isinstance(message, dict) and message.get("role") == "system" and isinstance(message.get("content"), str)
    ]
    protected = tuple(dict.fromkeys(text for text in texts if text and text.strip()))
    return OutputGuard(settings, protected)

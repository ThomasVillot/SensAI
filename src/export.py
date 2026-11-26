from __future__ import annotations

import json
import re

from markdown_it import MarkdownIt
from markdown_it.common.utils import escapeHtml

EXPORT_FORMAT = "sensai-conversation"
DOCUMENT_FORMAT = "sensai-itinerary"
EXPORT_VERSION = 1
FENCE = re.compile(r"[ \t]*(`{3,}|~{3,})")
URL = re.compile(r"https?://[^\s<>\"'\\]+")
MARKDOWN_PUNCTUATION = re.compile(r"([\\`*_\[\]<>!#|~])")
URL_TRAILING_PUNCTUATION = ".,;:!?)"


def _image_as_link(self, tokens, idx, options, env) -> str:
    token = tokens[idx]
    source = str(token.attrGet("src") or "")
    text = self.renderInlineAsText(token.children or [], options, env) or source
    return f'<a href="{escapeHtml(source)}">{escapeHtml(text)}</a>'


def _renderer(breaks: bool) -> MarkdownIt:
    renderer = MarkdownIt("commonmark", {"html": False, "breaks": breaks}).enable(["table", "strikethrough"])
    renderer.add_render_rule("image", _image_as_link)
    return renderer


MARKDOWN = _renderer(breaks=False)
MARKDOWN_WITH_BREAKS = _renderer(breaks=True)


def to_html(markdown: str, breaks: bool = False) -> str:
    return (MARKDOWN_WITH_BREAKS if breaks else MARKDOWN).render(markdown)


def to_markdown(messages: list[dict]) -> str:
    turns = _turns(messages)
    blocks = ["# Conversation SensAI", _summary(messages, len(turns))]
    for number, (question, replies) in enumerate(turns, 1):
        blocks += [f"## Échange {number}", "**Vous**", _quote(question), "**SensAI**", *_reply_blocks(replies)]
    return "\n\n".join(blocks) + "\n"


def to_json(messages: list[dict], conversation: str | None = None) -> str:
    data = {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "conversation": conversation,
        "turns": len(_turns(messages)),
        "messages": messages,
    }
    return json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n"


def document_to_json(document: dict) -> str:
    data = {"format": DOCUMENT_FORMAT, "version": EXPORT_VERSION, **document}
    return json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n"


def _turns(messages: list[dict]) -> list[tuple[str, list[dict]]]:
    turns: list[tuple[str, list[dict]]] = []
    for message in messages:
        role = message.get("role")
        if role == "user":
            turns.append((_text(message.get("content")), []))
        elif turns and role in ("assistant", "tool"):
            turns[-1][1].append(message)
    return turns


def _text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)


def _escape(text: str) -> str:
    return MARKDOWN_PUNCTUATION.sub(r"\\\1", text)


def _summary(messages: list[dict], turns: int) -> str:
    models = list(dict.fromkeys(
        message["model"]
        for message in messages
        if message.get("role") == "assistant" and isinstance(message.get("model"), str) and message["model"]
    ))
    parts = [f"{turns} échange{'s' if turns > 1 else ''}"]
    if models:
        parts.append(f"modèle{'s' if len(models) > 1 else ''} : {_escape(', '.join(models))}")
    return " · ".join(parts)


def _quote(text: str) -> str:
    lines = text.strip("\n").splitlines() or [""]
    return "\n".join(f"> {line}".rstrip() for line in lines)


def _reply_blocks(replies: list[dict]) -> list[str]:
    blocks: list[str] = []
    calls: list[str] = []
    sources: dict[str, str | None] = {}
    answered = False
    for message in replies:
        if message.get("role") == "tool":
            _collect_sources(message.get("content"), sources)
            continue
        text = _text(message.get("content")).strip("\n")
        if text.strip():
            if calls:
                blocks.append(_tool_list(calls))
                calls = []
            blocks.append(_closed_fences(text))
            answered = True
        calls += [_call_text(call) for call in message.get("tool_calls") or []]
    if calls:
        blocks.append(_tool_list(calls))
    if not answered:
        blocks.append("_Pas de réponse écrite._")
    if sources:
        blocks.append(
            "Pages consultées par les outils :\n\n" + "\n".join(_source_item(url, label) for url, label in sources.items())
        )
    return blocks


def _closed_fences(text: str) -> str:
    fence = None
    for line in text.splitlines():
        match = FENCE.match(line)
        if not match:
            continue
        marker, rest = match.group(1), line[match.end():]
        if fence is None:
            if not (marker[0] == "`" and "`" in rest):
                fence = marker
        elif marker[0] == fence[0] and len(marker) >= len(fence) and not rest.strip():
            fence = None
    return text if fence is None else f"{text}\n{fence}"


def _tool_list(calls: list[str]) -> str:
    return "Outils utilisés :\n\n" + "\n".join(f"- {call}" for call in calls)


def _call_text(call) -> str:
    function = call.get("function") if isinstance(call, dict) else None
    if not isinstance(function, dict):
        function = {}
    name = str(function.get("name") or "?")
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            pass
    if isinstance(arguments, dict):
        shown = ", ".join(
            f"{key}={json.dumps(value, ensure_ascii=False, default=str)}" for key, value in arguments.items()
        )
    elif arguments is None:
        shown = ""
    else:
        shown = json.dumps(arguments, ensure_ascii=False, default=str)
    return _inline_code(f"{name}({shown})")


def _inline_code(text: str) -> str:
    text = " ".join(text.splitlines())
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    delimiter = "`" * (longest + 1)
    padding = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{delimiter}{padding}{text}{padding}{delimiter}"


def _collect_sources(content, sources: dict[str, str | None]) -> None:
    text = _text(content)
    try:
        value = json.loads(text)
    except ValueError:
        value = None
    if isinstance(value, (dict, list)):
        _walk_sources(value, sources)
        return
    for url in URL.findall(text):
        _add_source(sources, url.rstrip(URL_TRAILING_PUNCTUATION), None)


def _walk_sources(value, sources: dict[str, str | None]) -> None:
    if isinstance(value, dict):
        label = next(
            (value[key] for key in ("title", "name") if isinstance(value.get(key), str) and value[key].strip()),
            None,
        )
        items = value.values()
    elif isinstance(value, list):
        label, items = None, value
    else:
        return
    for item in items:
        if isinstance(item, str) and URL.fullmatch(item):
            _add_source(sources, item, label)
        elif isinstance(item, str):
            _walk_sources(_json_in(item), sources)
        else:
            _walk_sources(item, sources)


def _json_in(text: str):
    if not text.lstrip().startswith(("{", "[")):
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _add_source(sources: dict[str, str | None], url: str, label: str | None) -> None:
    if sources.get(url) is None:
        sources[url] = label


def _source_item(url: str, label: str | None) -> str:
    if label:
        return f"- [{_escape(' '.join(label.split()))}](<{url}>)"
    return f"- <{url}>"

from __future__ import annotations

import json
import os
import re
import secrets
import socket
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from string import Template

from .export import to_html, to_markdown
from .tools.trip_document import render_markdown

SHARE_VERSION = 1
TOKEN_BYTES = 32
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
KIND_CONVERSATION = "conversation"
KIND_DOCUMENT = "document"

PAGE = Template("""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta name="referrer" content="no-referrer">
<title>$title</title>
<style>
:root { color-scheme: light dark; --bg: #f7f7f5; --card: #ffffff; --text: #1f2328; --muted: #656d76; --border: #d8dee4; --code: #f0f1f3; --accent: #0b6bcb; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #15171a; --card: #1e2125; --text: #e6e8eb; --muted: #9aa4ae; --border: #343a40; --code: #2a2e33; --accent: #6cb2ff; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font: 16px/1.6 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 780px; margin: 0 auto; padding: 32px 16px 48px; }
.notice { margin: 0 0 24px; padding: 10px 14px; border: 1px solid var(--border); border-radius: 8px; background: var(--card); color: var(--muted); font-size: 14px; }
article { padding: 8px 24px 24px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); overflow-wrap: anywhere; }
h1 { font-size: 26px; margin: 16px 0 4px; }
h2 { font-size: 19px; margin: 32px 0 8px; padding-top: 16px; border-top: 1px solid var(--border); }
blockquote { margin: 0; padding: 8px 14px; border-left: 4px solid var(--accent); background: var(--code); border-radius: 0 8px 8px 0; }
blockquote p { margin: 0; }
code { padding: 1px 5px; border-radius: 4px; background: var(--code); font: 14px/1.5 ui-monospace, "Cascadia Code", Consolas, monospace; }
pre { padding: 12px 14px; border-radius: 8px; background: var(--code); overflow-x: auto; }
pre code { padding: 0; background: none; }
table { border-collapse: collapse; display: block; overflow-x: auto; }
th, td { padding: 6px 10px; border: 1px solid var(--border); text-align: left; }
a { color: var(--accent); }
footer { margin-top: 24px; color: var(--muted); font-size: 13px; text-align: center; }
@media print {
  :root { color-scheme: light; --bg: #ffffff; --card: #ffffff; --text: #000000; --muted: #444444; --border: #bbbbbb; --code: #f2f2f2; --accent: #0645ad; }
  body { font-size: 12pt; }
  main { max-width: none; padding: 0; }
  article { padding: 0; border: none; border-radius: 0; }
  .notice, footer { display: none; }
  h1, h2, h3 { break-after: avoid; }
  pre, blockquote, table, li { break-inside: avoid; }
  pre { white-space: pre-wrap; overflow: visible; }
  table { display: table; }
}
</style>
</head>
<body>
<main>
$content
<footer>Page en lecture seule générée par SensAI.</footer>
</main>
</body>
</html>
""")


class ShareError(Exception):
    pass


def create_share(
    shares_dir: Path,
    conversation: str | None,
    messages: list[dict],
    ttl_days: int,
    now: datetime | None = None,
) -> dict:
    return _create(shares_dir, conversation, ttl_days, now, KIND_CONVERSATION, {"messages": messages})


def create_document_share(
    shares_dir: Path,
    conversation: str | None,
    document: dict,
    ttl_days: int,
    now: datetime | None = None,
) -> dict:
    return _create(shares_dir, conversation, ttl_days, now, KIND_DOCUMENT, {"document": document})


def load_share(shares_dir: Path, token: str, now: datetime | None = None) -> dict | None:
    if not TOKEN_PATTERN.fullmatch(token):
        return None
    path = shares_dir / f"{token}.json"
    share = _read(path)
    if share is None:
        return None
    if _expired(share, now or datetime.now(timezone.utc)):
        _delete_quietly(path)
        return None
    return share


def revoke_share(shares_dir: Path, token: str) -> bool:
    if not TOKEN_PATTERN.fullmatch(token):
        return False
    path = shares_dir / f"{token}.json"
    if not path.is_file():
        return False
    _delete(path)
    return True


def revoke_conversation(shares_dir: Path, conversation: str | None) -> int:
    revoked = 0
    for path in _share_files(shares_dir):
        share = _read(path)
        if share is not None and share["conversation"] == conversation:
            _delete(path)
            revoked += 1
    return revoked


def purge_expired(shares_dir: Path, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    purged = 0
    for path in _share_files(shares_dir):
        share = _read(path)
        if share is not None and _expired(share, now) and _delete_quietly(path):
            purged += 1
    return purged


def token_from(link: str) -> str:
    return link.strip().rstrip("/").rsplit("/", 1)[-1]


def public_host(host: str) -> str:
    if host != "0.0.0.0":
        return host
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("8.8.8.8", 80))
        except OSError:
            return "127.0.0.1"
        return probe.getsockname()[0]


def share_url(host: str, port: int, token: str) -> str:
    return f"http://{public_host(host)}:{port}/share/{token}"


def format_expiry(share: dict) -> str:
    return datetime.fromisoformat(share["expires_at"]).astimezone().strftime("%d/%m/%Y à %H:%M")


def share_markdown(share: dict) -> str:
    if share["kind"] == KIND_DOCUMENT:
        return render_markdown(share["document"])
    return to_markdown(share["messages"])


def render_page(share: dict) -> str:
    subject = "Document d'itinéraire partagé" if share["kind"] == KIND_DOCUMENT else "Conversation partagée"
    notice = (
        f'<p class="notice">{subject} en lecture seule. '
        f"Ce lien expire le {escape(format_expiry(share))} et peut être révoqué à tout moment.</p>"
    )
    article = f"<article>\n{to_html(share_markdown(share), breaks=share['kind'] == KIND_DOCUMENT)}</article>"
    return PAGE.substitute(title=f"{subject} · SensAI", content=f"{notice}\n{article}")


def not_found_page() -> str:
    article = (
        "<article>\n<h1>Lien indisponible</h1>\n"
        "<p>Ce lien de partage n'existe pas, a expiré ou a été révoqué.</p>\n</article>"
    )
    return PAGE.substitute(title="Lien indisponible · SensAI", content=article)


def _create(
    shares_dir: Path,
    conversation: str | None,
    ttl_days: int,
    now: datetime | None,
    kind: str,
    content: dict,
) -> dict:
    now = now or datetime.now(timezone.utc)
    purge_expired(shares_dir, now)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    share = {
        "version": SHARE_VERSION,
        "kind": kind,
        "conversation": conversation,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=ttl_days)).isoformat(),
        **content,
    }
    path = shares_dir / f"{token}.json"
    tmp = path.with_name(path.name + ".tmp")
    try:
        shares_dir.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(share, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        raise ShareError(f"lien impossible à créer ({exc})") from None
    return {"token": token, **share}


def _share_files(shares_dir: Path) -> list[Path]:
    try:
        return [path for path in shares_dir.glob("*.json") if TOKEN_PATTERN.fullmatch(path.stem)]
    except OSError:
        return []


def _read(path: Path) -> dict | None:
    try:
        share = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(share, dict) or share.get("version") != SHARE_VERSION:
        return None
    if not isinstance(share.get("expires_at"), str):
        return None
    share.setdefault("kind", KIND_CONVERSATION)
    share.setdefault("conversation", None)
    if share["kind"] == KIND_CONVERSATION and isinstance(share.get("messages"), list):
        return share
    if share["kind"] == KIND_DOCUMENT and _is_document(share.get("document")):
        return share
    return None


def _is_document(document) -> bool:
    return (
        isinstance(document, dict)
        and isinstance(document.get("trip"), dict)
        and isinstance(document.get("sections"), list)
        and all(isinstance(section, dict) for section in document["sections"])
    )


def _expired(share: dict, now: datetime) -> bool:
    try:
        return datetime.fromisoformat(share["expires_at"]) <= now
    except (ValueError, TypeError):
        return True


def _delete(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise ShareError(f"lien impossible à supprimer ({exc})") from None


def _delete_quietly(path: Path) -> bool:
    try:
        _delete(path)
    except ShareError:
        return False
    return True


from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

from .interaction_log import STATUS_BLOCKED, STATUS_ERROR, STATUS_INTERRUPTED, STATUS_MAX_ITERATIONS, STATUS_OK

BAR_WIDTH = 20
FAILED_STATUSES = (STATUS_ERROR, STATUS_MAX_ITERATIONS)


def load_entries(path: Path) -> tuple[list[dict], int]:
    entries: list[dict] = []
    skipped = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return [], 0
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        if isinstance(entry, dict):
            entries.append(entry)
        else:
            skipped += 1
    return entries, skipped


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def _latencies(entries: list[dict]) -> list[float]:
    return [e["latency_s"] for e in entries if isinstance(e.get("latency_s"), (int, float))]


def _is_failed(entry: dict) -> bool:
    return entry.get("status") in FAILED_STATUSES


def _num(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _pct(part: int, total: int) -> str:
    return f"{_num(100 * part / total, 1)} %" if total else "-"


def summarize(entries: list[dict]) -> dict:
    total = len(entries)
    statuses = {s: sum(1 for e in entries if e.get("status") == s) for s in (STATUS_OK, STATUS_ERROR, STATUS_INTERRUPTED, STATUS_MAX_ITERATIONS, STATUS_BLOCKED)}
    kinds: dict[str, int] = {}
    for e in entries:
        kinds[e.get("kind", "?")] = kinds.get(e.get("kind", "?"), 0) + 1

    latencies = _latencies(entries)
    latency = None
    if latencies:
        latency = {
            "mean": statistics.fmean(latencies),
            "median": statistics.median(latencies),
            "p95": percentile(latencies, 95),
            "worst": max(latencies),
        }

    tokens = [e["tokens"] for e in entries if isinstance(e.get("tokens"), (int, float))]
    calls = [e["model_calls"] for e in entries if isinstance(e.get("model_calls"), (int, float))]

    tools: dict[str, dict] = {}
    for e in entries:
        for t in e.get("tool_calls") or []:
            if not isinstance(t, dict):
                continue
            stats = tools.setdefault(str(t.get("name", "?")), {"calls": 0, "failures": 0, "durations": []})
            stats["calls"] += 1
            if t.get("ok") is False:
                stats["failures"] += 1
            if isinstance(t.get("duration_s"), (int, float)):
                stats["durations"].append(t["duration_s"])

    return {
        "total": total,
        "statuses": statuses,
        "kinds": kinds,
        "error_rate": sum(statuses[s] for s in FAILED_STATUSES) / total,
        "sessions": len({e.get("session") for e in entries if e.get("session")}),
        "first": min((e["ts"] for e in entries if isinstance(e.get("ts"), str)), default=None),
        "last": max((e["ts"] for e in entries if isinstance(e.get("ts"), str)), default=None),
        "latency": latency,
        "tokens_total": sum(tokens) if tokens else None,
        "tokens_mean": statistics.fmean(tokens) if tokens else None,
        "calls_mean": statistics.fmean(calls) if calls else None,
        "tools": tools,
    }


def group_rows(entries: list[dict], key) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for e in entries:
        groups.setdefault(str(key(e) or "?"), []).append(e)
    rows = []
    for name in sorted(groups):
        members = groups[name]
        latencies = _latencies(members)
        rows.append(
            {
                "key": name,
                "turns": len(members),
                "latency_mean": statistics.fmean(latencies) if latencies else None,
                "failed": sum(1 for e in members if _is_failed(e)),
            }
        )
    return rows


def _day(entry: dict) -> str:
    ts = entry.get("ts")
    return ts[:10] if isinstance(ts, str) and len(ts) >= 10 else "?"


def _bar(value: float | None, biggest: float) -> str:
    if value is None or biggest <= 0:
        return ""
    return "█" * max(1, round(BAR_WIDTH * value / biggest))


def _table_group(title: str, rows: list[dict], label: str) -> list[str]:
    biggest = max((r["latency_mean"] for r in rows if r["latency_mean"] is not None), default=0)
    lines = ["", title, f"  {label:<14}{'tours':>6}  {'latence moy.':>12}  {'erreurs':>8}"]
    for r in rows:
        latency = f"{_num(r['latency_mean'])} s" if r["latency_mean"] is not None else "-"
        lines.append(
            f"  {r['key']:<14}{r['turns']:>6}  {latency:>12}  {_pct(r['failed'], r['turns']):>8}  {_bar(r['latency_mean'], biggest)}"
        )
    return lines


def format_dashboard(entries: list[dict], source: str = "", skipped: int = 0) -> str:
    if not entries:
        return (
            f"Aucune interaction journalisée pour l'instant{f' ({source})' if source else ''}.\n"
            "Discute avec SensAI puis relance cette vue."
        )

    s = summarize(entries)
    kinds = ", ".join(f"{name} {count}" for name, count in sorted(s["kinds"].items()))
    lines = [
        f"SensAI | métriques des interactions{f' ({source})' if source else ''}",
        f"Période : {s['first']} -> {s['last']} | {s['total']} interaction(s) ({kinds}) | {s['sessions']} session(s)",
        "",
        "Fiabilité",
        f"  réussies {s['statuses'][STATUS_OK]} ({_pct(s['statuses'][STATUS_OK], s['total'])})"
        f" | erreurs {s['statuses'][STATUS_ERROR]}"
        f" | plafond d'itérations {s['statuses'][STATUS_MAX_ITERATIONS]}"
        f" | interrompues {s['statuses'][STATUS_INTERRUPTED]}"
        f" | bloquées {s['statuses'][STATUS_BLOCKED]}",
        f"  taux d'erreur : {_num(100 * s['error_rate'], 1)} %",
    ]

    if s["latency"]:
        lat = s["latency"]
        lines += [
            "",
            "Latence d'un tour (somme des appels modèle)",
            f"  moyenne {_num(lat['mean'])} s | médiane {_num(lat['median'])} s"
            f" | p95 {_num(lat['p95'])} s | pire cas {_num(lat['worst'])} s",
        ]
    extras = []
    if s["tokens_total"] is not None:
        total_tokens = f"{s['tokens_total']:,}".replace(",", " ")
        extras.append(f"tokens : {total_tokens} au total, {_num(s['tokens_mean'], 0)} par tour")
    if s["calls_mean"] is not None:
        extras.append(f"appels modèle : {_num(s['calls_mean'], 1)} par tour")
    if extras:
        lines.append("  " + " | ".join(extras))

    if s["tools"]:
        lines += ["", "Outils", f"  {'nom':<20}{'appels':>7}{'échecs':>8}{'durée moy.':>12}"]
        for name, stats in sorted(s["tools"].items(), key=lambda item: -item[1]["calls"]):
            duration = f"{_num(statistics.fmean(stats['durations']))} s" if stats["durations"] else "-"
            lines.append(f"  {name:<20}{stats['calls']:>7}{stats['failures']:>8}{duration:>12}")
    else:
        lines += ["", "Outils", "  aucun appel d'outil journalisé"]

    lines += _table_group("Évolution par jour", group_rows(entries, _day), "jour")
    lines += _table_group("Par version de prompt", group_rows(entries, lambda e: e.get("prompt_version")), "version")
    lines += _table_group("Par modèle", group_rows(entries, lambda e: e.get("model")), "modèle")

    if skipped:
        lines += ["", f"({skipped} ligne(s) illisible(s) ignorée(s) dans le journal)"]
    return "\n".join(lines)


def dashboard(path: Path | None) -> str:
    if path is None:
        return "Journal des interactions désactivé (clé 'interaction_log' absente de config/config.json)."
    entries, skipped = load_entries(path)
    try:
        source = str(path.relative_to(Path.cwd()))
    except ValueError:
        source = str(path)
    return format_dashboard(entries, source, skipped)

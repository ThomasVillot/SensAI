"""Rapport graphique du bench outils : une planche PNG resumant la comparaison."""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

CATS = [
    ("outils", "Tool-calling"),
    ("vitesse", "Vitesse"),
    ("json", "JSON"),
    ("raisonnement", "Raisonnement"),
    ("consignes", "Consignes"),
    ("francais", "Francais"),
]
PALETTE = ["#4c78a8", "#f58518", "#54a24b", "#e45756", "#72b7b2",
           "#b279a2", "#9d755d", "#bab0ac"]


def _radar(ax, names, models):
    labels = [lab for _, lab in CATS]
    angles = np.linspace(0, 2 * np.pi, len(labels), endpoint=False).tolist()
    angles += angles[:1]
    for i, m in enumerate(names):
        vals = [models[m]["scores"].get(k) or 0 for k, _ in CATS]
        vals += vals[:1]
        color = PALETTE[i % len(PALETTE)]
        ax.plot(angles, vals, linewidth=2, label=m, color=color)
        ax.fill(angles, vals, alpha=0.12, color=color)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylim(0, 100)
    ax.set_yticks([25, 50, 75, 100])
    ax.set_yticklabels(["25", "50", "75", "100"], fontsize=7, color="gray")
    ax.set_title("Profil par categorie", fontsize=12, pad=26)
    ax.legend(loc="upper left", bbox_to_anchor=(1.18, 1.05), fontsize=8, frameon=False)


def _global_bars(ax, names, models):
    ranked = sorted(names, key=lambda m: models[m]["global"])
    vals = [models[m]["global"] for m in ranked]
    colors = [PALETTE[names.index(m) % len(PALETTE)] for m in ranked]
    bars = ax.barh(ranked, vals, color=colors)
    ax.bar_label(bars, fmt="%.1f", padding=3, fontsize=9)
    ax.set_xlim(0, 112)
    ax.set_xlabel("score global / 100")
    ax.set_title("Classement general", fontsize=12)
    ax.grid(axis="x", alpha=0.25)


def _heatmap(ax, names, models):
    grid = np.array([[models[m]["scores"].get(k) or 0 for k, _ in CATS] for m in names])
    im = ax.imshow(grid, cmap="RdYlGn", vmin=0, vmax=100, aspect="auto")
    ax.set_xticks(range(len(CATS)))
    ax.set_xticklabels([lab for _, lab in CATS], rotation=25, ha="right", fontsize=8)
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=9)
    for y in range(grid.shape[0]):
        for x in range(grid.shape[1]):
            ax.text(x, y, str(int(grid[y, x])), ha="center", va="center",
                    fontsize=9, color="black")
    ax.set_title("Detail des scores", fontsize=12)
    return im


def _react(ax, names, models):
    """Le graphe qui compte : le cout d'une question avec la boucle A1."""
    ranked = sorted(names, key=lambda m: models[m]["perf"].get("latence_s_median") or 0)
    lat = [models[m]["perf"].get("latence_s_median") or 0 for m in ranked]
    x = np.arange(len(ranked))
    width = 0.26
    for offset, mult, label in [(-width, 1, "1 appel"), (0, 3, "3 appels"), (width, 5, "5 appels")]:
        vals = [l * mult for l in lat]
        bars = ax.bar(x + offset, vals, width, label=label)
        ax.bar_label(bars, fmt="%.0fs", fontsize=7, padding=2)
    ax.axhline(20, color="#e45756", linestyle="--", linewidth=1.2)
    ax.text(-0.45, 20, " seuil demo ~20s ", color="#e45756", fontsize=8,
            ha="left", va="bottom",
            bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="none", alpha=0.85))
    ax.set_xticks(x)
    ax.set_xticklabels(ranked, fontsize=8, rotation=18, ha="right")
    ax.set_ylabel("secondes")
    ax.set_title("Cout d'une question avec la boucle A1", fontsize=12)
    ax.legend(fontsize=8)
    ax.margins(y=0.22)


def _speed(ax, names, models):
    ranked = sorted(names, key=lambda m: models[m]["perf"].get("gen_tok_s_median") or 0)
    vals = [models[m]["perf"].get("gen_tok_s_median") or 0 for m in ranked]
    colors = [PALETTE[names.index(m) % len(PALETTE)] for m in ranked]
    bars = ax.barh(ranked, vals, color=colors)
    ax.bar_label(bars, fmt="%.1f", padding=3, fontsize=9)
    ax.set_xlabel("tokens / seconde (generation)")
    ax.set_title("Debit de generation", fontsize=12)
    ax.grid(axis="x", alpha=0.25)
    ax.margins(x=0.15)


def _stability(ax, names, models):
    vals = [models[m]["scores"].get("stabilite") for m in names]
    if all(v is None for v in vals):
        ax.axis("off")
        ax.text(0.5, 0.5, "Stabilite indisponible\n(relance avec --repeat 3)",
                ha="center", va="center", fontsize=10, color="gray")
        return
    colors = [PALETTE[i % len(PALETTE)] for i in range(len(names))]
    bars = ax.bar(names, [v or 0 for v in vals], color=colors)
    ax.bar_label(bars, fmt="%.0f", fontsize=9)
    ax.set_ylim(0, 112)
    ax.set_ylabel("stabilite / 100")
    ax.set_title("Reproductibilite entre essais", fontsize=12)
    ax.tick_params(axis="x", labelsize=8)
    plt.setp(ax.get_xticklabels(), rotation=18, ha="right")


def build(payload: dict, out_dir: Path) -> Path:
    models = payload["models"]
    names = list(models)

    fig = plt.figure(figsize=(16, 11 + 0.35 * max(0, len(names) - 4)))
    gs = fig.add_gridspec(3, 2, hspace=0.45, wspace=0.28)

    _global_bars(fig.add_subplot(gs[0, 0]), names, models)
    _radar(fig.add_subplot(gs[0, 1], polar=True), names, models)
    im = _heatmap(fig.add_subplot(gs[1, 0]), names, models)
    _react(fig.add_subplot(gs[1, 1]), names, models)
    _speed(fig.add_subplot(gs[2, 0]), names, models)
    _stability(fig.add_subplot(gs[2, 1]), names, models)

    fig.colorbar(im, ax=fig.axes[2], fraction=0.035, pad=0.03)

    meta = payload["meta"]
    fig.suptitle("SensAI — comparaison de modeles Ollama", fontsize=16, y=0.985)
    fig.text(0.5, 0.955, meta["date"] + "  |  " + meta["machine"]
             + "  |  " + str(meta["repetitions"]) + " essais par cas",
             ha="center", fontsize=9, color="gray")

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "rapport.png"
    fig.savefig(path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    return path

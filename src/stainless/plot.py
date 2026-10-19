"""The results figure: % stained per group, one dot per section, mean ± SD; controls apart,
in grey. matplotlib only (no Qt), loaded when first needed.
"""

from __future__ import annotations

import numpy as np


def figure(summary: list[dict], region: str, stain: str, colour=(0, 230, 118), fig=None,
           dark: bool = True):
    from matplotlib.figure import Figure
    fig = fig or Figure(figsize=(5, 4), dpi=100)
    fig.clear()
    fg, bg = ("#e0e0e0", "#232323") if dark else ("#000000", "#ffffff")
    fig.set_facecolor(bg)
    ax = fig.add_subplot()
    ax.set_facecolor(bg)
    c = tuple(v / 255 for v in colour)
    rng = np.random.default_rng(0)
    for i, g in enumerate(summary):
        ctrl = g["group"] in ("negative control", "positive control")
        col = (0.55, 0.55, 0.55) if ctrl else c
        v = np.asarray(g["values"], float)
        ax.scatter(i + rng.uniform(-0.12, 0.12, len(v)), v, s=60, color=col, zorder=3,
                   edgecolor=bg, linewidth=0.6)
        ax.hlines(g["mean"], i - 0.25, i + 0.25, color=fg, linewidth=2, zorder=4)
        if g["sd"] is not None:
            ax.errorbar(i, g["mean"], yerr=g["sd"], color=fg, capsize=6, linewidth=1, zorder=4)
    ax.set_xticks(range(len(summary)))
    ax.set_xticklabels([f"{g['group']}\nn={g['n']}" for g in summary], color=fg)
    ax.set_xlim(-0.6, max(len(summary), 1) - 0.4)
    ax.set_ylim(bottom=0)
    ax.set_ylabel(f"{stain}⁺ area, % of tissue", color=fg)
    ax.set_title(region, color=fg)
    ax.tick_params(colors=fg)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(fg)
    fig.tight_layout()
    return fig


if __name__ == "__main__":
    f = figure([{"group": "MI", "n": 2, "mean": 5.0, "sd": 1.0, "values": [4.0, 6.0]},
                {"group": "negative control", "n": 1, "mean": 0.2, "sd": None,
                 "values": [0.2]}], "infarct", "DAB")
    assert len(f.axes) == 1 and len(f.axes[0].get_xticklabels()) == 2
    print("plot self-check OK")

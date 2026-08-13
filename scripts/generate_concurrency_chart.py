"""Generates the Block 2C concurrency comparison chart from the real recorded
results of scripts/concurrency_test.py and scripts/naive_baseline.py (numbers
below are copied verbatim from those actual runs against the live CockroachDB
cluster and a local throwaway PostgreSQL instance - not simulated).

Usage: python -m scripts.generate_concurrency_chart
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

OUTPUT_PATH = Path(__file__).resolve().parents[1] / "docs" / "concurrency-comparison.png"

# from the real naive_baseline.py run (docs/REVIEW_LOG.md, Block 2C)
WRITER_COUNTS = [50, 200]
POSTGRES_LOST = [41, 163]
COCKROACHDB_LOST = [0, 0]

# palette: dataviz skill reference palette, categorical slots 1 (blue) and 2 (orange)
COLOR_POSTGRES = "#2a78d6"
COLOR_COCKROACHDB = "#eb6834"
COLOR_TEXT_PRIMARY = "#0b0b0b"
COLOR_TEXT_MUTED = "#898781"
COLOR_GRIDLINE = "#e1e0d9"
COLOR_SURFACE = "#fcfcfb"


def main():
    fig, ax = plt.subplots(figsize=(7, 5), dpi=150)
    fig.patch.set_facecolor(COLOR_SURFACE)
    ax.set_facecolor(COLOR_SURFACE)

    x = np.arange(len(WRITER_COUNTS))
    bar_width = 0.32

    bars_pg = ax.bar(x - bar_width / 2, POSTGRES_LOST, bar_width, label="PostgreSQL (naive, READ COMMITTED)", color=COLOR_POSTGRES)
    bars_cdb = ax.bar(x + bar_width / 2, COCKROACHDB_LOST, bar_width, label="CockroachDB (identical naive code, SERIALIZABLE)", color=COLOR_COCKROACHDB)

    for bars in (bars_pg, bars_cdb):
        for bar in bars:
            height = bar.get_height()
            ax.annotate(
                str(int(height)),
                xy=(bar.get_x() + bar.get_width() / 2, height),
                xytext=(0, 4),
                textcoords="offset points",
                ha="center", va="bottom",
                fontsize=11, color=COLOR_TEXT_PRIMARY,
            )

    ax.set_xticks(x)
    ax.set_xticklabels([f"{n} concurrent writers" for n in WRITER_COUNTS], color=COLOR_TEXT_PRIMARY, fontsize=11)
    ax.set_ylabel("Lost updates (out of committed transactions)", color=COLOR_TEXT_PRIMARY, fontsize=11)
    ax.set_title(
        "Concurrent read-then-write: identical naive code,\nPostgreSQL vs. CockroachDB",
        color=COLOR_TEXT_PRIMARY, fontsize=13, pad=14,
    )

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(COLOR_GRIDLINE)
    ax.spines["bottom"].set_color(COLOR_GRIDLINE)
    ax.tick_params(colors=COLOR_TEXT_MUTED)
    ax.yaxis.grid(True, color=COLOR_GRIDLINE, linewidth=1)
    ax.set_axisbelow(True)

    legend = ax.legend(frameon=False, loc="upper left", fontsize=9.5)
    for text in legend.get_texts():
        text.set_color(COLOR_TEXT_PRIMARY)

    fig.text(
        0.5, -0.02,
        "Same read→sleep→write transaction, no retry logic, run against both databases.\n"
        "PostgreSQL's default isolation silently loses updates; CockroachDB's default either succeeds or fails loudly (0 lost).",
        ha="center", fontsize=8.5, color=COLOR_TEXT_MUTED,
    )

    fig.tight_layout()
    fig.savefig(OUTPUT_PATH, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"saved chart to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()

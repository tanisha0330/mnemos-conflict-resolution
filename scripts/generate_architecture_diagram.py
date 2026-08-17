"""Generates docs/architecture.jpg - a raster architecture diagram matching
docs/architecture.md's mermaid source, updated to reflect what's actually
true now (real AWS deploy cycle completed, ground-truth verification added),
not the earlier "IaC-only, not deployed" state. No external renderer
(graphviz/mermaid-cli) is available in this environment, so this draws the
diagram directly with matplotlib, with dedicated non-crossing lanes for
every cross-column connector.

Usage: python scripts/generate_architecture_diagram.py
"""

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
from matplotlib.lines import Line2D

OUT_PATH = Path(__file__).parent.parent / "docs" / "architecture.jpg"

COLOR_AGENT = "#DCE9FF"
COLOR_PIPELINE = "#FFF3CF"
COLOR_AWS = "#FFE3D1"
COLOR_DB = "#D9F2E3"
COLOR_NEW = "#FFD6E0"
BORDER = "#33415C"
BYPASS = "#7a8ba3"
DBLINE = "#4c8a68"


def box(ax, x, y, w, h, text, color, fontsize=9.5, weight="normal", lw=1.4):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.06",
        linewidth=lw, edgecolor=BORDER, facecolor=color, zorder=3,
    ))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
             fontsize=fontsize, weight=weight, zorder=4)
    return (x, y, w, h)


def straight(ax, p1, p2, label=None, color=BORDER, dashed=False, lw=1.3, label_pos=None, fs=8):
    ax.add_patch(FancyArrowPatch(
        p1, p2, arrowstyle="-|>", mutation_scale=13, linewidth=lw,
        color=color, zorder=2, linestyle="dashed" if dashed else "solid",
    ))
    if label:
        lx, ly = label_pos if label_pos else ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
        ax.text(lx, ly, label, ha="center", va="center", fontsize=fs, color="#1a1a1a", zorder=5,
                bbox=dict(boxstyle="round,pad=0.15", facecolor="white", edgecolor="none", alpha=0.92))


def elbow3(ax, pts, label=None, color=BORDER, dashed=False, lw=1.3, label_pos=None, fs=8):
    """Draws straight segments through pts (a list of >=2 points), arrowhead only on the last segment."""
    for i in range(len(pts) - 1):
        is_last = i == len(pts) - 2
        ax.add_patch(FancyArrowPatch(
            pts[i], pts[i + 1], arrowstyle="-|>" if is_last else "-", mutation_scale=13,
            linewidth=lw, color=color, zorder=2, linestyle="dashed" if dashed else "solid",
        ))
    if label:
        lx, ly = label_pos if label_pos else pts[len(pts) // 2]
        ax.text(lx, ly, label, ha="center", va="center", fontsize=fs, color="#1a1a1a", zorder=5,
                bbox=dict(boxstyle="round,pad=0.15", facecolor="white", edgecolor="none", alpha=0.92))


def edge(b, side):
    x, y, w, h = b
    return {"top": (x + w / 2, y + h), "bottom": (x + w / 2, y),
            "left": (x, y + h / 2), "right": (x + w, y + h / 2)}[side]


def main():
    fig, ax = plt.subplots(figsize=(18, 12.5))
    ax.set_xlim(0, 18)
    ax.set_ylim(0, 12.7)
    ax.axis("off")

    ax.text(9, 12.42, "mnemos — CockroachDB × AWS architecture", ha="center", fontsize=18, weight="bold")
    ax.text(9, 12.08,
            "Deployed and verified for real 2026-08-18: deploy → real Lambda invoke → destroy → redeploy  (docs/REVIEW_LOG.md)",
            ha="center", fontsize=9.5, color="#444444", style="italic")

    # ---- Writer agents (top row) ----
    pa = box(ax, 0.6, 10.55, 3.2, 0.85, "payment-agent\n(mock Stripe API)", COLOR_AGENT)
    sa = box(ax, 4.3, 10.55, 3.2, 0.85, "support-agent\n(mock Zendesk)", COLOR_AGENT)

    ax.text(0.6, 10.15,
            "Solid = synchronous request path.   Dashed = branch only when the previous stage can't decide.   Grey = bypass / cross-service call.",
            fontsize=8.4, color="#444444", va="top")

    # ---- Pipeline column (left) ----
    PX, PW = 0.6, 5.6
    ing = box(ax, PX, 8.75, PW, 0.75, "Ingestion: claim extraction → embedding → subject_key", COLOR_PIPELINE)
    s1 = box(ax, PX, 7.5, PW, 0.75, "Stage 1: conflict detection\ncosine similarity vs. canonical (<=>)", COLOR_PIPELINE)
    verify = box(ax, PX, 6.2, PW, 0.85, "NEW: Ground-truth verification\nchecks payment_ledger before heuristics", COLOR_NEW)
    s2 = box(ax, PX, 4.95, PW, 0.75, "Stage 2: deterministic rules\nauthority tier → recency → confidence floor", COLOR_PIPELINE)
    arb = box(ax, PX, 3.7, PW, 0.75, "LLM arbiter (Bedrock)\nonly when rules can't decide", COLOR_PIPELINE)
    commit = box(ax, PX, 2.45, PW, 0.75, "Transactional commit\nSERIALIZABLE + 40001 retry (never wraps the LLM call)", COLOR_PIPELINE)
    api = box(ax, PX, 1.2, PW, 0.75, "Retrieval API: search / get_all / history / as_of", COLOR_PIPELINE)

    straight(ax, edge(pa, "bottom"), (2.2, 9.5))
    straight(ax, edge(sa, "bottom"), (4.5, 9.5))
    straight(ax, edge(ing, "bottom"), edge(s1, "top"))
    straight(ax, edge(s1, "bottom"), edge(verify, "top"), label="real conflict", label_pos=(4.6, 7.15))
    straight(ax, edge(verify, "bottom"), edge(s2, "top"), dashed=True, label="not decided", label_pos=(4.55, 5.85))
    straight(ax, edge(s2, "bottom"), edge(arb, "top"), dashed=True, label="needs_llm", label_pos=(4.35, 4.6))
    straight(ax, edge(arb, "bottom"), edge(commit, "top"))
    straight(ax, edge(commit, "bottom"), edge(api, "top"))

    # ---- Reader agent, next to the API it actually reads (no long crossing arrow) ----
    fa = box(ax, 7.0, 1.2, 3.1, 0.75, "fulfillment-agent\n(reads memory)", COLOR_AGENT)
    straight(ax, edge(api, "right"), edge(fa, "left"), label="search()\n/ as_of()", fs=8, label_pos=(6.75, 1.575))

    # ---- Bypass lane: verification/rules "decided" outcomes skip straight to commit ----
    BX1, BX2 = 6.45, 6.85
    elbow3(ax, [edge(verify, "right"), (BX1, edge(verify, "right")[1]), (BX1, edge(commit, "right")[1] + 0.12), edge(commit, "right")],
           color=BYPASS, label="decided", label_pos=(BX1 + 0.55, 6.6), fs=8)
    elbow3(ax, [edge(s2, "right"), (BX2, edge(s2, "right")[1]), (BX2, edge(commit, "right")[1] - 0.12), edge(commit, "right")],
           color=BYPASS, label="rule\ndecided", label_pos=(BX2 + 0.5, 4.6), fs=8)

    # ---- CockroachDB band (bottom) ----
    box(ax, 0.6, 0.15, 12.4, 0.6,
        "CockroachDB:  sources · subjects · beliefs (VECTOR(1024), vector_cosine_ops) · resolutions · payment_ledger",
        COLOR_DB, fontsize=10, weight="bold")
    for b in (s1, verify, commit, api):
        bx = edge(b, "bottom")
        straight(ax, bx, (bx[0], 0.75), color=DBLINE, lw=1.3)

    # ---- AWS column (right) ----
    ax.text(15.1, 9.85, "AWS", ha="center", fontsize=13, weight="bold")
    bedrock = box(ax, 12.6, 8.95, 5.0, 0.75, "Amazon Bedrock\nTitan Embed V2 + Nova Lite / Claude Haiku 4.5", COLOR_AWS)
    lam = box(ax, 12.6, 7.85, 5.0, 0.75, "AWS Lambda: resolution worker\nruns the same pipeline for the 'candidate' backlog", COLOR_AWS)
    eventbridge = box(ax, 12.6, 6.95, 2.35, 0.65, "EventBridge\nrate(1 min)", COLOR_AWS, fontsize=8.5)
    secrets = box(ax, 15.25, 6.95, 2.35, 0.65, "Secrets Manager\nDATABASE_URL", COLOR_AWS, fontsize=8.5)
    s3 = box(ax, 12.6, 6.05, 2.35, 0.65, "S3\nsource artifacts", COLOR_AWS, fontsize=8.5)
    fargate = box(ax, 15.25, 6.05, 2.35, 0.65, "Fargate\ndemo agent tasks", COLOR_AWS, fontsize=8.5)

    # Pipeline <-> Bedrock: one clean elbowed lane at x=9.6, well clear of the Lambda lane at x=10.7
    elbow3(ax, [edge(ing, "right"), (9.6, edge(ing, "right")[1]), (9.6, edge(bedrock, "left")[1]), edge(bedrock, "left")],
           color=BYPASS, label="claim extraction + embeddings", label_pos=(9.6, 9.15), fs=7.8)
    # Routed below commit/api (a clear horizontal gap at y=2.2) and offset from the
    # bypass lane's x=6.45/6.85 lines, so this never crosses either.
    elbow3(ax, [edge(arb, "right"), (6.35, edge(arb, "right")[1]), (6.35, 2.3), (9.6, 2.3),
                (9.6, edge(bedrock, "left")[1] - 0.18), edge(bedrock, "left")],
           color=BYPASS, dashed=True, label="Converse API, forced tool-use", label_pos=(8.0, 2.45), fs=7.8)

    # Lambda <-> pipeline: a separate lane at x=10.7, clear of the Bedrock lane above
    elbow3(ax, [edge(lam, "left"), (10.7, edge(lam, "left")[1]), (10.7, edge(s1, "right")[1]), edge(s1, "right")],
           color=BYPASS, label="polls, then resolve_pending_candidate()\n(same 6 pipeline stages, left)",
           label_pos=(10.9, 7.15), fs=7.8)

    straight(ax, edge(eventbridge, "top"), (edge(eventbridge, "top")[0], 7.85), color=BYPASS)
    straight(ax, edge(secrets, "top"), (edge(secrets, "top")[0], 7.85), color=BYPASS)

    ax.text(15.1, 5.75,
            "Ground-truth verification (pink) is new: checks a real payment_ledger\n"
            "table before authority/recency/confidence heuristics - when it decides,\n"
            "that overrides what the authority-tier rule would otherwise pick,\n"
            "not just tiebreaks it.",
            fontsize=8.3, color="#444444", ha="center", va="top", style="italic")

    legend_elems = [
        Line2D([0], [0], marker="s", linestyle="", color=COLOR_AGENT, markersize=14, markeredgecolor=BORDER, label="Agent"),
        Line2D([0], [0], marker="s", linestyle="", color=COLOR_PIPELINE, markersize=14, markeredgecolor=BORDER, label="mnemos pipeline (src/)"),
        Line2D([0], [0], marker="s", linestyle="", color=COLOR_NEW, markersize=14, markeredgecolor=BORDER, label="New: ground-truth verification"),
        Line2D([0], [0], marker="s", linestyle="", color=COLOR_AWS, markersize=14, markeredgecolor=BORDER, label="AWS"),
        Line2D([0], [0], marker="s", linestyle="", color=COLOR_DB, markersize=14, markeredgecolor=BORDER, label="CockroachDB"),
    ]
    ax.legend(handles=legend_elems, loc="upper left", bbox_to_anchor=(0.70, 0.335),
              fontsize=8.5, frameon=True, ncol=1)

    fig.tight_layout()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PATH, format="jpeg", dpi=200, bbox_inches="tight")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()

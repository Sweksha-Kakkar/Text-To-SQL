"""Render the README charts (light + dark versions) into docs/images/.

Run from the project folder after an evaluation:
    pip install matplotlib
    python -m docs.make_charts
"""
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.executor import run_query  # noqa: E402

OUT = ROOT / "docs" / "images"
MODE_LABELS = {"schema_only": "Schema only (no RAG)", "rag": "Schema + RAG",
               "rag_tables": "Retrieved tables + RAG"}
MODE_ORDER = ["schema_only", "rag", "rag_tables"]
DIFFICULTIES = ["easy", "medium", "hard"]

# Validated categorical palette (slots 1-3), one step set per theme.
THEMES = {
    "light": dict(surface="#fcfcfb", text="#0b0b0b", muted="#52514e", grid="#e4e3df",
                  series=["#2a78d6", "#eb6834", "#1baf7a"]),
    "dark": dict(surface="#1a1a19", text="#f0efec", muted="#c3c2b7", grid="#3a3936",
                 series=["#3987e5", "#d95926", "#199e70"]),
}


def style(ax, t, title, subtitle):
    fig = ax.figure
    fig.patch.set_facecolor(t["surface"])
    ax.set_facecolor(t["surface"])
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(t["grid"])
    ax.tick_params(colors=t["muted"], length=0, labelsize=10)
    ax.grid(axis="y", color=t["grid"], linewidth=0.8)
    ax.set_axisbelow(True)
    fig.text(0.02, 0.95, title, color=t["text"], fontsize=14, fontweight="bold", va="top")
    fig.text(0.02, 0.885, subtitle, color=t["muted"], fontsize=10, va="top")


def accuracy_by_mode(df, t, path):
    acc = df.groupby("mode")["correct"].mean().mul(100).reindex(MODE_ORDER).dropna()
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=150)
    style(ax, t, "RAG lifts execution accuracy",
          f"{df['id'].nunique()} held-out questions · answered by {df['model_used'].mode()[0]} · thinking={df['thinking'].iloc[0]}")
    bars = ax.bar([MODE_LABELS[m] for m in acc.index], acc.values, width=0.55,
                  color=[t["series"][MODE_ORDER.index(m)] for m in acc.index],
                  edgecolor=t["surface"], linewidth=2)
    for b, v in zip(bars, acc.values):
        ax.text(b.get_x() + b.get_width() / 2, v + 2, f"{v:.0f}%", ha="center",
                color=t["text"], fontsize=12, fontweight="bold")
    ax.set_ylim(0, 110)
    ax.set_yticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
    fig.subplots_adjust(top=0.78, bottom=0.12, left=0.08, right=0.98)
    fig.savefig(path, facecolor=t["surface"])
    plt.close(fig)


def accuracy_by_difficulty(df, t, path):
    pivot = (df.groupby(["difficulty", "mode"])["correct"].mean().mul(100)
               .unstack().reindex(index=DIFFICULTIES, columns=MODE_ORDER))
    counts = df[df["mode"] == MODE_ORDER[0]].groupby("difficulty").size().reindex(DIFFICULTIES)
    fig, ax = plt.subplots(figsize=(8, 4.4), dpi=150)
    style(ax, t, "The gap grows with question difficulty",
          "Accuracy per difficulty level · business rules matter most on hard questions")
    width = 0.26
    for i, mode in enumerate(MODE_ORDER):
        if mode not in pivot:
            continue
        xs = [j + (i - 1) * width for j in range(len(DIFFICULTIES))]
        bars = ax.bar(xs, pivot[mode].values, width=width, color=t["series"][i],
                      edgecolor=t["surface"], linewidth=2, label=MODE_LABELS[mode])
        for b, v in zip(bars, pivot[mode].values):
            ax.text(b.get_x() + b.get_width() / 2, v + 2, f"{v:.0f}", ha="center",
                    color=t["muted"], fontsize=9)
    ax.set_xticks(range(len(DIFFICULTIES)),
                  [f"{d} ({counts[d]} q)" for d in DIFFICULTIES])
    ax.set_ylim(0, 112)
    ax.set_yticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
    leg = ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=3, frameon=False,
                    fontsize=9.5, labelcolor=t["text"])
    for h in leg.legend_handles:
        h.set_edgecolor(t["surface"])
    fig.subplots_adjust(top=0.78, bottom=0.2, left=0.08, right=0.98)
    fig.savefig(path, facecolor=t["surface"])
    plt.close(fig)


def monthly_orders(t, path):
    df = run_query("SELECT date_trunc('month', order_date)::date AS month, COUNT(*) AS orders "
                   "FROM orders WHERE order_date < date_trunc('month', CURRENT_DATE) "
                   "GROUP BY 1 ORDER BY 1").df
    df["month"] = pd.to_datetime(df["month"])
    fig, ax = plt.subplots(figsize=(8, 3.8), dpi=150)
    style(ax, t, "Orders per month in the demo data",
          "Generated with growth + seasonality (Nov–Dec peaks), Jan 2024 – Oct 2026")
    ax.plot(df["month"], df["orders"], color=t["series"][0], linewidth=2)
    ax.fill_between(df["month"], df["orders"], color=t["series"][0], alpha=0.12, linewidth=0)
    peak = df.loc[df["orders"].idxmax()]
    ax.plot([peak["month"]], [peak["orders"]], "o", markersize=7, color=t["series"][0],
            markeredgecolor=t["surface"], markeredgewidth=2)
    ax.annotate(f"{peak['orders']:,} orders\n{peak['month']:%b %Y}", (peak["month"], peak["orders"]),
                textcoords="offset points", xytext=(-70, -6), color=t["text"], fontsize=9)
    ax.set_ylim(0, df["orders"].max() * 1.2)
    fig.autofmt_xdate(rotation=0, ha="center")
    fig.subplots_adjust(top=0.78, bottom=0.12, left=0.08, right=0.98)
    fig.savefig(path, facecolor=t["surface"])
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    results = json.loads((ROOT / "eval" / "results" / "latest.json").read_text(encoding="utf-8"))
    df = pd.DataFrame(results["rows"])
    for name, t in THEMES.items():
        accuracy_by_mode(df, t, OUT / f"accuracy_by_mode_{name}.png")
        accuracy_by_difficulty(df, t, OUT / f"accuracy_by_difficulty_{name}.png")
        monthly_orders(t, OUT / f"monthly_orders_{name}.png")
    print(f"wrote charts to {OUT}")


if __name__ == "__main__":
    main()

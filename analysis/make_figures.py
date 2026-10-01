#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_figures.py —— 生成项目核心图表（商业杂志风格，微调版）

设计原则（参考 FT / Economist）：
  - 低饱和协调配色，暖橙仅用于强调 Agent / 关键数字
  - 去掉多余边框，浅色横向网格，充足留白
  - 标题 + 副标题(直接给结论) + 来源脚注 三层信息结构
  - 关键结论用文字/引导线标在图上

产出：
  analysis/figures/fig1_tradeoff.png
  analysis/figures/fig2_baseline_vs_agent.png

用法：
    pip install matplotlib --break-system-packages
    python analysis/make_figures.py
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from matplotlib.patches import Ellipse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "analysis" / "figures"

# ---------------------------------------------------------------------------
# 视觉主题
# ---------------------------------------------------------------------------

INK = "#1a1a2e"
MUTED = "#6b7280"
GRID = "#e5e7eb"
ACCENT = "#e07a3f"
NEUTRAL_BAR = "#a8b0bd"
ACCENT_BAR = "#2c6e6a"
PANEL = "#f7f7f5"

PROMPT_C = {
    "original": "#c05559",
    "neutral": "#9aa3b2",
    "allow_refusal": "#5b8ac9",
    "cite_source": "#3f9b91",
}

for cand in ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"]:
    if any(cand in f.name for f in fm.fontManager.ttflist):
        plt.rcParams["font.family"] = cand
        break

plt.rcParams.update({
    "font.size": 11,
    "text.color": INK,
    "axes.edgecolor": MUTED,
    "axes.labelcolor": INK,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "axes.linewidth": 0.8,
})


def _clean_axes(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(length=0)
    ax.set_axisbelow(True)


# ---------------------------------------------------------------------------
# 图 1：权衡曲线
# ---------------------------------------------------------------------------

def fig1_tradeoff():
    B = {
        "original":      (54, 46),
        "neutral":       (46, 46),
        "allow_refusal": (54, 46),
        "cite_source":   (62, 38),
    }
    agent = (100, 0)
    # 微调：allow_refusal 标签更大偏移，避免和椭圆/其他点挤在一起
    jitter = {"original": (-1.2, 1.0), "allow_refusal": (2.0, -2.4)}

    fig, ax = plt.subplots(figsize=(8.4, 6.2))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    _clean_axes(ax)
    ax.grid(True, axis="both", color=GRID, linewidth=0.8)

    # 微调：椭圆缩小一点，右缘不再顶到 cite_source
    ax.add_patch(Ellipse((53, 43), width=23, height=16,
                         facecolor=PANEL, edgecolor=GRID, linewidth=1,
                         zorder=0))
    ax.text(53, 53, "what prompt engineering can reach",
            ha="center", fontsize=9.5, color=MUTED, style="italic")

    for name, (x, y) in B.items():
        dx, dy = jitter.get(name, (0, 0))
        ax.scatter(x + dx, y + dy, s=170, c=PROMPT_C[name],
                   edgecolors="white", linewidths=1.6, zorder=3)
        ax.annotate(name, (x + dx, y + dy), textcoords="offset points",
                    xytext=(8, 7), fontsize=10, color=INK)

    ax.scatter(*agent, s=430, c=ACCENT, marker="*",
               edgecolors="white", linewidths=1.6, zorder=4)
    ax.annotate("ReAct Agent\n(GLM-4-Flash)", agent,
                textcoords="offset points", xytext=(-12, 16),
                fontsize=11, color=ACCENT, fontweight="bold", ha="right")

    ax.annotate("", xy=(96, 2), xytext=(64, 37),
                arrowprops=dict(arrowstyle="-|>", color=ACCENT,
                                lw=1.6, alpha=0.55,
                                connectionstyle="arc3,rad=-0.25"))
    ax.text(80, 24, "the real lever:\nlocate + verify",
            fontsize=10, color=ACCENT, style="italic", ha="center")

    # 微调：x 上限收到 105，Agent 星不再贴边
    ax.set_xlim(40, 105)
    ax.set_ylim(-7, 58)
    ax.set_xlabel("Correctly refused when it should  (%)",
                  fontsize=11, color=MUTED)
    ax.set_ylabel("Fabricated when it should refuse  (%)",
                  fontsize=11, color=MUTED)

    fig.text(0.06, 0.955, "The refuse–hallucinate trade-off",
             fontsize=16, fontweight="bold", color=INK)
    fig.text(0.06, 0.915,
             "Prompt variants stay in the high-hallucination zone; the locate-and-verify"
             " agent breaks out.",
             fontsize=10.5, color=MUTED)
    fig.text(0.06, 0.02,
             "ESG report QA · 13 adversarial no-answer questions · deepseek-r1:1.5b vs GLM-4-Flash",
             fontsize=8.5, color=MUTED)

    fig.subplots_adjust(top=0.86, bottom=0.12, left=0.10, right=0.96)
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "fig1_tradeoff.png"
    fig.savefig(p, dpi=200, facecolor="white")
    print(f"saved {p.relative_to(ROOT)}")


# ---------------------------------------------------------------------------
# 图 2：baseline vs agent
# ---------------------------------------------------------------------------

def fig2_baseline_vs_agent():
    metrics = ["Accuracy", "Hallucination", "Factual error"]
    hint = ["higher is better", "lower is better", "lower is better"]
    base = [50, 46, 27]
    agent = [62, 0, 15]

    fig, ax = plt.subplots(figsize=(8.4, 5.8))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    _clean_axes(ax)
    ax.grid(True, axis="y", color=GRID, linewidth=0.8)

    x = range(len(metrics))
    w = 0.34
    xb = [i - w/2 - 0.01 for i in x]
    xa = [i + w/2 + 0.01 for i in x]

    ax.bar(xb, base, w, color=NEUTRAL_BAR, edgecolor="white", linewidth=1,
           label="Baseline · 1.5B, direct answer", zorder=3)
    ax.bar(xa, agent, w, color=ACCENT_BAR, edgecolor="white", linewidth=1,
           label="ReAct Agent · GLM-4-Flash, locate+verify", zorder=3)

    for xi, v in zip(xb, base):
        ax.text(xi, v + 1.2, f"{v}%", ha="center", fontsize=11,
                color=MUTED, fontweight="bold")
    for xi, v in zip(xa, agent):
        col = ACCENT if (v == 0 or v == 62) else INK
        ax.text(xi, v + 1.2, f"{v}%", ha="center", fontsize=11,
                color=col, fontweight="bold")

    # 微调：改善幅度标注，−38 下移不悬空
    deltas = ["+12", "−46", "−12"]
    dy_offset = [7, -5, 7]
    for i, (dtxt, oy) in enumerate(zip(deltas, dy_offset)):
        ax.annotate(dtxt, (i, max(base[i], agent[i]) + oy),
                    ha="center", fontsize=10.5, color=ACCENT,
                    fontweight="bold")

    # 微调：幻觉归零引导虚线（38% → 0）
    ax.annotate("", xy=(1 + w/2 + 0.01, 1.5),
                xytext=(1 - w/2 - 0.01, 37),
                arrowprops=dict(arrowstyle="-|>", color=ACCENT, lw=1.4,
                                ls=(0, (3, 2)), alpha=0.7,
                                connectionstyle="arc3,rad=-0.2"))

    ax.set_xticks(list(x))
    ax.set_xticklabels([f"{m}\n({h})" for m, h in zip(metrics, hint)],
                       fontsize=10.5, color=INK)
    ax.set_ylim(0, 74)
    ax.set_yticks([0, 20, 40, 60])
    ax.set_yticklabels(["0", "20", "40", "60"])
    # 微调：去掉孤零零的 "%"
    ax.set_ylabel("", fontsize=11, color=MUTED)
    ax.legend(loc="upper right", fontsize=9.5, frameon=False)

    fig.text(0.06, 0.955, "Same strategy, stronger model",
             fontsize=16, fontweight="bold", color=INK)
    fig.text(0.06, 0.915,
             "The locate-and-verify agent hurts a 1.5B model but, on GLM-4-Flash,"
             " drives hallucination to zero.",
             fontsize=10.5, color=MUTED)
    fig.text(0.06, 0.02,
             "ESG report QA · 26 answerable + 13 no-answer questions · temperature=0",
             fontsize=8.5, color=MUTED)

    fig.subplots_adjust(top=0.86, bottom=0.14, left=0.09, right=0.96)
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "fig2_baseline_vs_agent.png"
    fig.savefig(p, dpi=200, facecolor="white")
    print(f"saved {p.relative_to(ROOT)}")


if __name__ == "__main__":
    fig1_tradeoff()
    fig2_baseline_vs_agent()
    print("\n完成，图在 analysis/figures/")
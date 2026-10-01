#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export_web_data.py —— 把实验结果导出为交互网页用的单个 JSON

汇总 baseline(四组prompt) 与 Agent 的逐题结果 + 判分，
生成 analysis/web/data.js（可直接被 dashboard.html 读取，无需服务器）。

用法：
    python analysis/export_web_data.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "analysis" / "runs"
BENCH = ROOT / "eval" / "benchmark.json"
WEB = ROOT / "analysis" / "web"

sys.path.insert(0, str(ROOT / "eval"))
from scorer import judge, strip_think     # noqa: E402


def load_run(name):
    p = RUNS / f"run_{name}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def main():
    bench = json.loads(BENCH.read_text(encoding="utf-8"))
    qmap = {q["id"]: q for q in bench["questions"]}

    variants = ["original", "neutral", "allow_refusal", "cite_source", "agent"]
    runs = {v: load_run(v) for v in variants}

    # 汇总指标
    summary = {}
    for v, run in runs.items():
        if not run:
            continue
        A = B = 0
        acc = ferr = wref = hal = cref = vague = 0
        for r in run["results"]:
            q = qmap.get(r["id"])
            if not q:
                continue
            verdict = judge(r.get("answer"), q)["verdict"]
            if q["group"] == "A":
                A += 1
                if verdict == "correct": acc += 1
                elif verdict == "factual_error": ferr += 1
                elif verdict == "wrong_refusal": wref += 1
            else:
                B += 1
                if verdict == "hallucination": hal += 1
                elif verdict == "correct_refusal": cref += 1
                elif verdict == "vague_pass": vague += 1
        pct = lambda n, d: round(100 * n / d) if d else 0
        summary[v] = {
            "accuracy": pct(acc, A),
            "factual_error": pct(ferr, A),
            "wrong_refusal": pct(wref, A),
            "hallucination": pct(hal, B),
            "correct_refusal": pct(cref, B),
            "vague_pass": pct(vague, B),
        }

    # 逐题：把每道题在各 variant 下的回答与判定收集起来
    questions = []
    for q in bench["questions"]:
        entry = {
            "id": q["id"], "group": q["group"], "company": q["company"],
            "question": q["question"], "gold": q.get("gold_answer"),
            "gold_page": q.get("source_page"),
            "locality": q.get("answer_locality"),
            "answers": {},
        }
        for v, run in runs.items():
            if not run:
                continue
            for r in run["results"]:
                if r["id"] == q["id"]:
                    ans = strip_think(r.get("answer") or "")
                    j = judge(r.get("answer"), q)
                    entry["answers"][v] = {
                        "text": ans[:400],
                        "verdict": j["verdict"],
                        "pages": r.get("retrieved_pages"),
                    }
                    break
        questions.append(entry)

    data = {"summary": summary, "questions": questions,
            "variants": [v for v in variants if runs.get(v)]}

    WEB.mkdir(parents=True, exist_ok=True)
    # 写成 data.js（挂到 window，避免 file:// 下 fetch 的跨域限制）
    (WEB / "data.js").write_text(
        "window.ESG_DATA = " + json.dumps(data, ensure_ascii=False) + ";",
        encoding="utf-8")
    print(f"导出完成：{(WEB/'data.js').relative_to(ROOT)}")
    print(f"  {len(questions)} 题，{len(data['variants'])} 个 variant")


if __name__ == "__main__":
    main()
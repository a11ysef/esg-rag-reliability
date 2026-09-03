#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scorer.py —— 评测判分脚本（修订版 v2）

相比 v1 修复三个判分 bug（均由首次 10 题试跑暴露）：

  BUG1  年份误判为幻觉：模型拒答句中出现问题自带的年份（如 "in 2023"），
        v1 会把 2023 当作编造数值，导致 B 组正确拒答被误判为 hallucination。
        修复：数值抽取默认排除裸四位年份（1900-2099，且不带单位/百分号）。

  BUG2  <think> 污染：deepseek-r1 的推理块若未在生成端剥离，
        其中的中间数字会干扰判分。修复：判分前兜底剥离，且只取 </think> 之后的最终答案。

  BUG3  拒答优先级：v1 对 B 组先查数值再查拒答，导致"报告未提供...(2023)"被误判。
        修复：无论 A/B 组，先判定是否拒答，再判数值。

判定逻辑（对应 benchmark.json 的 _scoring_rules）：
  A 组：correct / factual_error / wrong_refusal
  B 组：hallucination / correct_refusal / vague_pass

半自动：低置信条目标注 needs_review，提示人工复核。

用法：
    python eval/scorer.py analysis/runs/run_original.json --detail
    python eval/scorer.py --all
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCHMARK = ROOT / "eval" / "benchmark.json"
RUNS_DIR = ROOT / "analysis" / "runs"

_REFUSAL_MARKERS = [
    "does not provide", "does not contain", "does not include",
    "not provided", "not available", "not mentioned", "not specified",
    "no information", "no explicit information", "cannot find", "could not find",
    "unable to", "not found in", "not in the context", "not directly",
    "isn't provided", "doesn't provide", "no data", "not disclosed",
    "not stated", "does not specify", "there is no", "not enough information",
]

_VAGUE_MARKERS = [
    "committed to", "is dedicated", "highly values", "places importance",
    "continues to", "strives to", "aims to", "focuses on",
    "recognizes the importance", "takes seriously", "is working",
]

# <think>...</think> 推理块
_THINK_BLOCK = re.compile(r"<think>.*?</think>", flags=re.DOTALL | re.IGNORECASE)
_THINK_OPEN = re.compile(r"<think>", flags=re.IGNORECASE)
_THINK_CLOSE = re.compile(r"</think>", flags=re.IGNORECASE)


def strip_think(text: str) -> str:
    """
    兜底剥离推理块。三种情况都处理：
      1) 完整 <think>...</think>  -> 整块删除
      2) 只有 </think>（开标签丢失）-> 取其后的内容
      3) 只有 <think>（未闭合）    -> 删除该标签及其后（视为纯推理，无最终答案）
    """
    if _THINK_CLOSE.search(text):
        # 有闭合标签：最终答案在最后一个 </think> 之后
        text = _THINK_CLOSE.split(text)[-1]
    text = _THINK_BLOCK.sub("", text)          # 清理任何残留的完整块
    text = _THINK_OPEN.sub("", text)           # 清理孤立的开标签
    return text.strip()


# ---------------------------------------------------------------------------
# 数值抽取
# ---------------------------------------------------------------------------

_NUM_PATTERN = re.compile(
    r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*"
    r"(million|billion|thousand|k|m|b|%|percent|tco2e|gallons?|tons?)?",
    flags=re.IGNORECASE,
)

_MULTIPLIER = {
    "thousand": 1e3, "k": 1e3,
    "million": 1e6, "m": 1e6,
    "billion": 1e9, "b": 1e9,
}


def extract_numbers(text: str, drop_years: bool = True) -> list[float]:
    """
    抽取数值，统一成绝对数量级。
    drop_years=True 时，排除裸四位年份（1900-2099 且无单位/百分号），
    避免把问题里的 "2023" 当成模型给出的答案数值。
    """
    out = []
    for m in _NUM_PATTERN.finditer(text):
        raw, suffix = m.group(1), (m.group(2) or "").lower()
        try:
            val = float(raw.replace(",", ""))
        except ValueError:
            continue

        # 排除裸年份
        if drop_years and not suffix and "," not in raw and "." not in raw:
            if 1900 <= val <= 2099:
                continue

        if suffix in _MULTIPLIER:
            val *= _MULTIPLIER[suffix]
        out.append(val)
    return out


def gold_to_float(gold) -> float | None:
    try:
        return float(str(gold).replace(",", "").replace("%", "").strip())
    except (ValueError, AttributeError):
        return None


def matches_gold(answer: str, q: dict) -> tuple[bool, str]:
    ans_lower = answer.lower()

    for alt in q.get("accept_also", []):
        if alt.lower() in ans_lower:
            return True, f"命中等价表述『{alt}』"

    gold_val = gold_to_float(q["gold_answer"])
    if gold_val is None:
        return False, "gold 无法解析为数值"

    nums = extract_numbers(answer)
    if not nums:
        return False, "答案中未抽取到数值"

    tol = q.get("tolerance") or 0
    for n in nums:
        if gold_val == 0:
            if n == 0:
                return True, "精确命中 0"
        elif abs(n - gold_val) / abs(gold_val) <= tol:
            return True, f"数值 {n:g} 在容差内命中 {gold_val:g}"

    closest = min(nums, key=lambda n: abs(n - gold_val))
    return False, f"给出数值 {closest:g}，与 gold {gold_val:g} 不符"


def is_refusal(answer: str) -> bool:
    a = answer.lower()
    return any(m in a for m in _REFUSAL_MARKERS)


def is_vague(answer: str) -> bool:
    has_vague = any(m in answer.lower() for m in _VAGUE_MARKERS)
    has_number = len(extract_numbers(answer)) > 0
    return has_vague and not has_number


# ---------------------------------------------------------------------------
# 单题判定
# ---------------------------------------------------------------------------

def judge(raw_answer: str | None, q: dict) -> dict:
    if raw_answer is None:
        return {"verdict": "error", "reason": "无回答",
                "confidence": "high", "needs_review": False,
                "clean_answer": None}

    answer = strip_think(raw_answer)
    if not answer:
        # 剥离后为空：模型只输出了推理、没有最终答案
        return {"verdict": "error", "reason": "剥离推理后无有效答案",
                "confidence": "low", "needs_review": True,
                "clean_answer": ""}

    refusal = is_refusal(answer)          # BUG3：先判拒答

    if q["group"] == "A":
        hit, why = matches_gold(answer, q)
        if hit:
            return _mk("correct", why, "high", False, answer)
        if refusal:
            return _mk("wrong_refusal", "报告中有答案，但模型表示找不到",
                       "high", False, answer)
        nums = extract_numbers(answer)
        if nums:
            return _mk("factual_error", why, "medium", True, answer)
        return _mk("factual_error", "未拒答也未给出可识别数值，暂判事实错误",
                   "low", True, answer)

    else:  # B 组
        if refusal:
            return _mk("correct_refusal", "报告无此信息，模型正确拒答",
                       "high", False, answer)
        if is_vague(answer):
            return _mk("vague_pass", "泛泛而谈，未给具体数字也未明确拒答",
                       "medium", True, answer)
        nums = extract_numbers(answer)     # 已排除裸年份
        if nums:
            return _mk("hallucination",
                       f"报告无此信息，模型编造数值 {nums[0]:g}",
                       "high", False, answer)
        return _mk("vague_pass", "未给数字也未拒答，归入含糊",
                   "low", True, answer)


def _mk(verdict, reason, confidence, needs_review, clean):
    return {"verdict": verdict, "reason": reason, "confidence": confidence,
            "needs_review": needs_review, "clean_answer": clean}


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------

def score_run(run_path: Path, bench: dict) -> dict:
    run = json.loads(run_path.read_text(encoding="utf-8"))
    qmap = {q["id"]: q for q in bench["questions"]}

    details = []
    for r in run["results"]:
        q = qmap.get(r["id"])
        if not q:
            continue
        j = judge(r.get("answer"), q)
        details.append({
            "id": r["id"], "group": q["group"],
            "answer_locality": q.get("answer_locality"),
            "verdict": j["verdict"], "reason": j["reason"],
            "confidence": j["confidence"], "needs_review": j["needs_review"],
            "clean_answer": j["clean_answer"],
            "retrieved_pages": r.get("retrieved_pages"),
            "gold_page": q.get("source_page"),
            "output_tokens": r.get("output_tokens"),
            "latency_s": r.get("latency_s"),
        })

    A = [d for d in details if d["group"] == "A"]
    B = [d for d in details if d["group"] == "B"]

    def rate(items, verdict):
        return (sum(1 for d in items if d["verdict"] == verdict) / len(items)
                if items else 0.0)

    # 检索命中率（gold_page 是否在 retrieved_pages 中）
    a_with_page = [d for d in A if d["gold_page"] and d["retrieved_pages"]]
    recall = (sum(1 for d in a_with_page if d["gold_page"] in d["retrieved_pages"])
              / len(a_with_page)) if a_with_page else None

    summary = {
        "run": run_path.stem,
        "prompt_variant": run["config"].get("prompt_variant"),
        "n_A": len(A), "n_B": len(B),
        "accuracy": rate(A, "correct"),
        "factual_error_rate": rate(A, "factual_error"),
        "wrong_refusal_rate": rate(A, "wrong_refusal"),
        "hallucination_rate": rate(B, "hallucination"),
        "correct_refusal_rate": rate(B, "correct_refusal"),
        "vague_pass_rate": rate(B, "vague_pass"),
        "retrieval_recall": recall,
        "needs_review": sum(1 for d in details if d["needs_review"]),
        "avg_output_tokens": (
            round(sum(d["output_tokens"] or 0 for d in details) / len(details), 1)
            if details else 0),
        "avg_latency_s": (
            round(sum(d["latency_s"] or 0 for d in details) / len(details), 2)
            if details else 0),
    }
    return {"summary": summary, "details": details}


def print_summary(s: dict) -> None:
    print(f"\n【{s['run']}】prompt = {s['prompt_variant']}")
    print(f"  A 组 {s['n_A']} 题：准确 {s['accuracy']:.0%}  "
          f"事实错误 {s['factual_error_rate']:.0%}  "
          f"错误拒答 {s['wrong_refusal_rate']:.0%}")
    print(f"  B 组 {s['n_B']} 题：幻觉 {s['hallucination_rate']:.0%}  "
          f"正确拒答 {s['correct_refusal_rate']:.0%}  "
          f"含糊 {s['vague_pass_rate']:.0%}")
    if s["retrieval_recall"] is not None:
        print(f"  检索命中率（标准答案页进入 top-{5}）：{s['retrieval_recall']:.0%}")
    print(f"  成本：平均 {s['avg_output_tokens']:.0f} tokens / "
          f"{s['avg_latency_s']:.1f}s 每题")
    if s["needs_review"]:
        print(f"  ⚠ {s['needs_review']} 题置信度不高，建议人工复核（--detail 查看）")


def print_detail(details: list[dict]) -> None:
    print("\n逐题明细：")
    print("-" * 78)
    for d in details:
        flag = "⚠" if d["needs_review"] else " "
        loc = f"/{d['answer_locality']}" if d["answer_locality"] else ""
        print(f"{flag} {d['id']} [{d['group']}{loc}] → {d['verdict']}"
              f"（{d['confidence']}）")
        print(f"    判据：{d['reason']}")
        if d["group"] == "A" and d["retrieved_pages"] is not None:
            hit = "✓" if d["gold_page"] in (d["retrieved_pages"] or []) else "✗"
            print(f"    检索页 {d['retrieved_pages']}｜标准答案在第 {d['gold_page']} 页 {hit}")
        if d["clean_answer"]:
            print(f"    回答：{d['clean_answer'][:120].strip()}…")
        print()


def main() -> int:
    p = argparse.ArgumentParser(description="RAG 评测判分")
    p.add_argument("run", nargs="?", help="要判定的 run_*.json")
    p.add_argument("--all", action="store_true", help="判定 runs/ 下全部 run 并汇总对比")
    p.add_argument("--detail", action="store_true", help="打印逐题明细")
    args = p.parse_args()

    bench = json.loads(BENCHMARK.read_text(encoding="utf-8"))

    if args.all:
        runs = sorted(RUNS_DIR.glob("run_*.json"))
        runs = [r for r in runs if not r.stem.endswith("_scored")]
        if not runs:
            print("runs/ 下没有 run_*.json，先运行 rag_baseline.py")
            return 1
        summaries = []
        for rp in runs:
            res = score_run(rp, bench)
            summaries.append(res["summary"])
            print_summary(res["summary"])
        print("\n" + "=" * 78)
        print("四组对照（这就是权衡曲线的数据）：\n")
        print(f"{'prompt':16}{'准确率':>8}{'错误拒答':>10}{'幻觉率':>8}{'正确拒答':>10}{'含糊':>8}")
        print("-" * 78)
        for s in summaries:
            print(f"{str(s['prompt_variant']):16}"
                  f"{s['accuracy']:>7.0%}"
                  f"{s['wrong_refusal_rate']:>9.0%}"
                  f"{s['hallucination_rate']:>8.0%}"
                  f"{s['correct_refusal_rate']:>9.0%}"
                  f"{s['vague_pass_rate']:>8.0%}")
        return 0

    if not args.run:
        p.error("需要指定 run 文件，或用 --all")

    run_path = Path(args.run)
    if not run_path.is_absolute():
        run_path = ROOT / run_path
    res = score_run(run_path, bench)
    print_summary(res["summary"])
    if args.detail:
        print_detail(res["details"])

    out = run_path.with_name(run_path.stem + "_scored.json")
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n判分结果写入 {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
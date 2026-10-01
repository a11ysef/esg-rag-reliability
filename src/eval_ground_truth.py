#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 0最后一步：拿人工核对过的ground truth（analysis/ground_truth.json，直接翻
原始PDF原文确认过的，完全不依赖pipeline自己的输出——不能自己给自己打分）跟
pipeline真实跑出来的结果（analysis/outputs/esg_comparison.json）一条条对比，
算出真正的precision/recall/F1，不再用之前那种"披露了几条就算几条"的粗糙数法。
（"披露了"不代表"取对了"，这正是这个项目从一开始就想验证的事：检索命中率不等于
取数准确率——如果连评估这一步自己都还停留在"披露了几条"这种粗糙的统计上，那
就是拿一套更粗糙的标准去检验一套更精细的架构，这说不过去。）

怎么判定TP/FP/FN/TN：
  - TP（真的抓对了）：ground truth说这项指标有披露，pipeline也说披露了，而且
    抓到的具体数值（以及年份，如果ground truth指定了年份的话）跟ground truth
    对得上——是"抓对了"，不只是"抓到了"。
  - FP（抓错了）：pipeline说披露了，但要么ground truth说这家公司根本没披露这项
    指标（pipeline抓错了口径，或者抓到了不相关的数字），要么ground truth确实
    有披露，但pipeline取到的数值或年份对不上（抓到了别的数）。
  - FN（该抓的没抓到）：ground truth说有披露，但pipeline老实说"未披露"——本来
    能取到的数字没取到，是错过了机会，但没有编造，这是"保守"型的错误，跟FP那种
    "自信但抓错了"的错误性质不一样，两种都要算进去，但不能混为一谈。
  - TN（正确地说没有）：ground truth说这家公司确实没披露，pipeline也老实说
    "未披露"——认对了"这里确实没有"，不算错过。

数值对不对用相对误差来判断（默认0.5%，只是为了容忍四舍五入、千分位这类无害的
写法差异，不是放宽标准），年份则要求完全一致（前提是ground truth指定了年份）。
有些指标（目前项目里只有supply_chain_emissions这一个）披露的方式本来就五花
八门，压根没有"唯一正确数字"这个说法（IndicatorSpec的disambiguation里本来就
写明允许好几种数字都算数），这类指标就退化成看"disclosed判断是否一致，加上
quote跟ground truth描述的真实段落是不是沾边（关键词有没有重叠）"来判定，报告
里会明确标成"宽松匹配"，不会跟严格数值匹配的指标混在一起、冒充是同一种精度。

用法：
    python3 src/eval_ground_truth.py
（纯本地计算，不需要API key，也不需要联网——ground truth和pipeline的输出都已经
是磁盘上现成的JSON文件了。）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GT_PATH = ROOT / "analysis" / "ground_truth.json"
PIPELINE_PATH = ROOT / "analysis" / "outputs" / "esg_comparison.json"
REPORT_PATH = ROOT / "analysis" / "outputs" / "precision_recall_report.md"

NUMERIC_TOLERANCE = 0.005  # 相对误差 0.5%——只吸收四舍五入/千分位这类无害差异，不是放水


def _numeric_close(a: float, b: float) -> bool:
    if a is None or b is None:
        return False
    if a == b == 0:
        return True
    return abs(a - b) <= NUMERIC_TOLERANCE * max(abs(a), abs(b))


def _keyword_overlap_ok(pipeline_quote: str, gt_quote: str) -> bool:
    """
    supply_chain_emissions 这类"披露形式多样、没有单一正确数字"的指标用的宽松匹配：
    不要求数值完全一致，只要求 pipeline 抓到的证据段落跟 ground truth 描述的真实段落
    确实是同一件事（关键数字/关键词有重叠），不是随便抓了一个不相关的供应商话术就算数。
    """
    if not pipeline_quote or not gt_quote:
        return False
    import re
    gt_numbers = set(re.findall(r"\d[\d,]*", gt_quote))
    pipeline_numbers = set(re.findall(r"\d[\d,]*", pipeline_quote))
    # 至少有一个具体数字是两边共有的（比如"39"、"28%"这种），才算真的抓到了同一件事，
    # 不是泛泛地都提到了"supplier"这个词就蒙混过关。
    return bool(gt_numbers & pipeline_numbers)


def classify(gt: dict, pred: dict | None, indicator_id: str) -> tuple[str, str]:
    """返回 (TP/FP/FN/TN, 一句话原因)。"""
    gt_disclosed = gt.get("disclosed", False)
    pred_disclosed = bool(pred and pred.get("disclosed"))

    if not gt_disclosed and not pred_disclosed:
        return "TN", "ground truth 确实未披露，pipeline 也如实标未披露"
    if not gt_disclosed and pred_disclosed:
        return "FP", f"ground truth 确实未披露，但 pipeline 抓出了 {pred.get('value')!r}（口径对不上，误判为已披露）"
    if gt_disclosed and not pred_disclosed:
        return "FN", "ground truth 确实有披露，但 pipeline 保守地标了未披露，错失了本来能取到的数字"

    # 走到这里：gt_disclosed=True 且 pred_disclosed=True，要看具体取值对不对
    is_flexible = gt.get("value") == "flexible"
    if is_flexible:
        if _keyword_overlap_ok(pred.get("quote", ""), gt.get("quote", "")):
            return "TP", "宽松匹配（供应链类指标披露形式多样）：pipeline 的引用跟 ground truth 描述的真实段落有具体数字重叠，确认抓到了同一件事"
        return "FP", "宽松匹配未通过：pipeline 说披露了，但引用内容跟 ground truth 描述的真实段落对不上，很可能抓错了别的段落"

    gt_val = gt.get("numeric_value")
    pred_val = pred.get("numeric_value")
    val_ok = _numeric_close(gt_val, pred_val)
    year_ok = True
    if gt.get("year"):
        year_ok = str(pred.get("year")) == str(gt.get("year"))
    if val_ok and year_ok:
        return "TP", f"数值/年份都对得上（{pred_val} vs ground truth {gt_val}，{gt.get('year')}年）"
    if not val_ok:
        return "FP", f"数值对不上：pipeline 取到 {pred_val}，ground truth 是 {gt_val}（很可能抓到了别的年份/口径/子类目）"
    return "FP", f"年份对不上：pipeline 说是 {pred.get('year')} 年，ground truth 是 {gt.get('year')} 年（很可能抓到了历史基线值而不是目标年份）"


def main() -> int:
    if not GT_PATH.exists():
        print(f"找不到 ground truth 文件：{GT_PATH}", file=sys.stderr)
        return 1
    if not PIPELINE_PATH.exists():
        print(f"找不到 pipeline 输出：{PIPELINE_PATH}（先跑一次 python3 src/analyst.py 生成全量结果）",
              file=sys.stderr)
        return 1

    ground_truth = json.loads(GT_PATH.read_text(encoding="utf-8"))
    pipeline_raw = json.loads(PIPELINE_PATH.read_text(encoding="utf-8"))

    # pipeline 输出是 [{"company": ..., "indicators": [...]}]，转成 (company, indicator_id) -> 结果 的查表
    pred_by_key: dict[tuple[str, str], dict] = {}
    for company_block in pipeline_raw:
        company = company_block.get("company")
        for ind in company_block.get("indicators", []):
            pred_by_key[(company, ind.get("indicator_id"))] = ind

    rows = []
    counts = {"TP": 0, "FP": 0, "FN": 0, "TN": 0}
    for company, indicators in ground_truth.items():
        if company.startswith("_"):
            continue
        for indicator_id, gt in indicators.items():
            if indicator_id.startswith("_"):
                continue
            pred = pred_by_key.get((company, indicator_id))
            if pred is None:
                # ground truth 里有、pipeline 这次跑的范围里没有这家公司/这个指标——
                # 不计入统计（不是"错误"，是这次没跑到），但要在报告里如实说明，不能
                # 悄悄跳过让分母看起来更小、指标看起来更好看。
                rows.append({
                    "company": company, "indicator_id": indicator_id,
                    "verdict": "SKIPPED", "reason": "pipeline 这次运行没有覆盖到这家公司/这个指标",
                    "gt_value": gt.get("value"), "gt_year": gt.get("year"),
                    "pred_value": None, "pred_year": None,
                })
                continue
            verdict, reason = classify(gt, pred, indicator_id)
            counts[verdict] += 1
            rows.append({
                "company": company, "indicator_id": indicator_id,
                "verdict": verdict, "reason": reason,
                "gt_value": gt.get("value"), "gt_year": gt.get("year"),
                "pred_value": pred.get("value"), "pred_year": pred.get("year"),
            })

    tp, fp, fn, tn = counts["TP"], counts["FP"], counts["FN"], counts["TN"]
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = (2 * precision * recall / (precision + recall)
          if (tp + fp) and (tp + fn) and (precision + recall) else float("nan"))

    print(f"=== 混淆矩阵 === TP={tp}  FP={fp}  FN={fn}  TN={tn}")
    print(f"precision = {precision:.3f}   recall = {recall:.3f}   F1 = {f1:.3f}")
    print()
    for r in rows:
        mark = {"TP": "✓", "FP": "✗FP", "FN": "✗FN", "TN": "✓TN", "SKIPPED": "—"}[r["verdict"]]
        print(f"[{mark}] {r['company']} / {r['indicator_id']}: "
              f"ground truth={r['gt_value']}({r['gt_year']})  pipeline={r['pred_value']}({r['pred_year']})")
        print(f"      {r['reason']}")

    # 写一份 markdown 报告，同时更新 PRD 里成功指标表格要引用的三个数字
    lines = [
        "# Precision / Recall / F1 评估报告",
        "",
        f"ground truth 来源：`analysis/ground_truth.json`（人工直接核对原始 PDF 原文确认，"
        f"跟 pipeline 输出完全独立生成）。pipeline 输出来源：`analysis/outputs/esg_comparison.json`。",
        "",
        "## 混淆矩阵",
        "",
        "| | pipeline 说披露 | pipeline 说未披露 |",
        "|---|---|---|",
        f"| **ground truth 确实披露** | TP = {tp} | FN = {fn} |",
        f"| **ground truth 确实未披露** | FP = {fp} | TN = {tn} |",
        "",
        f"- **Precision（取到的里面有多少是对的）= {precision:.1%}**",
        f"- **Recall（该取到的里面取到了多少）= {recall:.1%}**",
        f"- **F1 = {f1:.3f}**",
        "",
        "## 逐条明细",
        "",
        "| 公司 | 指标 | 判定 | ground truth | pipeline 结果 | 原因 |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        gt_str = f"{r['gt_value']}（{r['gt_year']}）" if r['gt_value'] else "未披露"
        pred_str = f"{r['pred_value']}（{r['pred_year']}）" if r['pred_value'] else "未披露"
        lines.append(f"| {r['company']} | {r['indicator_id']} | {r['verdict']} | {gt_str} | {pred_str} | {r['reason']} |")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n完整报告已写入 {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

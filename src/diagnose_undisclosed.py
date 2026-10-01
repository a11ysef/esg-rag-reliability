#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
diagnose_undisclosed.py —— 把每一条"未披露"自动分个类，说清楚具体是卡在哪儿，
不用每次都手动翻JSON去查

背景：这个项目从一开始就是"宁可少抓一点也不能抓错"的原则，但这样一来"未披露"
这三个字背后其实混着好几种完全不一样的情况：
  - 压根没检索到相关的页面（retrieval_miss）——不知道是 TOP_K 该调大了，还是
    这份报告本来就没披露这个数字；
  - 模型看过候选内容了，自己判断"这段不是答案"（model_not_in_passage）——
    可能是模型太保守，也可能这份报告确实没写这个数字；
  - 候选内容被四层机械校验里的某一层拦下了（mechanical_reject_*）——这一类
    最值得往下挖，因为"被拦下"不代表"这个数字真的是错的"，也可能是校验规则
    本身（比如 value_not_numeric、quote_not_genuine 这些）有考虑不到的边界情况
    （这个项目已经因为这种问题真的返工过好几次：_to_float 没处理 million/billion
    这种写法、单位说明跟数字断开导致误判……每次都是从"明明拦下了、但数字其实
    是对的"这种案例里发现的）；
  - 表格重建定位到了对应的行，但原文没有年份表头，没法确认这行到底是哪一年的
    （table_row_found_year_unconfirmed）——这个不算错，就是老老实实卡在
    "确实没办法确认"这一步。

光看终端打出来一行"✗ 未披露"，根本分不清是哪种情况，只能每次都手动去翻
attempts 的详细记录——这个脚本干的事就是把这套一直在用的手动排查方法变成一个
现成的工具，跑一下就能看清楚所有"未披露"到底卡在哪一类、值不值得再花时间去修。

用法：
    python src/diagnose_undisclosed.py                          # 读默认的 esg_comparison.json
    python src/diagnose_undisclosed.py --json path/to/other.json
    python src/diagnose_undisclosed.py --out analysis/outputs/undisclosed_diagnosis.md
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JSON = ROOT / "analysis" / "outputs" / "esg_comparison.json"
DEFAULT_OUT = ROOT / "analysis" / "outputs" / "undisclosed_diagnosis.md"

# 这几个前缀要跟 analyst.py 里 extract_indicator() 实际写 rejected_reason 时用的
# 前缀对得上（改这儿之前先去 analyst.py 确认一下前缀有没有变，两边得保持一致）。
_REASON_LABELS = {
    "value_not_numeric": "模型把承诺/目标话术误当成了数值",
    "quote_not_genuine": "模型给的证据原文里找不到（疑似编造，或合成文本格式让证据断开了）",
    "unit_mismatch": "单位对不上这个指标允许的单位列表",
    "quote_missing_keyword": "证据里没出现这个指标要求的关键词（很可能抓错了旁边相似的行）",
    "keyword_too_far_from_value": "关键词和数值都在证据里，但离得太远，可能不是同一件事",
}


def _classify_attempt(att: dict) -> str:
    reason = att.get("rejected_reason")
    if reason:
        for prefix in _REASON_LABELS:
            if reason.startswith(prefix):
                return f"mechanical_reject:{prefix}"
        return "mechanical_reject:other"
    raw = (att.get("raw_response") or "").strip()
    if raw == "NOT_IN_PASSAGE" or not raw:
        return "model_not_in_passage"
    return "unparseable_response"   # 模型回的东西不是预期的格式，_parse_json_response 兜底也没解析出来


def diagnose(data: list) -> list:
    """把每条未披露的指标都诊断一遍，按公司+指标分组，返回诊断结果。"""
    results = []
    for company_block in data:
        company = company_block["company"]
        for ind in company_block.get("indicators", []):
            if ind.get("disclosed"):
                continue
            attempts = ind.get("attempts", [])
            cats = Counter(_classify_attempt(a) for a in attempts)
            partial = ind.get("partial_table_rows") or []
            results.append({
                "company": company,
                "indicator_id": ind["indicator_id"],
                "label_cn": ind.get("label_cn", ind["indicator_id"]),
                "n_attempts": len(attempts),
                "categories": cats,
                "partial_table_rows": partial,
            })
    return results


def _dominant_category(cats: Counter) -> str:
    if not cats:
        return "no_attempts"   # 一个候选内容都没跑过，一般是检索那步压根没召回任何东西
    return cats.most_common(1)[0][0]


def _explain(cats: Counter, partial_rows: list) -> str:
    if partial_rows:
        return (f"表格重建定位到 {len(partial_rows)} 条疑似匹配的表格行，"
                f"但原文缺年份表头，无法确认对应哪一年——不算错，是真的没办法确认。")
    dom = _dominant_category(cats)
    if dom == "no_attempts":
        return "检索阶段就没有召回任何候选片段——查一下 TOP_K/query 措辞，或者这份报告确实没有相关表述。"
    if dom == "model_not_in_passage":
        return "绝大多数候选片段模型都判定'不是答案'——真实的检索/模型层面未命中，值得看看候选片段本身是否真的不含这个数据。"
    if dom == "unparseable_response":
        return "模型返回的内容解析不出预期的 JSON 结构——建议直接看 raw_response 原始输出，可能是提示词或模型输出格式问题。"
    if dom.startswith("mechanical_reject:"):
        reason_key = dom.split(":", 1)[1]
        label = _REASON_LABELS.get(reason_key, reason_key)
        return f"被机械校验拦下，主要原因「{label}」——建议抽一条真实 attempt 看看这次拦截是不是冤枉的。"
    return "原因不明确，建议人工查看 attempts 明细。"


def render_report(results: list) -> str:
    lines = ["# 「未披露」诊断报告", "",
             "自动把每一条「未披露」按候选片段的真实处理结果归类，不是靠终端上一行"
             "「✗ 未披露」猜原因。", ""]
    by_category_count = Counter()
    for r in results:
        by_category_count[_dominant_category(r["categories"])] += 1

    lines += ["## 总览", ""]
    total = len(results)
    lines.append(f"共 {total} 条未披露指标。")
    for cat, n in by_category_count.most_common():
        lines.append(f"- {cat}: {n} 条")
    lines.append("")

    lines += ["## 逐条明细", ""]
    for r in results:
        lines.append(f"### {r['company']} / {r['label_cn']} ({r['indicator_id']})")
        lines.append("")
        lines.append(f"候选片段数：{r['n_attempts']}")
        if r["categories"]:
            cat_str = "、".join(f"{k}×{v}" for k, v in r["categories"].most_common())
            lines.append(f"分类统计：{cat_str}")
        lines.append(f"诊断：{_explain(r['categories'], r['partial_table_rows'])}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description="把每条「未披露」自动归类成具体原因")
    p.add_argument("--json", default=str(DEFAULT_JSON))
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--no-write", action="store_true", help="只在终端打印，不写文件")
    args = p.parse_args()

    json_path = Path(args.json)
    if not json_path.is_file():
        print(f"找不到 {json_path}——先跑一次 python src/analyst.py 生成结果再来诊断。")
        return 1

    data = json.loads(json_path.read_text(encoding="utf-8"))
    results = diagnose(data)

    if not results:
        print("没有任何「未披露」的指标——全部已核实，没什么好诊断的。")
        return 0

    report = render_report(results)
    print(report)

    if not args.no_write:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
        print(f"\n（已写入 {out_path}）")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
score_companies.py —— 按「抽取结果经得起检查」给每家公司排序，选出效果最好的十家。

每一条「已核实」的数字都要通过下面全部检查，否则不计入排名，并写明原因：
  1. 有年份（年份为空或 not stated 的不计入）
  2. 单位类型和指标对得上：排放量、取水量不能是百分比；可再生能源占比、废弃物转移率必须是百分比
  3. 百分比不超过 100%
  4. 不是强度值（每平方英尺、每 1000 平方英尺、每营收、per xxx、normalized 等）
  5. 不是局部范围（食物废弃物、建筑废弃物、试点 pilot、配送中心、总部、地表水等）
  6. 原文确实在讲这个指标（比如可再生能源的原文里必须出现 renewable）
  7. 原文里出现的大数字不超过 2 个（超过的多半是表格串列，数字可能对错列）

输出两个文件（都在结果文件所在的文件夹里）：
  ranking.csv       每家公司的排名
  review_items.csv  每一条「已核实」的数字，以及通过或被排除的原因，方便你逐条过目

注意：这个排序衡量的是抽取结果经得起这些检查的程度，不等于准确率。准确率要靠人工对照原文核对。

用法：
    python3 analysis/score_companies.py analysis/outputs/batch50/esg_comparison.json
"""
import csv
import json
import re
import sys
from pathlib import Path

EMISSION_IDS = {"scope1_emissions", "scope2_emissions", "scope3_emissions"}
VOLUME_IDS = {"water_withdrawal"}
PERCENT_IDS = {"renewable_energy_pct", "waste_diversion_pct"}
HAND_CHECKED = {"Alphabet", "Apple", "Meta Platforms"}  # 这三家做过人工对照原文

RELEVANCE = {
    "scope1_emissions": r"scope|ghg|greenhouse|emission|co2|co₂|tco",
    "scope2_emissions": r"scope|ghg|greenhouse|emission|co2|co₂|tco",
    "scope3_emissions": r"scope|ghg|greenhouse|emission|co2|co₂|tco",
    "renewable_energy_pct": r"renewable",
    "water_withdrawal": r"water",
    "waste_diversion_pct": r"waste|landfill|divers",
    "supply_chain_emissions": r"supplier|supply|audit",
}

SCOPE_LIMIT = re.compile(
    r"food|construction|\bpilot\b|distribution cent|headquarter|facilit|partial|subset|"
    r"surface water|groundwater|ground water|single site|one site", re.I)

INTENSITY = re.compile(
    r"\bper\b|intensity|normali[sz]ed|by revenue|/\s*(1,?000|ft|sq|usd|\$|employee|"
    r"revenue|store|m2|m²)", re.I)

EMISSION_UNIT_OK = re.compile(r"co2|co₂|coe|tco|tonne|\bton|\bmt\b|mtco|metric|ghg", re.I)
WATER_UNIT_OK = re.compile(r"cubic|megalit|gallon|m3|m³|litre|liter|\bml\b", re.I)
YEAR_OK = re.compile(r"(19|20)\d{2}|FY\s?\d{2}|fiscal", re.I)
FUTURE_YEAR = re.compile(r"\b20(2[6-9]|[3-9]\d)\b")
TARGET_WORDS = re.compile(r"goal|target|commit|pledge|aim|ambition|by\s+20|net[- ]zero|plan", re.I)
METHOD_WORDS = re.compile(r"matched|certificate|\bREC", re.I)
YEAR_IN_TEXT = re.compile(r"\b(19|20)\d{2}\b|FY\s?\d{2}", re.I)
BIG_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\b\d{3,}(?:\.\d+)?\b")


def parse_num(numeric_value, text):
    if numeric_value is not None:
        try:
            return float(numeric_value)
        except (TypeError, ValueError):
            pass
    m = re.search(r"-?\d[\d,]*\.?\d*", text.replace(",", ""))
    return float(m.group(0)) if m else None


def year_of(text):
    m = re.search(r"(19|20)\d{2}", text)
    if m:
        return int(m.group(0))
    m = re.search(r"FY\s?(\d{2})", text, re.I)
    if m:
        return 2000 + int(m.group(1))
    return None


def check(ind):
    """返回这一条没通过的原因列表；空列表表示通过。"""
    reasons = []
    iid = ind.get("indicator_id", "")
    value = "" if ind.get("value") is None else str(ind.get("value"))
    unit = ind.get("unit") or ""
    year = ind.get("year")
    quote = (ind.get("quote") or "").strip()
    text_all = f"{value} {unit}"
    num = parse_num(ind.get("numeric_value"), value)

    if not quote:
        reasons.append("无原文")
    if year is None or not YEAR_OK.search(str(year)):
        reasons.append("无年份")
    elif year_of(str(year)) is not None and year_of(str(year)) < 2022:
        reasons.append("年份较旧（2022年以前）")
    if iid in EMISSION_IDS | VOLUME_IDS and "%" in text_all:
        reasons.append("单位是百分比，不是数量")
    if iid in EMISSION_IDS and not EMISSION_UNIT_OK.search(unit):
        reasons.append("单位与排放量不符")
    if iid in VOLUME_IDS and not WATER_UNIT_OK.search(unit):
        reasons.append("单位与取水量不符")
    if iid in PERCENT_IDS:
        if "%" not in text_all and "percent" not in text_all.lower():
            reasons.append("单位不是百分比")
        if num is not None and num > 100:
            reasons.append("百分比超过100%")
    if quote and value:
        q_clean = quote.replace(",", "")
        nums = [n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", value)]
        if nums and not all(n in q_clean for n in nums):
            reasons.append("数值在原文中找不到")
    if iid in RELEVANCE and quote and not re.search(RELEVANCE[iid], quote, re.I):
        reasons.append("原文未提及该指标")
    if quote and SCOPE_LIMIT.search(quote):
        reasons.append("原文说明的是局部范围")
    if INTENSITY.search(f"{unit} {quote}"):
        reasons.append("强度值而非总量")
    if quote and FUTURE_YEAR.search(quote) and TARGET_WORDS.search(quote):
        reasons.append("疑似目标值，不是现状")
    if quote and len(BIG_NUMBER.findall(YEAR_IN_TEXT.sub("", quote))) > 2:
        reasons.append("原文数字过多，疑似表格错列")
    return reasons


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    path = Path(sys.argv[1])
    data = json.loads(path.read_text(encoding="utf-8"))

    ranking, review = [], []
    for entry in data:
        company = entry["company"]
        inds = entry.get("indicators", [])
        disclosed = [i for i in inds if i.get("disclosed")]
        passed, excluded = 0, 0
        reason_counts = {}
        for ind in disclosed:
            reasons = check(ind)
            if reasons:
                excluded += 1
                for r in reasons:
                    reason_counts[r] = reason_counts.get(r, 0) + 1
            else:
                passed += 1
            quote = (ind.get("quote") or "").replace("\n", " ")
            review.append({
                "公司": company,
                "指标": ind.get("indicator_id"),
                "数值": ind.get("value"),
                "单位": ind.get("unit"),
                "年份": ind.get("year"),
                "页码": ind.get("page"),
                "结果": "排除" if reasons else "通过",
                "排除原因": "；".join(reasons),
                "口径提醒": ("可再生电力按证书或采购口径计，请确认是否等于实际占比"
                            if ind.get("indicator_id") == "renewable_energy_pct"
                            and METHOD_WORDS.search(quote) else ""),
                "原文片段": quote[:160],
            })
        suspect = sum(1 for i in inds if not i.get("disclosed") and i.get("partial_table_rows"))
        ranking.append({
            "公司": company,
            "通过检查": passed,
            "已抽取": len(disclosed),
            "被排除": excluded,
            "疑似待查": suspect,
            "主要排除原因": "；".join(f"{k}×{v}" for k, v in sorted(reason_counts.items(), key=lambda x: -x[1])),
            "人工核对过": "是" if company in HAND_CHECKED else "否",
        })

    if not ranking:
        print("结果文件里没有公司，请检查上一步是否报错。")
        return 1

    ranking.sort(key=lambda r: (-r["通过检查"], -(r["通过检查"] / r["已抽取"]) if r["已抽取"] else 0, r["公司"]))
    for n, r in enumerate(ranking, 1):
        r["排名"] = n
        r["前十"] = "是" if n <= 10 else ""
    ranking = [{k: r[k] for k in ["排名", "公司", "通过检查", "已抽取", "被排除", "疑似待查",
                                  "主要排除原因", "人工核对过", "前十"]} for r in ranking]

    out = path.parent
    with (out / "ranking.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(ranking[0].keys()))
        w.writeheader()
        w.writerows(ranking)
    with (out / "review_items.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(review[0].keys()) if review else ["公司"])
        w.writeheader()
        w.writerows(review)

    print(f"共 {len(ranking)} 家公司。排名写入 {out / 'ranking.csv'}")
    print(f"逐条检查明细写入 {out / 'review_items.csv'}\n")
    print("排名  公司                       通过/已抽取  被排除  人工核对  主要排除原因")
    for r in ranking:
        mark = "  ← 前十" if r["排名"] == 10 else ""
        print(f"{r['排名']:>4}  {r['公司']:<26} {r['通过检查']:>3}/{r['已抽取']:<3}     "
              f"{r['被排除']:>4}    {r['人工核对过']:<6}  {r['主要排除原因'][:60]}{mark}")
    print("\n前十家：" + "、".join(r["公司"] for r in ranking[:10]))
    print("\n提醒：这个排序只反映抽取结果经得起检查的程度，不是准确率。入选后请抽两三家对照原文核对。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

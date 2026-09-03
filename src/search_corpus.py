#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
search_corpus.py —— 语料检索辅助工具（标注 benchmark 时用）

作用：在已重建的 chunks.csv 里按关键词搜索，直接告诉你
「这个指标在第几页、原文长什么样」，省掉一页页翻 PDF 的时间。

这是纯文本检索，不调用模型、不做向量检索，因此很快。
它的用途是帮你定位候选题目，gold_answer 仍需你在 PDF 原文中确认。

用法：
    # 搜关键词
    python src/search_corpus.py Alphabet "Scope 1"

    # 只看包含数字的结果（标 A 组题时最有用）
    python src/search_corpus.py Alphabet "Scope 1" --numbers

    # 列出某公司数字最密集的页（从这些页开始标注效率最高）
    python src/search_corpus.py Alphabet --hotspots

    # 查看指定页的全部内容
    python src/search_corpus.py Alphabet --page 88
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

csv.field_size_limit(10 ** 9)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "esg_data"

# 千分位数字 / 百分比 / 带小数的数值
_NUM = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+\s*%?|\d+\s*%")


def find_chunks_csv(company: str) -> Path:
    """在 esg_data 下按公司名查找 chunks.csv"""
    matches = list(DATA_DIR.glob(f"*/{company}/corpus/chunks.csv"))
    if not matches:
        # 尝试模糊匹配
        fuzzy = [
            p for p in DATA_DIR.glob("*/*/corpus/chunks.csv")
            if company.lower() in p.parent.parent.name.lower()
        ]
        if len(fuzzy) == 1:
            return fuzzy[0]
        if len(fuzzy) > 1:
            names = ", ".join(sorted(p.parent.parent.name for p in fuzzy))
            raise SystemExit(f"公司名不明确，匹配到多个：{names}")
        available = sorted(p.parent.parent.name for p in DATA_DIR.glob("*/*/corpus/chunks.csv"))
        raise SystemExit(
            f"找不到 {company} 的语料。\n"
            f"已构建语料的公司：\n  " + "\n  ".join(available)
        )
    return matches[0]


def load_chunks(company: str) -> list[dict]:
    path = find_chunks_csv(company)
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["page"] = int(r["page"])
        r.pop("embeddings", None)      # 检索用不到，丢掉省内存
    return rows


def highlight(text: str, keyword: str, width: int = 200) -> list[str]:
    """返回关键词周围的上下文片段"""
    out = []
    for m in re.finditer(re.escape(keyword), text, flags=re.IGNORECASE):
        s = max(0, m.start() - width // 2)
        e = min(len(text), m.end() + width // 2)
        frag = text[s:e].replace("\n", " ")
        frag = re.sub(r"\s+", " ", frag).strip()
        out.append(("…" if s > 0 else "") + frag + ("…" if e < len(text) else ""))
    return out


def cmd_search(rows: list[dict], keyword: str, numbers_only: bool, limit: int) -> None:
    hits = 0
    for r in rows:
        content = r["content"]
        if keyword.lower() not in content.lower():
            continue
        if numbers_only and not _NUM.search(content):
            continue
        frags = highlight(content, keyword)
        for frag in frags:
            nums = _NUM.findall(frag)
            print(f"\n第 {r['page']} 页  [{r['chunk_id']}]"
                  + (f"  数值: {', '.join(nums[:6])}" if nums else ""))
            print(f"  {frag}")
            hits += 1
            if hits >= limit:
                print(f"\n（已显示前 {limit} 条，用 --limit 调整）")
                return
    if hits == 0:
        print(f"没有找到包含「{keyword}」的内容。换个说法试试，"
              f"比如用 ESG 报告里的英文原词。")
    else:
        print(f"\n共 {hits} 处匹配。")


def cmd_hotspots(rows: list[dict], top: int) -> None:
    """列出数字最密集的页——这些页通常是附录数据表，标 A 组题效率最高"""
    counter: Counter[int] = Counter()
    for r in rows:
        counter[r["page"]] += len(_NUM.findall(r["content"]))

    print(f"数字最密集的 {top} 页（建议从这些页开始标注 A 组题）：\n")
    print(f"{'页码':>6}  {'数值个数':>8}   预览")
    print("-" * 78)
    for page, count in counter.most_common(top):
        if count == 0:
            continue
        preview = ""
        for r in rows:
            if r["page"] == page:
                preview = re.sub(r"\s+", " ", r["content"])[:60]
                break
        print(f"{page:>6}  {count:>8}   {preview}…")


def cmd_page(rows: list[dict], page: int) -> None:
    chunks = [r for r in rows if r["page"] == page]
    if not chunks:
        print(f"第 {page} 页没有内容。")
        return
    print(f"===== 第 {page} 页（{len(chunks)} 个 chunk）=====\n")
    for r in chunks:
        print(f"--- {r['chunk_id']} ---")
        print(r["content"])
        print()


def main() -> int:
    p = argparse.ArgumentParser(description="语料检索辅助工具")
    p.add_argument("company", help="公司名，如 Alphabet")
    p.add_argument("keyword", nargs="?", help="搜索关键词，如 'Scope 1'")
    p.add_argument("--numbers", action="store_true", help="只显示包含数值的结果")
    p.add_argument("--hotspots", action="store_true", help="列出数字最密集的页")
    p.add_argument("--page", type=int, help="查看指定页的全部内容")
    p.add_argument("--limit", type=int, default=20, help="最多显示多少条结果")
    p.add_argument("--top", type=int, default=15, help="hotspots 显示多少页")
    args = p.parse_args()

    rows = load_chunks(args.company)
    company_name = rows[0]["company"] if rows else args.company
    print(f"【{company_name}】共 {len(rows)} 个 chunk，"
          f"{max(r['page'] for r in rows)} 页\n")

    if args.hotspots:
        cmd_hotspots(rows, args.top)
    elif args.page is not None:
        cmd_page(rows, args.page)
    elif args.keyword:
        cmd_search(rows, args.keyword, args.numbers, args.limit)
    else:
        p.error("需要提供 keyword，或使用 --hotspots / --page")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
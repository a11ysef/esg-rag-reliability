#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
merge_benchmark.py —— 安全合并追加题库进 benchmark.json

为什么需要这个脚本：
  benchmark.json 是嵌套 JSON，手动复制粘贴新题极易破坏括号/逗号结构，
  且容易引入重复 id。本脚本自动完成合并、去重、校验、更新计数，
  并在写入前备份原文件。

用法：
    python eval/merge_benchmark.py apple_questions.json
    python eval/merge_benchmark.py meta_questions.json

    # 只检查不写入（预演）
    python eval/merge_benchmark.py apple_questions.json --dry-run
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCHMARK = ROOT / "eval" / "benchmark.json"

# 每道题必须具备的字段
REQUIRED_FIELDS = {"id", "group", "company", "question", "gold_answer"}


def validate_question(q: dict) -> list[str]:
    """返回该题的错误列表，空列表表示合格。"""
    errs = []
    missing = REQUIRED_FIELDS - set(q.keys())
    if missing:
        errs.append(f"缺少字段 {missing}")

    if q.get("group") not in ("A", "B"):
        errs.append(f"group 必须是 A 或 B，实际为 {q.get('group')!r}")

    if q.get("group") == "A":
        if q.get("gold_answer") in (None, ""):
            errs.append("A 组题 gold_answer 不能为空")
        if q.get("source_page") is None:
            errs.append("A 组题应有 source_page")
    elif q.get("group") == "B":
        if q.get("gold_answer") is not None:
            errs.append("B 组题 gold_answer 必须为 null")

    return errs


def main() -> int:
    p = argparse.ArgumentParser(description="合并追加题库进 benchmark.json")
    p.add_argument("addition", help="追加题库文件（含 questions 数组）")
    p.add_argument("--dry-run", action="store_true", help="只检查不写入")
    args = p.parse_args()

    add_path = Path(args.addition)
    if not add_path.is_absolute():
        # 允许从项目根或 outputs 目录传入
        for base in (ROOT, ROOT / "eval", Path.cwd()):
            if (base / add_path).exists():
                add_path = base / add_path
                break

    if not add_path.exists():
        print(f"找不到追加文件：{add_path}", file=sys.stderr)
        return 1
    if not BENCHMARK.exists():
        print(f"找不到 benchmark：{BENCHMARK}", file=sys.stderr)
        return 1

    bench = json.loads(BENCHMARK.read_text(encoding="utf-8"))
    addition = json.loads(add_path.read_text(encoding="utf-8"))

    existing = bench["questions"]
    existing_ids = {q["id"] for q in existing}
    new_questions = addition["questions"]

    # 校验
    all_errs = []
    dup_ids = []
    for q in new_questions:
        errs = validate_question(q)
        if errs:
            all_errs.append((q.get("id", "?"), errs))
        if q["id"] in existing_ids:
            dup_ids.append(q["id"])

    if dup_ids:
        print(f"✗ 以下 id 已存在于 benchmark，不能重复：{dup_ids}", file=sys.stderr)
        return 1
    if all_errs:
        print("✗ 校验未通过：", file=sys.stderr)
        for qid, errs in all_errs:
            print(f"  {qid}: {'; '.join(errs)}", file=sys.stderr)
        return 1

    # 合并
    merged = existing + new_questions
    bench["questions"] = merged

    # 更新计数
    n_A = sum(1 for q in merged if q["group"] == "A")
    n_B = sum(1 for q in merged if q["group"] == "B")
    bench["_meta"]["current_size"] = {"A": n_A, "B": n_B}
    bench["_meta"]["last_merged"] = str(date.today())

    # 按公司统计，方便核对
    by_company: dict[str, dict[str, int]] = {}
    for q in merged:
        c = q["company"]
        by_company.setdefault(c, {"A": 0, "B": 0})
        by_company[c][q["group"]] += 1

    print(f"合并 {len(new_questions)} 道新题（来自 {add_path.name}）")
    print(f"合并后总计：A {n_A} 题，B {n_B} 题\n")
    print("按公司分布：")
    for c, cnt in sorted(by_company.items()):
        print(f"  {c:18} A {cnt['A']:2}  B {cnt['B']:2}")

    if args.dry_run:
        print("\n[dry-run] 未写入。去掉 --dry-run 执行实际合并。")
        return 0

    # 备份后写入
    backup = BENCHMARK.with_suffix(".json.bak")
    shutil.copy(BENCHMARK, backup)
    BENCHMARK.write_text(
        json.dumps(bench, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n✓ 已写入 {BENCHMARK.relative_to(ROOT)}")
    print(f"  原文件已备份为 {backup.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
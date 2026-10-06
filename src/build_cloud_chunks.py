#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_cloud_chunks.py —— 给任意一家公司补一份云端(GLM)版本的chunk向量

背景：
    agent_cloud.py里"重算云端向量"那一步，公司名单是从eval/benchmark.json的题目里
    反推出来的，所以只会处理当时评测集里那3家公司。现在想往外扩公司，却没有一个
    现成的入口能单独说"再把这家公司也算一下"，这个脚本就是补这个口子用的。

    用的还是agent_cloud.py里原来那个rebuild_company_vectors函数，没有重写任何逻辑，
    只是换了一种方式告诉它该处理哪些公司，所以不影响已经验证过的那部分代码。

前提条件：
    这家公司得先用build_corpus.py跑过一遍，确保esg_data/<行业>/<公司>/corpus/chunks.csv
    这个文件存在。这个脚本不负责解析PDF，只负责把已经切好的chunk内容重新算一遍云端向量。

用法：
    export ZHIPU_API_KEY="你的key"

    # 指定公司，可以写多个（公司名要跟esg_data目录下的文件夹名完全一致）
    python3 src/build_cloud_chunks.py --companies "Microsoft" "Walmart"

    # 不指定公司：自动找出"已经跑过build_corpus.py、但云端向量还没算"的公司，一次性全部补齐
    python3 src/build_cloud_chunks.py

跟build_corpus.py一样支持中断重跑：已经算好的公司（analysis/cloud_chunks/<公司>.json
已存在）会被跳过，不会重复花API额度。
"""

from __future__ import annotations

import argparse

from agent_cloud import DATA_DIR, CLOUD_CHUNK_DIR, rebuild_company_vectors


def find_companies_with_local_corpus() -> list[str]:
    """从esg_data里找出所有已经跑过build_corpus.py的公司（corpus/chunks.csv存在的那些）"""
    names = [csv_path.parent.parent.name for csv_path in DATA_DIR.glob("*/*/corpus/chunks.csv")]
    return sorted(set(names))


def main() -> int:
    p = argparse.ArgumentParser(description="给指定公司补一份云端(GLM)版本的chunk向量")
    p.add_argument(
        "--companies", nargs="+",
        help="要处理的公司名，跟esg_data/<行业>/<公司名>的文件夹名要完全一致。"
             "不传的话，自动找出所有已经跑过build_corpus.py、但云端向量还没算的公司",
    )
    args = p.parse_args()

    if args.companies:
        targets = args.companies
    else:
        local_ready = find_companies_with_local_corpus()
        targets = [c for c in local_ready if not (CLOUD_CHUNK_DIR / f"{c}.json").exists()]
        if not targets:
            print("没有需要处理的公司：要么esg_data里还没有公司跑过build_corpus.py，"
                  "要么已经跑过的公司云端向量都算好了。")
            return 0
        print(f"自动找到 {len(targets)} 家公司还没算云端向量：{', '.join(targets)}")

    for company in targets:
        try:
            rebuild_company_vectors(company)
        except IndexError:
            # DATA_DIR.glob(...)[0] 在找不到chunks.csv的时候会报这个错，
            # 翻译成人话提示一下该先跑哪个脚本
            print(f"  [跳过] {company}：找不到 esg_data/*/{company}/corpus/chunks.csv，"
                  f"先用 python3 src/build_corpus.py --company \"{company}\" 跑一遍")
        except Exception as e:                # noqa: BLE001
            print(f"  [错误] {company}：{e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

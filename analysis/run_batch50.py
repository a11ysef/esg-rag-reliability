#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_batch50.py —— 把所有已经切好语料的公司跑一遍抽取，然后按证据齐全程度排名。

前提：先设置 API key
    export ZHIPU_API_KEY="你的key"

用法：
    python3 analysis/run_batch50.py

注意：会调用大量智谱接口（约 1.4 万次向量计算，外加每家公司 7 项指标的抽取调用），
耗时较长，也会产生费用。中途断了可以直接重跑：已经算好的向量会自动跳过。
结果写在 analysis/outputs/batch50/ 下，不会覆盖之前的 esg_comparison.json。
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "analysis" / "outputs" / "batch50"


def companies():
    names = [f.parent.parent.name for f in (ROOT / "esg_data").glob("*/*/corpus/chunks.csv")]
    return sorted(set(names))


def main():
    if not os.environ.get("ZHIPU_API_KEY"):
        print('请先设置：export ZHIPU_API_KEY="你的key"', file=sys.stderr)
        return 1

    names = companies()
    print(f"将处理 {len(names)} 家公司：")
    print("、".join(names))
    print()

    py = sys.executable
    subprocess.run([py, str(ROOT / "src" / "build_cloud_chunks.py"), "--companies", *names],
                   cwd=ROOT, check=False)
    subprocess.run([py, str(ROOT / "src" / "analyst.py"), "--companies", *names,
                    "--out-dir", str(OUT), "--skip-commitments"], cwd=ROOT, check=False)

    json_path = OUT / "esg_comparison.json"
    if not json_path.exists():
        print("没有生成结果文件，请看上面的报错信息。")
        return 1
    subprocess.run([py, str(ROOT / "analysis" / "score_companies.py"), str(json_path)],
                   cwd=ROOT, check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent_cloud.py —— 全云端 ReAct Agent（智谱 GLM）

背景（一个真实的部署取舍）：
  本地 8GB Mac 无法稳定同时运行生成模型与向量检索（反复 502、7B 直接崩溃）。
  这印证了私有化部署在 agentic 任务上的硬件约束。为验证 Agent 策略本身是否有效，
  Agent 环节接入云端 GLM API：embedding 与生成均走云，本地零内存占用。

设计（数据驱动，同 agent.py）：
  baseline 分析显示检索命中不决定准确率（57%≈58%），真瓶颈是"取数精度"
  （相似数字干扰）。故逐个片段做 VERIFY 核对标签/口径，而非一次性塞入 top-5。

维度对齐：
  本地 chunk 向量是 nomic(768维)，与智谱 embedding-3(2048维) 不兼容。
  故只对评测涉及的 3 家公司(约1000 chunk)用智谱重算为 2048 维，问题也用智谱，
  维度统一。评测集只用这三家，不影响任何结论。

两阶段（均带断点续跑，中断重跑不必从头）：
  阶段一：智谱 embedding 重算 3 家 chunk → analysis/cloud_chunks/<company>.json
  阶段二：智谱 embedding 算问题向量 + GLM-4-Flash 跑 ReAct Agent

用法：
    export ZHIPU_API_KEY="你的key"
    python src/agent_cloud.py               # 全量
    python src/agent_cloud.py --limit 6     # 只跑前 6 题
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import math
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

csv.field_size_limit(10 ** 9)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "esg_data"
RESULTS_DIR = ROOT / "analysis" / "runs"
CLOUD_CHUNK_DIR = ROOT / "analysis" / "cloud_chunks"
QEMB_CACHE = ROOT / "analysis" / "cloud_question_emb.json"

BASE = "https://open.bigmodel.cn/api/paas/v4"
EMBED_MODEL = "embedding-3"
GEN_MODEL = "glm-4-flash"       # 免费
TOP_K = 5
MAX_VERIFY = 3

KEY = os.environ.get("ZHIPU_API_KEY")


# ---------------------------------------------------------------------------
# 云端调用（带重试）
# ---------------------------------------------------------------------------

def _post(path, payload, timeout=60):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {KEY}"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _retry(fn, tries=4):
    last = None
    for a in range(tries):
        try:
            return fn()
        except Exception as e:               # noqa: BLE001
            last = e
            time.sleep(2 * (a + 1))
    raise last


def cloud_embed(text):
    d = _retry(lambda: _post("/embeddings", {"model": EMBED_MODEL, "input": text}))
    return d["data"][0]["embedding"]


_THINK = re.compile(r"<think>.*?</think>", flags=re.DOTALL | re.IGNORECASE)


def cloud_gen(system, user):
    def _call():
        d = _post("/chat/completions", {
            "model": GEN_MODEL,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": 0,
        })
        return d["choices"][0]["message"]["content"]
    t = _retry(_call)
    return _THINK.sub("", t).strip()


# ---------------------------------------------------------------------------
# 阶段一：重算 3 家 chunk 向量（断点续跑）
# ---------------------------------------------------------------------------

def rebuild_company_vectors(company):
    out = CLOUD_CHUNK_DIR / f"{company}.json"
    if out.exists():
        data = json.loads(out.read_text(encoding="utf-8"))
        print(f"  {company}: 已存在 {len(data)} 个云向量，跳过")
        return data

    src = list(DATA_DIR.glob(f"*/{company}/corpus/chunks.csv"))[0]
    rows = []
    with open(src, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            r.pop("embeddings", None)          # 丢掉旧的 768 维
            r["page"] = int(r["page"])
            rows.append(r)

    print(f"  {company}: 重算 {len(rows)} 个 chunk 的云向量...")
    data = []
    for i, r in enumerate(rows, 1):
        vec = cloud_embed(r["content"])
        data.append({"chunk_id": r["chunk_id"], "page": r["page"],
                     "content": r["content"], "vec": vec})
        if i % 50 == 0:
            print(f"    {i}/{len(rows)}", flush=True)
            # 中途也存盘，防止前功尽弃
            CLOUD_CHUNK_DIR.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(data), encoding="utf-8")
    CLOUD_CHUNK_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data), encoding="utf-8")
    print(f"  {company}: 完成，{len(data)} 个云向量已存盘")
    return data


# ---------------------------------------------------------------------------
# 检索 + ReAct
# ---------------------------------------------------------------------------

def cosine_top_k(qv, chunks, k):
    qn = math.sqrt(sum(x * x for x in qv)) + 1e-9
    scored = []
    for c in chunks:
        v = c["vec"]
        dot = sum(a * b for a, b in zip(qv, v))
        vn = math.sqrt(sum(x * x for x in v)) + 1e-9
        scored.append((c, dot / (qn * vn)))
    scored.sort(key=lambda x: -x[1])
    return scored[:k]


VERIFY_TMPL = (
    "Passage (from page {p}):\n{c}\n\n"
    "Question: {q}\n\n"
    "Steps:\n"
    "1. Identify exactly what the question asks: the subject, the year, and the "
    "specific metric/scope.\n"
    "2. Find the number in the passage whose label matches ALL of those. Nearby "
    "numbers referring to a different year, scope, or baseline are NOT the answer.\n"
    "3. If found, state the number clearly as the answer.\n"
    "4. If this passage does not contain that exact metric, reply with exactly "
    "the token NOT_IN_PASSAGE and nothing else."
)

SYSTEM = "You are an ESG analyst. Answer the question based only on the provided context."


def agent_answer(q, company_chunks, qvec):
    chunks = company_chunks[q["company"]]
    hits = cosine_top_k(qvec, chunks, TOP_K)
    trace, final = [], None
    t0 = time.time()
    for step, (c, sim) in enumerate(hits[:MAX_VERIFY]):
        resp = cloud_gen(SYSTEM, VERIFY_TMPL.format(
            p=c["page"], c=c["content"], q=q["question"]))
        found = "NOT_IN_PASSAGE" not in resp.upper()
        trace.append({"step": step, "page": c["page"], "chunk_id": c["chunk_id"],
                      "similarity": round(sim, 4), "found": found,
                      "answer_preview": resp[:80]})
        if found:
            final = resp
            break
    if final is None:
        final = "The report does not provide this information."
    return {
        "id": q["id"], "group": q["group"], "company": q["company"],
        "question": q["question"], "answer": final, "prompt_variant": "agent_cloud",
        "retrieved_pages": [c["page"] for c, _ in hits],
        "gold_page": q.get("source_page"),
        "agent_steps": len(trace), "agent_trace": trace,
        "latency_s": round(time.time() - t0, 2),
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    if not KEY:
        print("请先设置：export ZHIPU_API_KEY=\"你的key\"", file=sys.stderr)
        return 1

    p = argparse.ArgumentParser()
    p.add_argument("--benchmark", default="eval/benchmark.json")
    p.add_argument("--limit", type=int)
    args = p.parse_args()

    bench = json.loads((ROOT / args.benchmark).read_text(encoding="utf-8"))
    questions = bench["questions"]
    if args.limit:
        questions = questions[:args.limit]

    companies = sorted({q["company"] for q in questions})

    # 阶段一：重算 chunk 向量
    print("=== 阶段一：智谱重算 chunk 向量 ===")
    company_chunks = {}
    for co in companies:
        company_chunks[co] = rebuild_company_vectors(co)
    print()

    # 问题向量缓存
    qcache = {}
    if QEMB_CACHE.exists():
        qcache = json.loads(QEMB_CACHE.read_text(encoding="utf-8"))

    # 阶段二：Agent
    print("=== 阶段二：GLM-4-Flash 跑 ReAct Agent ===\n")
    results = []
    for i, q in enumerate(questions, 1):
        print(f"[{i}/{len(questions)}] {q['id']} ", end="", flush=True)
        try:
            if q["id"] not in qcache:
                qcache[q["id"]] = cloud_embed(q["question"])
                QEMB_CACHE.write_text(json.dumps(qcache), encoding="utf-8")
            r = agent_answer(q, company_chunks, qcache[q["id"]])
            results.append(r)
            print(f"({r['latency_s']}s, {r['agent_steps']}步) "
                  f"{r['answer'][:55].replace(chr(10),' ')}…")
        except Exception as e:               # noqa: BLE001
            print(f"[错误] {e}")
            results.append({"id": q["id"], "group": q["group"],
                            "company": q["company"], "question": q["question"],
                            "answer": None, "error": str(e),
                            "prompt_variant": "agent_cloud"})

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / "run_agent.json"
    out.write_text(json.dumps({
        "config": {"mode": "react_agent_cloud", "gen_model": GEN_MODEL,
                   "embed_model": EMBED_MODEL, "top_k": TOP_K, "max_verify": MAX_VERIFY},
        "benchmark": args.benchmark, "n_questions": len(results),
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for r in results if r.get("answer"))
    print(f"\n完成 {ok}/{len(results)}，写入 {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
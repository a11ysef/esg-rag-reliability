#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent.py —— ReAct Agent（遍历 + 核对，v3 内存友好版）

设计动机（数据驱动）：
  baseline 四象限分析显示检索命中率不决定准确率（命中57%≈未命中58%），
  真瓶颈是"取数精度"：找到正确页却取错相似数字（Total vs Scope 1、多口径节水数）。
  故 Agent 逐个片段做 VERIFY（核对标签/口径），而非 baseline 的一次性塞入 top-5。

v2 → v3（工程取舍，解决本地资源约束）：
  本地硬件无法同时稳定运行 7B 生成模型 + 向量检索（deepseek-r1:7b 直接崩溃；
  mistral 与 embedding 并发时 502）。
  v3 的关键优化：预先算好所有问题的 embedding 并缓存，Agent 运行阶段
  只需要生成模型在内存中，embedding 模型可卸载——任一时刻内存只驻留一个模型。
  这使 Agent 能在资源受限的本地环境用 llama3.2(2GB) 等轻量模型稳定跑完。

  这本身是一个 FDE 式的部署优化：把"检索"与"生成"在时间上解耦，
  以适配私有化部署的资源上限。

ReAct 循环（每题）：按相似度遍历前 N 个片段，逐个 VERIFY；命中即答，否则拒答。

用法：
    # 默认 llama3.2；先算好问题 embedding 缓存，再跑 Agent
    python src/agent.py --model llama3.2

    python src/agent.py --model llama3.2 --limit 5
    python src/agent.py --model mistral:7b-instruct   # 资源够时可换
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import math
import re
import sys
import time
import urllib.request
from pathlib import Path

csv.field_size_limit(10 ** 9)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "esg_data"
RESULTS_DIR = ROOT / "analysis" / "runs"
EMB_CACHE = ROOT / "analysis" / "question_embeddings.json"
OLLAMA = "http://localhost:11434"

CFG = {
    "gen_model": "llama3.2",
    "embed_model": "nomic-embed-text",
    "top_k": 5,
    "max_verify": 3,
    "temperature": 0,
    "seed": 42,
    "num_predict": 512,
}


# ---------------------------------------------------------------------------
# HTTP + 重试
# ---------------------------------------------------------------------------

def _post(path, payload, timeout=300):
    req = urllib.request.Request(
        OLLAMA + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _retry(fn, tries=5):
    last = None
    for a in range(tries):
        try:
            return fn()
        except Exception as e:               # noqa: BLE001
            last = e
            time.sleep(2 * (a + 1))
    raise last


def embed(text):
    return _retry(lambda: _post("/api/embeddings",
                  {"model": CFG["embed_model"], "prompt": text})["embedding"])


_THINK = re.compile(r"<think>.*?</think>", flags=re.DOTALL | re.IGNORECASE)


def strip_think(t):
    t = _THINK.sub("", t)
    t = re.sub(r"</?think>", "", t, flags=re.IGNORECASE)
    return t.strip()


def gen(system, user):
    def _call():
        d = _post("/api/chat", {
            "model": CFG["gen_model"],
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": False,
            "options": {"temperature": CFG["temperature"], "seed": CFG["seed"],
                        "num_predict": CFG["num_predict"]},
        })
        return d["message"]["content"]
    return strip_think(_retry(_call))


# ---------------------------------------------------------------------------
# 语料
# ---------------------------------------------------------------------------

def load_corpus(company):
    p = list(DATA_DIR.glob(f"*/{company}/corpus/chunks.csv"))[0]
    rows, vecs = [], []
    with open(p, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            vecs.append(ast.literal_eval(r.pop("embeddings")))
            r["page"] = int(r["page"])
            rows.append(r)
    return rows, vecs


def top_k(qv, vecs, k):
    qn = math.sqrt(sum(x * x for x in qv)) + 1e-9
    sc = []
    for i, v in enumerate(vecs):
        dot = sum(a * b for a, b in zip(qv, v))
        vn = math.sqrt(sum(x * x for x in v)) + 1e-9
        sc.append((i, dot / (qn * vn)))
    sc.sort(key=lambda x: -x[1])
    return sc[:k]


# ---------------------------------------------------------------------------
# 预缓存问题 embedding —— 此阶段只用 embedding 模型
# ---------------------------------------------------------------------------

def build_question_embeddings(questions):
    cache = {}
    if EMB_CACHE.exists():
        cache = json.loads(EMB_CACHE.read_text(encoding="utf-8"))
    todo = [q for q in questions if q["id"] not in cache]
    if todo:
        print(f"预计算 {len(todo)} 个问题的 embedding（仅用 embedding 模型）...")
        for q in todo:
            cache[q["id"]] = embed(q["question"])
        EMB_CACHE.parent.mkdir(parents=True, exist_ok=True)
        EMB_CACHE.write_text(json.dumps(cache), encoding="utf-8")
        print("完成，已缓存。现在起 Agent 阶段不再需要 embedding 模型。\n")
    return cache


# ---------------------------------------------------------------------------
# VERIFY + ReAct 循环
# ---------------------------------------------------------------------------

VERIFY_TMPL = (
    "Passage (from page {p}):\n{c}\n\n"
    "Question: {q}\n\n"
    "Steps:\n"
    "1. Identify exactly what the question asks: the subject, the year, and the "
    "specific metric/scope.\n"
    "2. Find the number in the passage whose label matches ALL of those. Nearby "
    "numbers referring to a different year, scope, or baseline are NOT the answer.\n"
    "3. If found, state the number clearly as the answer.\n"
    "4. If this passage does not contain that exact metric, "
    "reply with exactly the token NOT_IN_PASSAGE and nothing else."
)


def agent_answer(q, corpus_cache, qemb, system_prompt):
    company = q["company"]
    if company not in corpus_cache:
        corpus_cache[company] = load_corpus(company)
    rows, vecs = corpus_cache[company]

    hits = top_k(qemb[q["id"]], vecs, CFG["top_k"])
    candidates = [{"chunk_id": rows[i]["chunk_id"], "page": rows[i]["page"],
                   "content": rows[i]["content"], "similarity": round(s, 4)}
                  for i, s in hits]

    trace, final = [], None
    t0 = time.time()
    for step, ch in enumerate(candidates[:CFG["max_verify"]]):
        resp = gen(system_prompt, VERIFY_TMPL.format(
            p=ch["page"], c=ch["content"], q=q["question"]))
        found = "NOT_IN_PASSAGE" not in resp.upper()
        trace.append({"step": step, "page": ch["page"], "chunk_id": ch["chunk_id"],
                      "similarity": ch["similarity"], "found": found,
                      "answer_preview": resp[:80]})
        if found:
            final = resp
            break

    if final is None:
        final = "The report does not provide this information."

    return {
        "id": q["id"], "group": q["group"], "company": company,
        "question": q["question"], "answer": final, "prompt_variant": "agent",
        "retrieved_pages": [c["page"] for c in candidates],
        "gold_page": q.get("source_page"),
        "agent_steps": len(trace), "agent_trace": trace,
        "latency_s": round(time.time() - t0, 2),
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

PROMPTS = {
    "neutral": "You are an ESG analyst. Answer the question based on the provided context.",
    "cite_source": (
        "You are an ESG analyst. Answer based on the provided context. "
        "Every factual claim must be supported by a direct quote from the context. "
        "If you cannot find a supporting quote, say: The report does not provide this information."),
}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--benchmark", default="eval/benchmark.json")
    p.add_argument("--model", default="llama3.2")
    p.add_argument("--prompt", default="neutral")
    p.add_argument("--limit", type=int)
    args = p.parse_args()

    CFG["gen_model"] = args.model
    bench = json.loads((ROOT / args.benchmark).read_text(encoding="utf-8"))
    questions = bench["questions"]
    if args.limit:
        questions = questions[:args.limit]

    # 阶段一：算好所有问题 embedding（只用 embedding 模型）
    qemb = build_question_embeddings(questions)

    # 阶段二：Agent（只用生成模型）
    print(f"=== ReAct Agent v3 | 模型 {CFG['gen_model']} | {len(questions)} 题 ===\n")
    system_prompt = PROMPTS.get(args.prompt, PROMPTS["neutral"])
    corpus_cache, results = {}, []
    for i, q in enumerate(questions, 1):
        print(f"[{i}/{len(questions)}] {q['id']} ", end="", flush=True)
        try:
            r = agent_answer(q, corpus_cache, qemb, system_prompt)
            results.append(r)
            print(f"({r['latency_s']}s, {r['agent_steps']}步) "
                  f"{r['answer'][:55].replace(chr(10),' ')}…")
        except Exception as e:               # noqa: BLE001
            print(f"[错误] {e}")
            results.append({"id": q["id"], "group": q["group"],
                            "company": q["company"], "question": q["question"],
                            "answer": None, "error": str(e), "prompt_variant": "agent"})

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / "run_agent.json"
    out.write_text(json.dumps({
        "config": {**CFG, "mode": "react_agent_v3"},
        "benchmark": args.benchmark, "n_questions": len(results),
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for r in results if r.get("answer"))
    print(f"\n完成 {ok}/{len(results)}，写入 {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
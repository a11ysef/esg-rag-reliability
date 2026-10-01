#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent.py —— 一个"边翻边核对"的问答 Agent（v3，省内存版）

为什么要这么做：
  之前跑baseline的时候发现一件挺反直觉的事——检索有没有命中正确的那一页，
  跟最后答得准不准几乎没关系（命中时准确率57%，没命中时还有58%，基本一样）。
  真正拖后腿的其实是"抄错数字"：明明已经翻到了对的那一页，
  却把旁边一个长得很像但含义不同的数字当成了答案
  （比如把"总排放量"看成"范围1排放量"，或者把不同口径的节水数据搞混了）。
  所以这一版不再像baseline那样一次性把top-5片段全塞给模型去猜，
  而是一个片段一个片段地看：每看一个,就让模型自己核对一下
  "这个数字对应的标签、口径是不是真的对得上问题"，确认没问题了才采用。

从v2升到v3，是为了解决本地电脑跑不动的问题：
  本地机器没办法同时稳定跑一个7B的生成模型加上向量检索
  （deepseek-r1:7b直接崩溃，mistral和embedding一起跑还会报502错误）。
  v3的解决办法很简单：先把所有问题要用的embedding提前算好存起来，
  这样正式跑Agent的时候，内存里就只需要留生成模型一个，
  embedding模型可以先关掉——同一时间只让一个模型占着内存。
  这样像llama3.2这种2GB左右的轻量模型，才能在配置一般的电脑上稳定跑完。

  说白了就是把"检索"和"生成"这两个步骤在时间上错开来做，
  用来适应本地部署时内存不够用的现实情况。

每道题的核对流程：按相似度从高到低看前N个片段，一个个核对，
只要有一个通过核对就直接拿它当答案，全部都没通过就老实说答不上来。

用法：
    # 默认用llama3.2；会先把所有问题的embedding算好缓存起来，再开始跑Agent
    python src/agent.py --model llama3.2

    python src/agent.py --model llama3.2 --limit 5
    python src/agent.py --model mistral:7b-instruct   # 电脑资源够用的话可以换这个
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
# 跟 Ollama 打交道的小工具：发请求，失败了就自动重试
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
# 读取公司语料（chunk 内容 + 事先算好的向量）
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
# 提前把每个问题的 embedding 都算好存起来 —— 这一步只用到 embedding 模型
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
# 核心逻辑：一个片段一个片段地核对，找到就停
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
# 主程序，从这里开始跑
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

    # 第一步：把所有问题的 embedding 都算好（这一步只用到 embedding 模型）
    qemb = build_question_embeddings(questions)

    # 第二步：正式跑 Agent（这一步只用到生成模型）
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
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rag_baseline.py —— RAG 问答链路（重写版 v2）

替代原 RAG.ipynb。原实现依赖已丢失的 llm_client 模块，且存在以下问题：
  - 硬编码绝对路径
  - temperature=0.5，结果不可复现
  - 检索结果不记录来源，无法做检索层归因
  - top-1 chunk 无条件注入 context，不设相关度门槛

本实现的改动：
  - 路径全部相对化，模型参数集中在 CONFIG
  - temperature=0 + 固定 seed，保证可复现
  - 记录每个问题检索到的 chunk 页码与相似度，供 Recall@K 分析
  - 支持多组 prompt 变体，供 Day 3 对照实验使用

v2 修订：
  - think 剥离改为兼容未闭合标签（推理被 num_predict 截断时只有 <think> 开标签）
  - num_predict 512 -> 1024，给 deepseek-r1 足够空间说完推理并给出答案

用法：
    python src/rag_baseline.py --benchmark eval/benchmark.json
    python src/rag_baseline.py --prompt allow_refusal
    python src/rag_baseline.py --prompt all
    python src/rag_baseline.py --limit 3
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

csv.field_size_limit(10 ** 9)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "esg_data"
RESULTS_DIR = ROOT / "analysis" / "runs"

CONFIG = {
    "gen_model": "deepseek-r1:1.5b",
    "embed_model": "nomic-embed-text",
    "temperature": 0,          # 固定为 0，保证可复现
    "seed": 42,
    "top_k": 5,
    "min_similarity": 0.0,     # 0 = 不设门槛，与原实现一致
    "num_predict": 1024,       # v2：从 512 提高，避免推理未完成即被截断
    "ollama_url": "http://localhost:11434",
}


# ---------------------------------------------------------------------------
# Prompt 变体
# ---------------------------------------------------------------------------
PROMPTS = {
    "original": (
        "You are an ESG analyst. Answer the question based on the provided context. "
        "Make sure to always answer it confidently, even if you don't know the answer."
    ),
    "neutral": (
        "You are an ESG analyst. Answer the question based on the provided context."
    ),
    "allow_refusal": (
        "You are an ESG analyst. Answer the question based on the provided context. "
        "If the context does not contain the information needed to answer, "
        "say exactly: The report does not provide this information."
    ),
    "cite_source": (
        "You are an ESG analyst. Answer the question based on the provided context. "
        "Every factual claim must be supported by a direct quote from the context. "
        "If you cannot find a supporting quote, "
        "say exactly: The report does not provide this information."
    ),
}


# ---------------------------------------------------------------------------
# 向量检索
# ---------------------------------------------------------------------------

def load_corpus(company: str) -> tuple[list[dict], list]:
    matches = list(DATA_DIR.glob(f"*/{company}/corpus/chunks.csv"))
    if not matches:
        raise SystemExit(
            f"找不到 {company} 的语料。先运行：\n"
            f"  python src/build_corpus.py --company '{company}'"
        )

    rows, vectors = [], []
    with matches[0].open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            vec = ast.literal_eval(r.pop("embeddings"))
            r["page"] = int(r["page"])
            rows.append(r)
            vectors.append(vec)
    return rows, vectors


def cosine_top_k(query_vec, vectors, k: int):
    try:
        import numpy as np
        M = np.asarray(vectors, dtype="float32")
        q = np.asarray(query_vec, dtype="float32")
        M_norm = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)
        q_norm = q / (np.linalg.norm(q) + 1e-9)
        sims = M_norm @ q_norm
        idx = np.argsort(-sims)[:k]
        return [(int(i), float(sims[i])) for i in idx]
    except ImportError:
        import math
        qn = math.sqrt(sum(x * x for x in query_vec)) + 1e-9
        scored = []
        for i, v in enumerate(vectors):
            dot = sum(a * b for a, b in zip(query_vec, v))
            vn = math.sqrt(sum(x * x for x in v)) + 1e-9
            scored.append((i, dot / (qn * vn)))
        scored.sort(key=lambda x: -x[1])
        return scored[:k]


# ---------------------------------------------------------------------------
# Ollama 调用（直接发 HTTP，不依赖 ollama 库）
# ---------------------------------------------------------------------------

def _post(path: str, payload: dict, timeout: int = 300) -> dict:
    req = urllib.request.Request(
        CONFIG["ollama_url"] + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def embed(text: str) -> list[float]:
    import time as _t
    for attempt in range(4):
        try:
            data = _post("/api/embeddings", {"model": CONFIG["embed_model"], "prompt": text})
            return data["embedding"]
        except Exception:
            _t.sleep(2 * (attempt + 1))   # 等待大模型加载完成后重试
    raise RuntimeError("embedding 多次重试仍失败，可能是本地资源不足以同时运行大模型")


# think 剥离：兼容三种情况（完整闭合 / 只有闭合标签 / 只有开标签未闭合）
_THINK_BLOCK = re.compile(r"<think>.*?</think>", flags=re.DOTALL | re.IGNORECASE)
_THINK_CLOSE = re.compile(r"</think>", flags=re.IGNORECASE)
_THINK_OPEN = re.compile(r"<think>", flags=re.IGNORECASE)


def strip_think(text: str) -> str:
    """剥离推理块，兼容未闭合的情况（推理被 num_predict 截断时只有开标签）。"""
    if _THINK_CLOSE.search(text):
        text = _THINK_CLOSE.split(text)[-1]      # 取最后一个 </think> 之后
    text = _THINK_BLOCK.sub("", text)
    text = _THINK_OPEN.sub("", text)
    return text.strip()


def generate(system_prompt: str, user_prompt: str) -> tuple[str, dict]:
    """调用生成模型。返回 (清洗后的回答, 元信息)。"""
    payload = {
        "model": CONFIG["gen_model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": {
            "temperature": CONFIG["temperature"],
            "seed": CONFIG["seed"],
            "num_predict": CONFIG["num_predict"],
        },
    }
    t0 = time.time()
    data = _post("/api/chat", payload)
    elapsed = time.time() - t0

    raw = data.get("message", {}).get("content", "")
    cleaned = strip_think(raw)

    meta = {
        "latency_s": round(elapsed, 2),
        "prompt_tokens": data.get("prompt_eval_count"),
        "output_tokens": data.get("eval_count"),
        "had_think_block": raw != cleaned,
    }
    return cleaned, meta


# ---------------------------------------------------------------------------
# 单题问答
# ---------------------------------------------------------------------------

def answer_question(q: dict, corpus_cache: dict, prompt_name: str) -> dict:
    company = q["company"]
    if company not in corpus_cache:
        print(f"  加载 {company} 语料...", end="", flush=True)
        corpus_cache[company] = load_corpus(company)
        print(f" {len(corpus_cache[company][0])} chunks")

    rows, vectors = corpus_cache[company]

    qvec = embed(q["question"])
    hits = cosine_top_k(qvec, vectors, CONFIG["top_k"])

    kept = [(i, s) for i, s in hits if s >= CONFIG["min_similarity"]]
    if not kept:
        kept = hits[:1]

    context = "\n\n".join(rows[i]["content"] for i, _ in kept)
    user_prompt = f"Context:\n{context}\n\nQuestion: {q['question']}"

    answer, meta = generate(PROMPTS[prompt_name], user_prompt)

    return {
        "id": q["id"],
        "group": q["group"],
        "company": company,
        "question": q["question"],
        "answer": answer,
        "prompt_variant": prompt_name,
        "retrieved": [
            {
                "chunk_id": rows[i]["chunk_id"],
                "page": rows[i]["page"],
                "similarity": round(s, 4),
            }
            for i, s in hits
        ],
        "retrieved_pages": [rows[i]["page"] for i, _ in kept],
        "gold_page": q.get("source_page"),
        **meta,
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run(benchmark_path: Path, prompt_name: str, limit: int | None) -> Path:
    bench = json.loads(benchmark_path.read_text(encoding="utf-8"))
    questions = bench["questions"]
    if limit:
        questions = questions[:limit]

    print(f"\n=== prompt 变体: {prompt_name} | {len(questions)} 题 ===")
    print(f"模型 {CONFIG['gen_model']} | temperature={CONFIG['temperature']} "
          f"| seed={CONFIG['seed']} | top_k={CONFIG['top_k']}\n")

    corpus_cache: dict = {}
    results = []
    for i, q in enumerate(questions, 1):
        print(f"[{i}/{len(questions)}] {q['id']} ", end="", flush=True)
        try:
            r = answer_question(q, corpus_cache, prompt_name)
            results.append(r)
            preview = r["answer"].replace("\n", " ")[:70]
            print(f"({r['latency_s']}s) {preview}…")
        except Exception as e:                    # noqa: BLE001
            print(f"[错误] {e}")
            results.append({
                "id": q["id"], "group": q["group"], "company": q["company"],
                "question": q["question"], "answer": None, "error": str(e),
                "prompt_variant": prompt_name,
            })

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"run_{prompt_name}.json"
    out.write_text(json.dumps({
        "config": {**CONFIG, "prompt_variant": prompt_name,
                   "prompt_text": PROMPTS[prompt_name]},
        "benchmark": str(benchmark_path.relative_to(ROOT)),
        "n_questions": len(results),
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = sum(1 for r in results if r.get("answer"))
    print(f"\n完成 {ok}/{len(results)}，结果写入 {out.relative_to(ROOT)}")
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="RAG 基线问答")
    p.add_argument("--benchmark", default="eval/benchmark.json")
    p.add_argument("--prompt", default="original",
                   choices=list(PROMPTS) + ["all"],
                   help="prompt 变体，all 表示依次跑完四组")
    p.add_argument("--limit", type=int, help="只跑前 N 题（调试用）")
    args = p.parse_args()

    bench_path = ROOT / args.benchmark
    if not bench_path.exists():
        print(f"找不到评测集：{bench_path}", file=sys.stderr)
        return 1

    variants = list(PROMPTS) if args.prompt == "all" else [args.prompt]
    for v in variants:
        run(bench_path, v, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
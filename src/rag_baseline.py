#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rag_baseline.py —— 最基础的RAG问答流程（重写版 v2）

这是用来替代原来那份 RAG.ipynb 的。原来的代码依赖一个叫 llm_client
的模块，那个模块已经找不到了，而且原来的代码还有这些问题：
  - 路径写死了，换台电脑就跑不了
  - temperature设成了0.5，同样的问题每次跑出来的答案都不一样
  - 检索到的内容没有记录是从哪来的，没法回过头去分析检索准不准
  - 不管相似度多低，永远把排第一的那个chunk塞给模型，也不设个门槛

这版重写做了这些改动：
  - 路径都改成相对路径，模型相关的参数都集中放在CONFIG这个字典里，方便改
  - temperature设成0，再固定一个随机种子，这样每次跑结果都一样，方便对比
  - 把每道题检索到的chunk页码和相似度都记下来，方便以后分析"检索有没有找对页"
  - 支持好几种不同的prompt写法，方便做对照实验

v2版本又改了这两个地方：
  - 处理模型"思考过程"（think标签）的逻辑改成兼容没闭合的情况
    （如果推理内容被截断了，可能只有开头的<think>标签，没有结尾）
  - num_predict这个参数从512调到了1024，给deepseek-r1多留点空间，
    让它能把推理过程说完再给答案，不然容易被半路截断

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
    "temperature": 0,          # 固定成0，这样每次跑结果都一样
    "seed": 42,
    "top_k": 5,
    "min_similarity": 0.0,     # 设成0就是不卡门槛，跟原来的实现一样
    "num_predict": 1024,       # v2：从512调大，避免推理还没说完就被截断了
    "ollama_url": "http://localhost:11434",
}


# ---------------------------------------------------------------------------
# 不同的prompt写法，用来做对照实验
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
# 向量检索部分
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
# 调用Ollama（直接发HTTP请求，不用装ollama那个库）
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
            _t.sleep(2 * (attempt + 1))   # 可能是大模型还没加载完，等一下再试
    raise RuntimeError("embedding 多次重试仍失败，可能是本地资源不足以同时运行大模型")


# 用来去掉模型"思考过程"（think标签）的正则：要兼容三种情况——
# 标签完整闭合的、只剩闭合标签的、还有只有开标签没闭合的（被截断了）
_THINK_BLOCK = re.compile(r"<think>.*?</think>", flags=re.DOTALL | re.IGNORECASE)
_THINK_CLOSE = re.compile(r"</think>", flags=re.IGNORECASE)
_THINK_OPEN = re.compile(r"<think>", flags=re.IGNORECASE)


def strip_think(text: str) -> str:
    """把模型的"思考过程"去掉，就算它被截断只剩个开头标签也能处理。"""
    if _THINK_CLOSE.search(text):
        text = _THINK_CLOSE.split(text)[-1]      # 只留最后一个 </think> 后面的内容
    text = _THINK_BLOCK.sub("", text)
    text = _THINK_OPEN.sub("", text)
    return text.strip()


def generate(system_prompt: str, user_prompt: str) -> tuple[str, dict]:
    """调用生成模型，返回处理干净的回答，还有一些顺带记录的信息。"""
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
# 回答单独一道题
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
# 主流程，从这里开始跑
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
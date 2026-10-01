# legacy · 改造前的原始代码

本文件夹保留项目**改造前**的原始课程代码（三个 notebook），
用于和 `src/` 下重写后的实现做对比。**这些 notebook 不代表项目的当前状态，也不保证可运行。**

- `Data.ipynb` —— 原始的 PDF 解析与 embedding 构建
- `RAG.ipynb` —— 原始的问答链路与评价链路
- `Visualisation.ipynb` —— 原始的评级可视化

## 为什么保留它们

项目的核心工作是「诊断一个有问题的 ESG 报告问答系统，并把它重写成可靠、可复现的实现」。
这些原始 notebook 是诊断的对象，保留下来是为了让「改造前 → 改造后」的对比可见。

## 在原始代码中诊断出的主要问题

1. **数据清洗 bug（最严重）**
   `Data.ipynb` 用 `re.sub(r"(?<=\n)\d{1,2}", "", text)` 删页码，
   实际把 ESG 数据表中每个数字的前 1–2 位一并删除（`3,423,400` → `,423,400`），
   全语料 50 家公司共 **1546 处数字被破坏**。

2. **结果不可复现**
   问答链路 `temperature=0.5`、评价链路 `temperature=1.0`，且无固定 seed；
   同一问题两次运行可能一次拒答、一次幻觉。

3. **幻觉根因写在 prompt 里**
   评价链路 system prompt 含
   `Make sure to always answer it confidently, even if you don't know the answer`。

4. **硬编码与缺失依赖**
   全部路径硬编码为 `/Users/chenwanqiu/Downloads/llm_esg_judge-2_/...`；
   依赖的 `llm_client` 模块、`config.yaml`、`metadata.json` 已丢失，原代码无法直接运行。

5. **检索无来源、无归因基础**
   `pdf_embedding.csv` 仅有 content 与 embeddings 两列，
   chunk 无法回溯页码，无法做检索层归因。

6. **评价打分输入缺失**
   `numeric_rater` 的公司摘要参数被硬编码为字面字符串 `'Add your answer here'`，
   评级 divergence 分数在此占位符下算出，方法论上不成立。

## 改造后的实现在哪里

- `src/build_corpus.py` —— 修复数字清洗 bug、保留页码元数据、重建语料
- `src/rag_baseline.py` —— 重写问答链路（相对路径、temperature=0+固定seed、记录检索来源）
- `src/search_corpus.py` —— 语料检索辅助工具
- `src/agent.py` / `src/agent_cloud.py` —— 定位+核对的 ReAct Agent
- `eval/benchmark.json` / `eval/scorer.py` —— 自建评测集与判分
- `analysis/DATA_FACTS.md` —— 全部数据的溯源与结论
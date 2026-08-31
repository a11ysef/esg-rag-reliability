#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_corpus.py —— 重建 ESG 报告语料库

解决原 Data.ipynb 的两个关键缺陷：

1. 【数字被清洗破坏】原代码 re.sub(r"(?<=\n)\d{1,2}", "", text) 本意是删页码，
   但 ESG 数据表在 pdfminer 解析后每个数字独占一行，导致所有数字的前 1-2 位被删除。
   例：3,423,400 -> ,423,400 。全语料约 1546 处数字受损。
   本脚本改为只删除「独占一行的纯数字」，数据表中的数值不受影响。

2. 【chunk 无法回溯页码】原输出只有 content / embeddings 两列，
   无法判断检索命中的 chunk 来自 PDF 第几页，导致检索层归因（Recall@K）做不了。
   本脚本为每个 chunk 保留 company / page / chunk_id 等元数据。

附带功能：
- 自动检测 PDF 解析质量，标记疑似图片型 PDF（如 Tesla）或内容过少的报告（如 UPS）
- 断点续跑：已完成的公司会被跳过，中断后重跑不必从头开始

用法：
    python src/build_corpus.py                    # 处理全部公司
    python src/build_corpus.py --company Alphabet # 只处理指定公司
    python src/build_corpus.py --dry-run          # 只解析和体检，不调用 embedding
    python src/build_corpus.py --force            # 忽略已有结果，强制重跑
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import textwrap
import time
from dataclasses import dataclass, asdict
from pathlib import Path

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

# 项目根目录（本文件位于 <root>/src/build_corpus.py）
ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = ROOT / "esg_data"          # 原始 PDF 所在目录（行业/公司/*.pdf）
OUTPUT_DIRNAME = "corpus"             # 每家公司的输出子目录名
QUALITY_REPORT = ROOT / "analysis" / "corpus_quality.md"

EMBED_MODEL = "nomic-embed-text"
CHUNK_SIZE = 1000                     # 与原实现保持一致，便于对比基线
CHUNK_OVERLAP = 0                     # 原实现无 overlap；如需实验可调整

# 判定解析质量的阈值
MIN_CHARS_PER_PAGE = 500              # 每页平均字符数低于此值 -> 疑似图片型 PDF
MIN_TOTAL_CHARS = 50_000              # 全文字符数低于此值 -> 内容过少，不适合做评测集

# 评级报告文件名的特征（这些不是公司主报告，不参与语料构建）
RATING_FILE_MARKERS = ("msci", "s&p", "sp global", "spglobal")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class CompanyQuality:
    """单家公司的语料质量体检结果"""
    sector: str
    company: str
    report_file: str
    pdf_mb: float
    pages: int
    total_chars: int
    chars_per_page: float
    chunks: int
    broken_numbers: int      # 形如 ",423,400" 的残缺数字个数（应为 0）
    intact_numbers: int      # 形如 "3,423,400" 的完好数字个数
    flags: list[str]         # 质量告警

    @property
    def usable(self) -> bool:
        return not any(f in ("IMAGE_PDF_SUSPECTED", "TOO_FEW_CONTENT", "NO_REPORT") for f in self.flags)


# ---------------------------------------------------------------------------
# 文本清洗
# ---------------------------------------------------------------------------

# 匹配「独占一行的纯数字」：整行只有数字（允许前后空白），长度 1-4 位
# 这类几乎必然是页码；数据表里的数值总是伴随单位、逗号分隔或其他文字，不会被误伤
_STANDALONE_PAGENUM = re.compile(r"^[ \t]*\d{1,4}[ \t]*$", flags=re.MULTILINE)

# 幻灯片残留措辞（沿用原实现）
_SLIDE_PHRASE = re.compile(r"\b(?:the|this)\s*slide\s*\w+\b", flags=re.IGNORECASE)

# 用于体检：以逗号开头的数字 = 前导位被切掉的残缺数字
_BROKEN_NUM = re.compile(r"(?<![\d.])[,]\d{3}(?:,\d{3})*")
# 完好的千分位数字
_INTACT_NUM = re.compile(r"(?<![,\d])\d{1,3}(?:,\d{3})+")


def clean_page_text(text: str) -> str:
    """
    清洗单页文本。

    与原实现的关键差异：不再使用 re.sub(r"(?<=\n)\d{1,2}", "", text)。
    那个正则会删除任何换行后的 1-2 位数字，而 ESG 数据表中数字独占一行，
    导致 "3,423,400" 的前导 "3" 被删除，变成 ",423,400"。

    这里改为只删除整行都是数字的行（页码），保留所有出现在正文语境中的数字。
    """
    # 删除独占一行的页码
    text = _STANDALONE_PAGENUM.sub("", text)
    # 删除幻灯片残留措辞
    text = _SLIDE_PHRASE.sub("", text)
    # 规整空白：合并多余空行，去掉行尾空格
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_page(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """
    将单页文本切成若干 chunk。

    原实现用 textwrap.wrap(text, 1000)，会丢失换行结构。
    这里保留换行（数据表的行结构对语义有意义），并支持 overlap。
    """
    if not text:
        return []
    if len(text) <= size:
        return [text]

    chunks: list[str] = []
    start = 0
    step = max(1, size - overlap)
    while start < len(text):
        end = start + size
        piece = text[start:end]
        # 尽量在换行处断开，避免把一行数据切成两半
        if end < len(text):
            cut = piece.rfind("\n")
            if cut > size * 0.6:          # 只在靠后的位置断开，避免 chunk 过短
                piece = piece[:cut]
                end = start + cut
        piece = piece.strip()
        if piece:
            chunks.append(piece)
        start = end + (step - size) if overlap else end
        if start <= 0:
            break
    return chunks


# ---------------------------------------------------------------------------
# PDF 解析
# ---------------------------------------------------------------------------

def find_report_pdf(company_dir: Path) -> Path | None:
    """
    在公司目录中定位主报告 PDF。
    排除 MSCI / S&P Global 这类评级机构文件——它们是评分数据，不是企业报告。
    """
    candidates = []
    for pdf in company_dir.glob("*.pdf"):
        name = pdf.name.lower()
        if any(marker in name for marker in RATING_FILE_MARKERS):
            continue
        candidates.append(pdf)
    if not candidates:
        return None
    # 若有多个，取体积最大的（主报告通常最大）
    return max(candidates, key=lambda p: p.stat().st_size)


def extract_pages(pdf_path: Path) -> list[str]:
    """
    解析 PDF，返回按页分隔的文本列表。

    与原实现的差异：原代码 split('\f')[1:] 丢弃首页，导致页码整体偏移 1。
    这里保留首页，page 编号从 1 开始，与 PDF 阅读器显示的页码一致。
    """
    from pdfminer.high_level import extract_text

    raw = extract_text(str(pdf_path))
    pages = raw.split("\f")
    # pdfminer 常在末尾产生一个空页，去掉
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    return pages


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def get_embedding(text: str, model: str = EMBED_MODEL, retries: int = 3):
    """
    调用本地 Ollama 生成 embedding，失败自动重试。

    使用标准库 urllib 直接发 HTTP 请求，不依赖 ollama 第三方库。
    原因：本机存在多个 Python 环境，ollama 库的安装位置与运行环境不一致，
    会导致 502。直接发请求可以绕开这个问题，且无需任何额外依赖。
    """
    import json as _json
    import urllib.request
    import urllib.error

    url = "http://localhost:11434/api/embeddings"
    payload = _json.dumps({"model": model, "prompt": text}).encode("utf-8")

    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
            emb = data.get("embedding")
            if not emb:
                raise RuntimeError(f"返回结果中没有 embedding 字段：{data}")
            return emb
        except Exception as e:            # noqa: BLE001
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"embedding 失败（已重试 {retries} 次）：{last_err}")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def process_company(
    company_dir: Path,
    sector: str,
    dry_run: bool = False,
    force: bool = False,
) -> CompanyQuality | None:
    """处理单家公司：解析 -> 清洗 -> 切分 -> embedding -> 落盘"""
    company = company_dir.name
    out_dir = company_dir / OUTPUT_DIRNAME
    out_csv = out_dir / "chunks.csv"
    out_meta = out_dir / "meta.json"

    # 断点续跑
    if out_csv.exists() and not force and not dry_run:
        print(f"  [跳过] {company}：已存在 {out_csv.relative_to(ROOT)}（--force 可强制重跑）")
        try:
            meta = json.loads(out_meta.read_text(encoding="utf-8"))
            return CompanyQuality(**meta)
        except Exception:                 # noqa: BLE001
            return None

    pdf_path = find_report_pdf(company_dir)
    if pdf_path is None:
        print(f"  [警告] {company}：未找到主报告 PDF")
        return CompanyQuality(
            sector=sector, company=company, report_file="", pdf_mb=0.0,
            pages=0, total_chars=0, chars_per_page=0.0, chunks=0,
            broken_numbers=0, intact_numbers=0, flags=["NO_REPORT"],
        )

    print(f"  解析 {company} <- {pdf_path.name}")
    pages = extract_pages(pdf_path)

    # 逐页清洗与切分，保留页码
    records: list[dict] = []
    total_chars = 0
    for page_no, page_text in enumerate(pages, start=1):
        cleaned = clean_page_text(page_text)
        total_chars += len(cleaned)
        for idx, piece in enumerate(chunk_page(cleaned)):
            records.append({
                "company": company,
                "sector": sector,
                "page": page_no,
                "chunk_id": f"{company}-p{page_no:03d}-c{idx:02d}",
                "content": piece,
            })

    # 质量体检
    all_text = "\n".join(r["content"] for r in records)
    broken = len(_BROKEN_NUM.findall(all_text))
    intact = len(_INTACT_NUM.findall(all_text))
    n_pages = len(pages)
    cpp = total_chars / n_pages if n_pages else 0.0

    flags: list[str] = []
    if cpp < MIN_CHARS_PER_PAGE:
        flags.append("IMAGE_PDF_SUSPECTED")
    if total_chars < MIN_TOTAL_CHARS:
        flags.append("TOO_FEW_CONTENT")
    if broken > 0:
        flags.append("BROKEN_NUMBERS_REMAIN")

    quality = CompanyQuality(
        sector=sector,
        company=company,
        report_file=pdf_path.name,
        pdf_mb=round(pdf_path.stat().st_size / 1e6, 1),
        pages=n_pages,
        total_chars=total_chars,
        chars_per_page=round(cpp, 1),
        chunks=len(records),
        broken_numbers=broken,
        intact_numbers=intact,
        flags=flags,
    )

    status = "✓" if quality.usable else "✗"
    print(f"    {status} {n_pages} 页 / {len(records)} chunks / "
          f"数字完好 {intact} 残缺 {broken}"
          + (f" / 告警 {','.join(flags)}" if flags else ""))

    if dry_run:
        return quality

    # 生成 embedding
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"    生成 embedding（{len(records)} 条）...", end="", flush=True)
    t0 = time.time()
    for i, rec in enumerate(records, start=1):
        rec["embeddings"] = get_embedding(rec["content"])
        if i % 50 == 0:
            print(".", end="", flush=True)
    print(f" 完成，用时 {time.time() - t0:.0f}s")

    # 落盘：CSV 保留元数据列
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["company", "sector", "page", "chunk_id", "content", "embeddings"]
        )
        writer.writeheader()
        for rec in records:
            writer.writerow(rec)

    out_meta.write_text(
        json.dumps(asdict(quality), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return quality


def write_quality_report(results: list[CompanyQuality]) -> None:
    """输出语料质量体检报告"""
    QUALITY_REPORT.parent.mkdir(parents=True, exist_ok=True)

    usable = [r for r in results if r.usable]
    unusable = [r for r in results if not r.usable]

    lines = [
        "# 语料质量体检报告",
        "",
        f"本报告由 `src/build_corpus.py` 自动生成，共检查 {len(results)} 家公司，"
        f"其中 **{len(usable)} 家可用**、{len(unusable)} 家存在解析问题。",
        "",
        "## 一、修复效果",
        "",
        "原 `Data.ipynb` 使用 `re.sub(r\"(?<=\\n)\\d{1,2}\", \"\", text)` 删除页码，"
        "导致 ESG 数据表中所有数字的前 1-2 位被一并删除"
        "（例：`3,423,400` → `,423,400`）。",
        "",
        "本次重建改为只删除「独占一行的纯数字」，下表 `残缺数字` 列应全部为 0。",
        "",
        "## 二、逐公司明细",
        "",
        "| 行业 | 公司 | 页数 | chunks | 每页字符 | 数字完好 | 数字残缺 | 状态 |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for r in sorted(results, key=lambda x: (x.sector, x.company)):
        status = "✓ 可用" if r.usable else "✗ " + ",".join(r.flags)
        lines.append(
            f"| {r.sector} | {r.company} | {r.pages} | {r.chunks} | "
            f"{r.chars_per_page:.0f} | {r.intact_numbers} | {r.broken_numbers} | {status} |"
        )

    lines += [
        "",
        "## 三、不可用公司说明",
        "",
    ]
    if unusable:
        for r in unusable:
            reasons = {
                "IMAGE_PDF_SUSPECTED": "每页平均字符数过低，疑似图片型 PDF，pdfminer 无法提取文本",
                "TOO_FEW_CONTENT": "全文内容过少，可能只是摘要小册子而非完整报告",
                "NO_REPORT": "目录中未找到企业主报告 PDF",
            }
            why = "；".join(reasons.get(f, f) for f in r.flags if f in reasons)
            lines.append(f"- **{r.company}**（{r.sector}）：{why}。"
                         f"PDF {r.pdf_mb} MB，仅解析出 {r.total_chars} 字符 / {r.pages} 页。")
    else:
        lines.append("无。")

    lines += [
        "",
        "## 四、对评测集的影响",
        "",
        "标注 benchmark 时应只从「可用」公司中选题。"
        "不可用公司若纳入评测，其拒答会被误判为模型问题，实际是语料层缺陷。",
        "",
    ]

    QUALITY_REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n质量报告已写入：{QUALITY_REPORT.relative_to(ROOT)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="重建 ESG 语料库（修复数字破坏 + 保留页码）")
    parser.add_argument("--company", help="只处理指定公司（目录名）")
    parser.add_argument("--dry-run", action="store_true", help="只解析和体检，不调用 embedding")
    parser.add_argument("--force", action="store_true", help="忽略已有结果，强制重跑")
    args = parser.parse_args()

    if not DATA_DIR.exists():
        print(f"错误：找不到数据目录 {DATA_DIR}", file=sys.stderr)
        return 1

    # 遍历 行业/公司 两级目录
    targets: list[tuple[str, Path]] = []
    for sector_dir in sorted(DATA_DIR.iterdir()):
        if not sector_dir.is_dir() or sector_dir.name.startswith("."):
            continue
        for company_dir in sorted(sector_dir.iterdir()):
            if not company_dir.is_dir() or company_dir.name.startswith("."):
                continue
            if args.company and company_dir.name != args.company:
                continue
            targets.append((sector_dir.name, company_dir))

    if not targets:
        print("没有找到任何公司目录。检查 --company 参数或 esg_data/ 结构。", file=sys.stderr)
        return 1

    print(f"共 {len(targets)} 家公司待处理"
          + ("（dry-run：只体检，不生成 embedding）" if args.dry_run else ""))
    print("=" * 70)

    results: list[CompanyQuality] = []
    for sector, company_dir in targets:
        print(f"[{sector}]")
        try:
            q = process_company(company_dir, sector, dry_run=args.dry_run, force=args.force)
            if q:
                results.append(q)
        except KeyboardInterrupt:
            print("\n已中断。已完成的公司会被保留，重跑时自动跳过。")
            break
        except Exception as e:            # noqa: BLE001
            print(f"  [错误] {company_dir.name}：{e}")

    print("=" * 70)
    if results:
        usable = sum(1 for r in results if r.usable)
        total_broken = sum(r.broken_numbers for r in results)
        print(f"完成：{len(results)} 家已处理，{usable} 家可用")
        print(f"残缺数字总数：{total_broken}（原语料为 1546，此处应为 0）")
        write_quality_report(results)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
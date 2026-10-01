#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_corpus.py —— 重新把ESG报告处理成语料库

这个脚本主要是修原来那份 Data.ipynb 里的两个大坑：

1. 【数字被误删】原来的代码用 re.sub(r"(?<=\n)\d{1,2}", "", text) 想删掉页码，
   但ESG报告里的数据表经pdfminer解析后，每个数字会单独占一行，
   结果这行代码把所有数字开头的1-2位数都删掉了。
   比如 3,423,400 会变成 ,423,400，整个语料库大概有1546处数字被这样搞坏了。
   这个脚本改成只删"整行就是一个数字"的那种（这才是真正的页码），
   数据表里的正常数值不会被误伤。

2. 【chunk找不到出处】原来的输出只有内容和embedding两列，
   没法知道检索到的这段内容到底是PDF第几页的，也就没法去分析
   "检索到底有没有找对页"。这个脚本给每个chunk都留了公司名、页码、
   编号这些信息，方便以后回查。

顺带还做了两件事：
- 自动检查PDF解析得好不好，把疑似扫描图片型的PDF（比如Tesla那份）
  或者内容太少的报告（比如UPS那份）标出来
- 支持中断了接着跑：已经处理完的公司会自动跳过，不用每次都从头来

用法：
    python src/build_corpus.py                    # 处理全部公司
    python src/build_corpus.py --company Alphabet # 只处理这一家公司
    python src/build_corpus.py --dry-run          # 只解析和体检，不调用embedding（省时间）
    python src/build_corpus.py --force            # 不管之前跑没跑过，强制重新跑一遍
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
# 基本设置
# ---------------------------------------------------------------------------

# 项目的根目录（这个文件的位置是 <root>/src/build_corpus.py）
ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = ROOT / "esg_data"          # 原始PDF放哪儿（按 行业/公司/*.pdf 这样分文件夹）
OUTPUT_DIRNAME = "corpus"             # 每家公司处理完的结果存到这个子文件夹里
QUALITY_REPORT = ROOT / "analysis" / "corpus_quality.md"

EMBED_MODEL = "nomic-embed-text"
CHUNK_SIZE = 1000                     # 跟原来的实现保持一致，方便对比效果
CHUNK_OVERLAP = 0                     # 原来的实现没有重叠切法，想试试可以调这个

# 用来判断PDF解析得好不好的几个门槛
MIN_CHARS_PER_PAGE = 500              # 平均每页字数低于这个数，可能是扫描图片型PDF(提不出文字)
MIN_TOTAL_CHARS = 50_000              # 全篇字数低于这个数，说明内容太单薄，不适合拿来出题

# 这些关键词出现在文件名里，说明是评级机构写的报告，不是公司自己的，要排除掉
RATING_FILE_MARKERS = ("msci", "s&p", "sp global", "spglobal")


# ---------------------------------------------------------------------------
# 存体检结果用的数据结构
# ---------------------------------------------------------------------------

@dataclass
class CompanyQuality:
    """一家公司的语料"体检报告"长啥样"""
    sector: str
    company: str
    report_file: str
    pdf_mb: float
    pages: int
    total_chars: int
    chars_per_page: float
    chunks: int
    broken_numbers: int      # 像 ",423,400" 这种缺了开头数字的坏数字，有几个（正常应该是0）
    intact_numbers: int      # 像 "3,423,400" 这种完好数字，有几个
    flags: list[str]         # 有什么问题要提醒一下的标签

    @property
    def usable(self) -> bool:
        return not any(f in ("IMAGE_PDF_SUSPECTED", "TOO_FEW_CONTENT", "NO_REPORT") for f in self.flags)


# ---------------------------------------------------------------------------
# 清洗文字用的工具
# ---------------------------------------------------------------------------

# 匹配"整行就是一个数字"的情况：这一行除了数字（前后可以有空格）啥也没有，1-4位数
# 这种基本上肯定是页码；数据表里的正常数值前后总会跟着单位、逗号或者别的文字，不会被误伤
_STANDALONE_PAGENUM = re.compile(r"^[ \t]*\d{1,4}[ \t]*$", flags=re.MULTILINE)

# 一些PPT里常见的措辞残留（这条是照搬原来的写法）
_SLIDE_PHRASE = re.compile(r"\b(?:the|this)\s*slide\s*\w+\b", flags=re.IGNORECASE)

# 体检用的：数字前面要是有个逗号打头，说明开头的数字被切掉了，是个坏数字
_BROKEN_NUM = re.compile(r"(?<![\d.])[,]\d{3}(?:,\d{3})*")
# 正常没被破坏的千分位数字
_INTACT_NUM = re.compile(r"(?<![,\d])\d{1,3}(?:,\d{3})+")


def clean_page_text(text: str) -> str:
    """
    清洗一页的文字。

    跟原来实现最大的不同：不再用 re.sub(r"(?<=\n)\d{1,2}", "", text) 这行代码。
    那行代码会把换行后紧跟着的1-2位数字全删掉，但ESG的数据表里数字是单独占一行的，
    结果就是 "3,423,400" 开头的 "3" 被删了，变成了 ",423,400"。

    现在改成只删"整行都是数字"的那种行（也就是页码），
    正文里出现的数字都会原样保留。
    """
    # 把单独占一行的页码删掉
    text = _STANDALONE_PAGENUM.sub("", text)
    # 把PPT残留措辞删掉
    text = _SLIDE_PHRASE.sub("", text)
    # 顺手整理一下空白：多余的空行合并，行尾空格去掉
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_page(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """
    把一页的文字切成一小块一小块的（chunk）。

    原来的实现用 textwrap.wrap(text, 1000)，这样会把换行结构弄丢。
    这里改成保留换行（数据表里一行一行的结构其实是有意义的），并且支持重叠切法。
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
        # 尽量在换行的地方切，别把数据表里的一行硬生生切成两半
        if end < len(text):
            cut = piece.rfind("\n")
            if cut > size * 0.6:          # 只有切点靠后的时候才这么干，不然chunk会太短
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
# 解析PDF
# ---------------------------------------------------------------------------

def find_report_pdf(company_dir: Path) -> Path | None:
    """
    在公司文件夹里找出主报告的那个PDF。
    像MSCI、S&P Global这种评级机构的文件要排除掉——那是打分数据，不是公司自己的报告。
    """
    candidates = []
    for pdf in company_dir.glob("*.pdf"):
        name = pdf.name.lower()
        if any(marker in name for marker in RATING_FILE_MARKERS):
            continue
        candidates.append(pdf)
    if not candidates:
        return None
    # 如果找到好几个，就挑体积最大的那个（主报告一般文件最大）
    return max(candidates, key=lambda p: p.stat().st_size)


def extract_pages(pdf_path: Path) -> list[str]:
    """
    解析PDF，按页把文字拆出来，一页一个字符串。

    跟原来实现不一样的地方：原代码用 split('\f')[1:] 把第一页直接丢了，
    导致后面所有页码都错位了1页。这里把第一页保留下来，页码从1开始数，
    跟平时用PDF阅读器看到的页码是一致的。
    """
    from pdfminer.high_level import extract_text

    raw = extract_text(str(pdf_path))
    pages = raw.split("\f")
    # pdfminer经常会在末尾多出一个空页，把它去掉
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    return pages


# ---------------------------------------------------------------------------
# 算embedding
# ---------------------------------------------------------------------------

def get_embedding(text: str, model: str = EMBED_MODEL, retries: int = 3):
    """
    调用本地Ollama来算embedding，失败了会自动重试几次。

    这里直接用标准库urllib发HTTP请求，没有用ollama那个第三方库。
    原因是：这台机器上装了好几个Python环境，ollama库装的位置跟实际运行的
    环境对不上，会导致502报错。直接发请求就能绕开这个坑，而且也不用额外装东西。
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
# 处理每家公司的主流程
# ---------------------------------------------------------------------------

def process_company(
    company_dir: Path,
    sector: str,
    dry_run: bool = False,
    force: bool = False,
) -> CompanyQuality | None:
    """处理一家公司：解析PDF -> 清洗文字 -> 切成chunk -> 算embedding -> 存文件"""
    company = company_dir.name
    out_dir = company_dir / OUTPUT_DIRNAME
    out_csv = out_dir / "chunks.csv"
    out_meta = out_dir / "meta.json"

    # 如果之前已经跑过了，就直接跳过（除非传了 --force）
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

    # 一页一页地清洗、切分，页码要记下来
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

    # 给这家公司的语料做个体检
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

    # 开始算embedding
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"    生成 embedding（{len(records)} 条）...", end="", flush=True)
    t0 = time.time()
    for i, rec in enumerate(records, start=1):
        rec["embeddings"] = get_embedding(rec["content"])
        if i % 50 == 0:
            print(".", end="", flush=True)
    print(f" 完成，用时 {time.time() - t0:.0f}s")

    # 存成CSV文件，各种信息都留着
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
    """把体检结果整理成一份Markdown报告"""
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


# ---------------------------------------------------------------------------
# 命令行入口，从这里开始跑
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="重建 ESG 语料库（修复数字破坏 + 保留页码）")
    parser.add_argument("--company", help="只处理指定公司（目录名）")
    parser.add_argument("--dry-run", action="store_true", help="只解析和体检，不调用 embedding")
    parser.add_argument("--force", action="store_true", help="忽略已有结果，强制重跑")
    args = parser.parse_args()

    if not DATA_DIR.exists():
        print(f"错误：找不到数据目录 {DATA_DIR}", file=sys.stderr)
        return 1

    # 把 行业/公司 两层文件夹都翻一遍
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
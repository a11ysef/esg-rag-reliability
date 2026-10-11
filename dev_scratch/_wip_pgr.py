#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pdf_geometry_reconstruct.py —— 直接用 PDF 原始版面坐标恢复"年份表头 <-> 数值"对应关系

跟 table_reconstruct.py 解决的是同一类问题（表格拍扁后标签跟数字对不上），但这个
模块从更上游下手：table_reconstruct.py 只能在 pdfminer 已经把整页拉成一条线性文字流
之后，靠"标签块紧跟数字块"这种文字层面的模式去猜结构——遇到年份表头本身在转文字
这一步就已经丢失/错位的情况（实测中 Apple、Meta 的多年对比表都有这个问题），
文字层面已经无解，只能老实报「年份未确认」。

但 PDF 文件本身并没有真的丢掉这些信息——每个字在页面上都有精确的 (x, y) 坐标，
表格的行列结构完全可以从坐标反推出来，根本不需要经过"转成线性文字"这一步走弯路。
这个模块直接用 pdfplumber 读取原始 PDF 每个词的坐标，按纵坐标把词聚类成行，
找到"一整行全是四位数年份"的表头行，再看后面每一行的数字有没有精确落在某个年份
表头的横坐标上——落得上就是这一年的确切数值（不是猜的，是页面几何位置算出来的），
落不上、或者一行里有数字怎么都对不上任何表头列、或者两个数字抢占同一列，
就整行放弃，不产出任何结果——跟项目一贯的"宁可少抽不能抽错"原则完全一致，
只是这次的"能不能安全对齐"判断标准从"文字模式"换成了"版面坐标"，更接近
表格在人眼里本来的样子，所以能覆盖 table_reconstruct.py 覆盖不到的情况
（实测验证：Apple 第77页 Scope 3、Meta 第78页 Scope 1/2/3 这两个 table_reconstruct.py
也没能确认年份的案例，用坐标法可以精确、无歧义地恢复出完整的"年份->数值"对照，
逐字核对过跟原文一致）。

这个模块的产出方式和 table_reconstruct.py 一样：只生成"改善喂给 LLM 的输入质量"的
合成文本，重新走 analyst.py 现有的 EXTRACT_TMPL + 四层机械校验，不新开任何绕过校验
的信任通道。
"""

from __future__ import annotations

import json
import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

# 项目根目录：<root>/src/pdf_geometry_reconstruct.py -> <root>
_ROOT = Path(__file__).resolve().parent.parent
_ESG_DATA_DIR = _ROOT / "esg_data"

_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
_YEAR_WITH_FOOTNOTE_RE = re.compile(r"^((?:19|20)\d{2})\d{1,2}$")
# 真实数据踩出来的坑：Alphabet 报告表头那一行是"Emissions inventory Unit 20191 2020
# 2021 2022 2023"——"2019"后面紧跟着一个脚注角标编号"1"，中间没有空格，pdfplumber
# 因为两者之间没有可识别的横向间隙，把它们合并成了一个词"20191"。只按"整段必须
# 恰好是4位数字年份"匹配的话，这个词会被判定成"不是年份"，导致 2019 这一整列在
# 表头识别阶段就丢了——不是这一列的数值对不上，是列本身在"认出表头"这一步就被漏
# 掉了，后面 2019 那一列真实存在的数值只能落进"横坐标对不上任何已知表头列"的
# 兜底分支，被误判成标签的一部分（这正是 Alphabet Scope 1 那次真实事故：2019年
# 的 81,900 被拼进了标签文本里，变成"Scope 12 tCOe 81,900"，年份表头只剩4列）。
# 只在"纯4位年份后面紧跟1-2位数字"这个模式下才这样解析（脚注编号通常是个位数
# 或两位数），不改变纯4位年份本身的匹配方式，避免把明显不是"年份+脚注"的号码
# 误判成年份。
_NUMERIC_WORD_RE = re.compile(r"^[-+]?[\d,]+(\.\d+)?%?$")
_DASH_ONLY_RE = re.compile(r"^[-–—]+$")   # 表格里常见的"没有数据"占位符，不是数值也不是标签

_SCOPE_FOOTNOTE_RE = re.compile(r"\bScope\s+([123])\d{1,2}\b", re.IGNORECASE)
# 同一类"脚注角标跟正文之间没有空格、被 pdfplumber 粘成一个词"的坑，这次出现在标签
# 而不是表头：Alphabet 报告里"Scope 1"后面紧跟脚注编号"2"，中间没有空格，被合并成了
# 一个词"Scope 12"。年份列对齐那部分已经修过了（见 _parse_header_year），但重建出来的
# 行标签还是"Scope 12 tCOe"——数值本身是对的，可这个标签喂给模型做二次验证时，模型看到
# "Scope 12"这种它从没见过的写法，会合理地判断"这跟 Scope 1 对不上"，直接回 NOT_IN_
# PASSAGE，白白损失一个数值早就对齐好的真实结果（真实数据跑出来才发现：Alphabet Scope 1
# 的坐标重建候选年份、数值全部正确，但因为标签写成"Scope 12"，模型自己判定不是答案，
# 候选池里排第一的这个候选直接被跳过）。只处理"Scope"后面紧跟"1/2/3 + 1到2位数字，
# 中间没有空格"这个具体模式——这是 GHG Protocol 里唯一会出现"Scope N"这种写法的场景，
# 不会误伤其它含数字的标签（比如"Scope 1, 2 (market-based), and 3"这种表述，数字后面
# 不是紧跟着别的数字，不会被误匹配到）。
def _strip_scope_footnote(label: str) -> str:
    return _SCOPE_FOOTNOTE_RE.sub(lambda m: f"Scope {m.group(1)}", label)

Y_TOL = 2.5     # 聚类成"同一行"的纵坐标容差（实测同一行内的词纵坐标差异 <0.1pt，
                # 不同行之间通常 >15pt，2.5pt 足够安全，不会把相邻两行并到一起）
X_TOL = 6.0     # 数字对齐到表头某一列的横坐标容差（实测同一列在不同行里的横坐标
                # 差异 <0.1pt，相邻列间距通常 60-80pt，6pt 容差足够宽松又不会串列）

_warned_keys: set = set()
_warn_lock = threading.Lock()


def _warn_once(key: str, message: str) -> None:
    """同一类问题只在终端上大声报一次，不刷屏，但绝不允许完全沉默——这个模块之前
    踩过一次"失败了但表现得跟'没有可用数据'一模一样"的坑（pdfplumber 没装成功，
    真实数据跑了一整圈却毫无察觉），代价是绕了好几轮弯路才查出来，所以现在任何
    会导致这一层"整个不生效"或"某一页跳过"的情况，都必须在终端上留下明确痕迹。"""
    with _warn_lock:
        if key in _warned_keys:
            return
        _warned_keys.add(key)
    print(f"[pdf_geometry_reconstruct] {message}", file=sys.stderr)


@dataclass
class GeometricRow:
    label: str
    year_values: dict            # {year(int): value_str}，只包含页面上真的有数字的年份
                                  # （表格里的"-"占位符对应的年份不会出现在这里，不是漏了，
                                  # 是原文本来就没有这个数）
    page: int
    source_chunk_id: str = ""    # 便于跟已有的 partial_table_rows 等信息对照


# ---------------------------------------------------------------------------
# PDF 路径解析：company 名字 -> 原始报告 PDF 的路径
# ---------------------------------------------------------------------------

_pdf_path_cache: dict = {}
_pdf_path_lock = threading.Lock()


def _build_company_pdf_index() -> dict:
    """
    扫描 esg_data/<sector>/<company>/corpus/meta.json（build_corpus.py 生成的体检文件），
    读出每家公司的 report_file 字段，拼出原始 PDF 的完整路径。只扫一次，缓存住。
    """
    index = {}
    if not _ESG_DATA_DIR.is_dir():
        return index
    for meta_path in _ESG_DATA_DIR.glob("*/*/corpus/meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        company = meta.get("company")
        report_file = meta.get("report_file")
        if not company or not report_file:
            continue
        pdf_path = meta_path.parent.parent / report_file
        if pdf_path.is_file():
            index[company] = pdf_path
    return index


def resolve_pdf_path(company: str):
    """
    返回某家公司原始报告 PDF 的路径；找不到（比如这家公司的 PDF 确实缺失，或者是
    图片型 PDF 从来没被列进语料）就返回 None——上层看到 None 时应该直接跳过坐标重建，
    退回到 table_reconstruct.py 的文字模式重建，不报错、不中断整个抽取流程。
    """
    with _pdf_path_lock:
        if not _pdf_path_cache:
            _pdf_path_cache.update(_build_company_pdf_index())
        return _pdf_path_cache.get(company)


# ---------------------------------------------------------------------------
# 版面坐标提取（带缓存，线程安全）
# ---------------------------------------------------------------------------

_page_words_cache: dict = {}
_page_words_lock = threading.Lock()


def extract_page_words(pdf_path, page_no: int) -> list:
    """
    返回某页所有词的坐标信息（word 字典：至少含 text / x0 / top）。
    缓存 + 加锁：一是避免同一页被反复解析（同一家公司的多个指标常常落在同一页），
    二是 pdfplumber/pdfminer 底层解析对象在多线程并发访问同一份 PDF 时没有官方保证的
    线程安全性，这里统一加锁串行化，安全第一，反正这不是性能热点。
    """
    key = (str(pdf_path), page_no)
    with _page_words_lock:
        if key in _page_words_cache:
            return _page_words_cache[key]
        try:
            import pdfplumber
        except ImportError as e:
            # 真实事故：之前这里是静默吞掉、直接返回空列表，跟"这页PDF本来就没有可用
            # 数据"表现得一模一样——结果用户实际运行的 Python 环境里根本没装
            # pdfplumber（跟我验证代码时用的环境不是同一个），导致坐标重建整层
            # 悄无声息地全程空转，跑了一整圈真实数据、什么错误都不报，看起来像是
            # "这个方法对这批PDF不管用"，其实只是没跑起来。这种"失败了但表现得
            # 跟正常情况一模一样"的设计本身就是错的，所以改成第一次遇到就大声报出来，
            # 绝不能让同样的坑再犯一次。
            _warn_once("pdfplumber_import_error",
                       f"[严重] 无法导入 pdfplumber（{e}）——PDF 版面坐标级表格重建这一整层"
                       f"完全没有在运行，所有指标都会退回到没有这一层增强的旧流程，不会报错，"
                       f"只会看起来像是'坐标法找不到数据'。请确认当前运行 analyst.py 用的这个"
                       f"Python 环境里执行 `python -c \"import pdfplumber\"` 不报错。")
            _page_words_cache[key] = []
            return []
        words = []
        try:
            with pdfplumber.open(pdf_path) as pdf:
                if 1 <= page_no <= len(pdf.pages):
                    # build_corpus.py 里 page_no 从 1 开始、跟 PDF 阅读器页码一致；
                    # pdfplumber 的 pdf.pages 是 0-indexed，所以这里要 -1。
                    page = pdf.pages[page_no - 1]
                    words = page.extract_words()
                else:
                    _warn_once(f"page_out_of_range_{pdf_path}",
                               f"[警告] {Path(pdf_path).name} 第{page_no}页超出范围"
                               f"（PDF共{len(pdf.pages)}页），跳过坐标重建，不影响其他页。")
        except Exception as e:
            _warn_once(f"pdf_open_error_{pdf_path}",
                       f"[警告] 打开/解析 {Path(pdf_path).name} 第{page_no}页失败（{e}），"
                       f"跳过坐标重建，不影响其他页，也不中断整体抽取。")
            words = []
        _page_words_cache[key] = words
        return words


# ---------------------------------------------------------------------------
# 行聚类 + 表头识别 + 列对齐
# ---------------------------------------------------------------------------

def _cluster_rows(words: list) -> list:
    """按纵坐标把词聚类成"行"，返回按 top 从小到大排列的行列表，每行是按 x0 排好序的词列表。"""
    if not words:
        return []
    ordered = sorted(words, key=lambda w: w["top"])
    rows = []
    current = [ordered[0]]
    current_top = ordered[0]["top"]
    for w in ordered[1:]:
        if abs(w["top"] - current_top) <= Y_TOL:
            current.append(w)
        else:
            rows.append(sorted(current, key=lambda w: w["x0"]))
            current = [w]
            current_top = w["top"]
    rows.append(sorted(current, key=lambda w: w["x0"]))
    return rows


def _parse_header_year(text: str) -> int | None:
    """把表头词解析成年份整数：优先按纯4位年份匹配；匹配不上时再试"年份+紧跟1-2位
    脚注编号、中间没有空格"这种模式（见 _YEAR_WITH_FOOTNOTE_RE 上面的说明），命中
    就只取前4位当年份，脚注编号部分丢弃不用。两种都对不上就返回 None。"""
    if _YEAR_RE.match(text):
        return int(text)
    m = _YEAR_WITH_FOOTNOTE_RE.match(text)
    if m:
        return int(m.group(1))
    return None


def _is_year_header_row(row: list) -> list | None:
    """
    找这一行里"横坐标连续、中间不被非年份词打断"的最长一串年份词（>=2个）当作表头，
    返回 [(year, x0), ...]（按 x0 排序）；找不到长度>=2的连续串就返回 None。

    真实数据踩过的坑：一开始这里要求"整行全部是年份词"，结果 Apple 的报告是双栏
    排版——正文数据表在页面左侧，右侧还有一栏脚注文字，两栏内容经常落在完全相同的
    纵坐标上，被我们按纵坐标聚类成"同一行"之后，年份表头那一行就会混进右侧脚注的
    文字（比如"2023 2022 2021 2020 2019 • For data on years prior to 2019, please
    reference..."），"整行全年份"的判断因此失败，导致后面所有数据行都找不到表头可用、
    全部被跳过——明明表头信息就在，只是被误判成了"不是表头"。改成"找最长连续年份串"
    之后，哪怕同一行混进了不相关的脚注文字，只要年份词本身在横坐标上是连续排列的
    （中间没有被别的词打断），照样能准确识别出表头，脚注部分自然被晾在一边、
    不影响判断——这一行后面的数据行会不会被脚注文字干扰，由数据行自己的对齐逻辑
    另外把关（见 reconstruct_table_by_geometry），这里只负责认出表头本身。
    """
    best_run: list = []
    current_run: list = []
    for w in row:   # row 已经按 x0 从小到大排好序
        year = _parse_header_year(w["text"])
        if year is not None:
            current_run.append((year, w["x0"]))
        else:
            if len(current_run) > len(best_run):
                best_run = current_run
            current_run = []
    if len(current_run) > len(best_run):
        best_run = current_run
    if len(best_run) < 2:
        return None
    return best_run


def _looks_numeric(text: str) -> bool:
    return bool(_NUMERIC_WORD_RE.match(text)) and not _DASH_ONLY_RE.match(text)


def reconstruct_table_by_geometry(pdf_path, page_no: int, keywords) -> list:
    """
    对某一页做坐标级表格重建，只返回"标签匹配 keywords 且至少能确认一个年份数值"的行。
    核心规则（能不猜就不猜）：
      - 表头必须是"整行全年份"的行，且是当前数据行上方最近的一个表头（一页允许有
        多个表头，管各自下方的数据行，直到遇到下一个表头为止）；没有表头可用的数据行
        直接跳过。
      - 先用表头本身的横坐标划出"标签区"（表头最左一列往左，留 X_TOL 容差）和
        "数据区"（表头最左一列往右）——不是先判断"这个词长得像不像数字"，是先看
        它在页面上落在标签区还是数据区。这一步很关键：像"Scope 1"这种标签本身
        以数字结尾的情况（"1"紧跟在"Scope"后面，横坐标显然还在标签区），如果先用
        "长得像数字"去判断，会把这个"1"错当成一个对不上任何表头列的数值，进而把
        整行误判成"结构可疑"而放弃——真实数据踩过的坑，所以改成先分区、再在数据区
        内部判断数值。
      - 数据区里每一个"长得像数字"的词，必须精确落在表头某一列的横坐标上（容差
        X_TOL）才会被采信为该年份的值；数据区里长得不像数字的词（脚注标记、"N/A"
        这类）直接忽略，不算错；但凡数据区里有个数字怎么都对不上任何表头列，或者
        两个数字抢占了同一列，整行放弃，不产出任何结果——不满足"对齐关系无歧义"
        就不硬凑。
      - 标签区里如果一个词都没有（比如标签因为换行分到了相邻的另一行，没跟数字
        同框），也整行放弃——不去猜多行拼接的标签。
    """
    if not keywords:
        return []
    words = extract_page_words(pdf_path, page_no)
    rows = _cluster_rows(words)

    results = []
    current_header = None   # [(year, x0), ...]，按 x0 排序

    for row in rows:
        header = _is_year_header_row(row)
        if header is not None:
            current_header = header
            continue
        if not current_header:
            continue

        label_boundary = min(x0 for _, x0 in current_header) - X_TOL
        label_words = [w for w in row if w["x0"] < label_boundary]
        data_words = [w for w in row if w["x0"] >= label_boundary]

        if not label_words:
            continue   # 标签没跟数字同框（比如标签换行了），不猜，直接放弃这一行

        year_values = {}
        used_x_positions = []
        aligned_ok = True
        for w in data_words:
            if not _looks_numeric(w["text"]):
                continue   # 数据区里长得不像数字的词（脚注标记、N/A）忽略，不算数也不算错
            match = None
            for year, x0 in current_header:
                if abs(w["x0"] - x0) <= X_TOL:
                    match = (year, x0)
                    break
            if match is None:
                aligned_ok = False   # 有数字对不上任何表头列——整行结构可疑，放弃
                break
            year, x0 = match
            if year in year_values or x0 in used_x_positions:
                aligned_ok = False   # 同一年/同一列被抢占了两次——歧义，放弃
                break
            year_values[year] = w["text"]
            used_x_positions.append(x0)

        if not aligned_ok or not year_values:
            continue

        label = " ".join(w["text"] for w in label_words)
        label = _strip_scope_footnote(label)

        low = label.lower()
        if not any(kw in low for kw in keywords):
            continue

        results.append(GeometricRow(
            label=label, year_values=year_values, page=page_no,
            source_chunk_id=f"{page_no}",
        ))

    return results


# ---------------------------------------------------------------------------
# 双栏正文重排：解决表格重建管不到的"正文被拍扁交错"问题
# ---------------------------------------------------------------------------

_GUTTER_MIN_GAP = 25.0
# 判断"这是不是栏与栏之间的空隙"的最小横向间距。真实数据实测过：正常词与词之间的
# 间距一般 <10pt，双栏排版里两栏之间的空隙普遍 >30pt，25pt 留了安全余量——宽松到
# 不会漏判真正的双栏空隙，又不至于把段落里偶尔宽一点的间距（比如项目符号后的缩进）
# 误判成栏间距。
_GUTTER_MIN_ROWS = 4
# 至少要有4行都在差不多同一个横坐标附近出现"大空隙"，才敢认定这一页真的是双栏排版、
# 这个位置就是栏与栏的分界线——只有1、2行凑巧宽一点，不能说明什么，贸然拆栏反而
# 可能把一个本来正常的单栏页面拆坏。


def reconstruct_two_column_text(pdf_path, page_no: int) -> str | None:
    """
    检测双栏排版，并按真正的阅读顺序（左栏从上到下读完，再读右栏从上到下）重新拼出
    这一页的正文——这是表格重建（reconstruct_table_by_geometry）管不到的另一类真实
    坑：pdfminer 默认按绝对纵坐标从上到下线性提取文字，如果双栏排版里左右两栏恰好
    行高对得上（ESG 报告里很常见），就会把左栏第N行和右栏第N行横向拼在一起、按行
    交错输出——一句完整的话被拦腰切开，中间插进另一栏完全无关的内容。

    真实踩过的坑：Meta 报告第31页，"我们在2021年开始与39家核心供应商合作，核算并
    汇报它们的温室气体排放"这句话，被拆成"In 2021, we began working with"和
    "a pilot group of 39 key suppliers"，分别插进了旁边讲"冷冻水泵"制冷设备的
    无关段落中间。模型拿到这种乱序文本，合理地判断"这里没有答案"（NOT_IN_PASSAGE），
    一个报告里写得清清楚楚的真实数字就这样丢了——不是数据不存在，是文字顺序被
    拍碎了，模型读不出来。

    做法：先按纵坐标把词聚类成行（复用 _cluster_rows），只看"行内恰好有一处明显大
    空隙"的行（见 _GUTTER_MIN_GAP）——要求"恰好一处"是为了把页眉/页脚导航条这种
    一行里有好几处空隙、但根本不是双栏正文的行天然排除掉，不让它们混进来污染判断。
    再用滑动窗口找这些空隙位置里"半径20pt内聚集最多"的那一撮，当作真正占多数的
    栏间分界线（不是简单取中位数——实测发现同一页可能中途换了一次栏宽，两种空隙
    位置掺在一起时，中位数会被带偏到两者中间、两边都对不上；找"聚集最密的一撮"能
    准确挑出真正占多数的那种栏宽，忽略掉少数派或者纯属巧合的孤立大空隙）。这一撮
    数量不够多（见 _GUTTER_MIN_ROWS）就认为这页本来是单栏，原样返回 None——绝不对
    单栏页面强行拆栏，拆错了比不拆还糟。分界线两侧的词各自按行的原有顺序（从上到下、
    行内从左到右）拼起来，左栏拼完接右栏，就是这一页真正的阅读顺序。

    已知局限：如果一页里不止一种栏宽（比如正文中途换了小节、栏宽跟着变了），这里
    只能挑出占多数的那一种，把它对应的那部分正文理顺，另一种栏宽的那部分不受影响
    （原样保留在最后拼接的左右栏文本里，可能还是交错的）——这是刻意的取舍：宁可
    只修好用得最多的那部分，也不做一个更复杂、更容易出错的多栏检测。

    这里只负责把"文字顺序"理顺，不代表内容一定是这个指标要的答案——重排出来的文本
    照样要送进跟其它候选完全一样的 EXTRACT_TMPL + 四层机械校验全流程，不新开任何
    信任通道，是不是真的答对了，还是交给既有校验去判断。
    """
    words = extract_page_words(pdf_path, page_no)
    if not words:
        return None
    rows = _cluster_rows(words)

    gutter_candidates = []
    for row in rows:
        if len(row) < 2:
            continue
        row_sorted = sorted(row, key=lambda w: w["x0"])
        row_gaps = []
        for a, b in zip(row_sorted, row_sorted[1:]):
            gap = b["x0"] - a.get("x1", a["x0"])
            if gap >= _GUTTER_MIN_GAP:
                row_gaps.append((a.get("x1", a["x0"]) + b["x0"]) / 2)
        if len(row_gaps) == 1:
            # 只要"恰好一处"大空隙的行，才算数——页眉/页脚这种一行里有好几处空隙的
            # （比如导航条被好几个大间距的短语隔开），天然被排除，不会混进来当噪音。
            gutter_candidates.append(row_gaps[0])

    if len(gutter_candidates) < _GUTTER_MIN_ROWS:
        return None   # 大空隙太少，不像真正的双栏页，不强行拆

    # 找"半径20pt内聚集最多"的那一撮，当作真正占多数的栏间分界线（见上面函数说明
    # 里"已知局限"那段——不用简单中位数，是为了不被同一页里可能存在的第二种栏宽
    # 带偏）。
    cluster_radius = 20.0
    best_members: list = []
    for v in gutter_candidates:
        members = [g for g in gutter_candidates if abs(g - v) <= cluster_radius]
        if len(members) > len(best_members):
            best_members = members
    if len(best_members) < _GUTTER_MIN_ROWS:
        return None   # 大空隙没有稳定聚在同一个位置，说明不是同一条栏间分界线，放弃
    split_x = sum(best_members) / len(best_members)

    left_lines, right_lines = [], []
    for row in rows:
        row_sorted = sorted(row, key=lambda w: w["x0"])
        left_words = [w for w in row_sorted if w["x0"] < split_x]
        right_words = [w for w in row_sorted if w["x0"] >= split_x]
        if left_words:
            left_lines.append(" ".join(w["text"] for w in left_words))
        if right_words:
            right_lines.append(" ".join(w["text"] for w in right_words))

    left_text = "\n".join(left_lines).strip()
    right_text = "\n".join(right_lines).strip()
    combined = "\n\n".join(t for t in (left_text, right_text) if t)
    return combined or None


# ---------------------------------------------------------------------------
# 合成文本（喂给现有的 EXTRACT_TMPL + 四层机械校验，不新开信任通道）
# ---------------------------------------------------------------------------

def rows_to_synthetic_chunk_geometry(row: GeometricRow, unit_context: str | None = None) -> dict:
    """
    真实数据踩过的坑：一开始 unit_context 是拼在整段话最后的一句"Unit context from
    the same page: (in metric tons CO2e)."，跟每条"标签 in 年份: 数值"分句是分开的。
    结果模型（合理地）把单位一起写进了它汇报的 quote 里，比如
    "Scope 1 in 2022: 66,934 (in metric tons CO2e)"——数值和年份都是对的，
    但这段 quote 作为一个整体在原文里找不到连续匹配（原文里单位说明在最后，不是
    紧跟在这个数值后面），被机械校验①（quote 必须是原文连续子串）正确地按"证据
    编造"拒绝了——模型没有编数字，是我自己拼出来的合成文本结构不对，逼得模型的
    quote 没法是一句连续原文。现在改成把单位直接跟在每一条"年份: 数值"后面，
    模型无论怎么组织它的 quote，只要提到某年的值，单位就紧挨着，天然是连续子串。
    """
    tag = re.sub(r"[^a-zA-Z0-9]+", "_", row.label)[:30]
    unit_suffix = f" {unit_context}" if unit_context else ""
    years_sorted = sorted(row.year_values.items())
    clauses = "; ".join(f"{row.label} in {y}: {v}{unit_suffix}" for y, v in years_sorted)
    content = (
        f"{clauses} (reconstructed directly from the original PDF's page layout coordinates on "
        f"page {row.page}; each year was matched to its value by its exact column position on the "
        f"page, not guessed from flattened text)."
    )
    return {
        "chunk_id": f"{row.page}__geometry_reconstructed__{tag}",
        "page": row.page,
        "content": content,
        "reconstructed": True,
        "reconstruction_method": "pdf_geometry",
        "years_confirmed": True,
    }

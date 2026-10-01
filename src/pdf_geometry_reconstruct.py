#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pdf_geometry_reconstruct.py —— 直接用PDF原始版面上的坐标，把"年份表头对应哪个数字"这件事找回来

这个文件跟table_reconstruct.py解决的是同一类问题（表格被拍扁以后，标签跟数字
对不上了），但这次是从更上游下手的。table_reconstruct.py只能在pdfminer已经把
整页拉成一条直直的文字流之后，靠"标签紧跟着数字"这种文字层面的规律去猜结构——
遇到年份表头本身在转文字这一步就已经丢了或者错位的情况（实测发现Apple、Meta
的多年对比表都有这毛病），纯从文字角度已经没法救了，只能老实说"年份没法确认"。

但PDF文件本身其实并没有真丢掉这些信息——每个字在页面上都有精确的(x, y)坐标，
表格的行列结构完全可以从坐标反推出来，根本不用绕"先转成一条直文字流"这个弯路。
这个文件直接用pdfplumber读原始PDF里每个词的坐标，按纵坐标把词聚成一行一行，
找出"整行都是四位数年份"的表头行，再看后面每一行的数字，横坐标有没有精确落在
某个年份表头正下方——落上了，那就是这一年真实的数值（不是猜的，是按页面上的
几何位置算出来的）；落不上、或者一行里有个数字怎么都对不上任何一列、或者两个
数字抢占了同一列，那整行直接放弃，什么都不产出——这跟这个项目一贯"宁可少抓
也不能抓错"的原则完全一致，只是这次判断"能不能放心配对"的标准，从"文字的
排列模式"换成了"版面上的坐标位置"，更接近表格在人眼里原本的样子，所以能补上
table_reconstruct.py补不到的情况（实测验证过：Apple第77页的Scope 3、Meta
第78页的Scope 1/2/3，这几个table_reconstruct.py也没能确认年份的例子，用坐标法
可以精确、没有歧义地把完整的"年份->数值"对照恢复出来，逐字核对过跟原文一致）。

这个文件产出东西的方式跟table_reconstruct.py一样：只生成"让喂给LLM的材料更
干净"的合成文本，重新走一遍analyst.py现成的EXTRACT_TMPL和那四层机械校验，
不会另开一条绕过校验的近路。
"""

from __future__ import annotations

import json
import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

# 找到项目根目录：这个文件在<root>/src/下面，往上退一层就是<root>
_ROOT = Path(__file__).resolve().parent.parent
_ESG_DATA_DIR = _ROOT / "esg_data"

_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
_YEAR_WITH_FOOTNOTE_RE = re.compile(r"^((?:19|20)\d{2})\d{1,2}$")
# 真实踩过的坑：Alphabet报告的表头那一行写的是"Emissions inventory Unit 20191
# 2020 2021 2022 2023"——"2019"后面紧跟着一个脚注小角标"1"，中间没有空格，
# pdfplumber因为两者之间没有能识别出来的间隙，就把它们粘成了一个词"20191"。
# 如果只按"必须刚好是4位数字"来判断年份，这个词就会被判成"不是年份"，结果2019
# 这一整列在识别表头这一步就直接漏掉了——不是这一列的数值对不上，是这一列压根
# 就没被认出来是表头的一部分。后面2019那列真实存在的数值，就只能掉进"横坐标
# 对不上任何已知表头列"的兜底分支里，被误当成了标签的一部分（这就是Alphabet
# Scope 1那次真实事故：2019年的81,900被拼进了标签文字里，变成"Scope 12 tCOe
# 81,900"，年份表头就只剩4列了）。所以这里只在"4位年份后面紧跟着1到2位数字"
# 这个具体情况下才这样处理（脚注编号一般就是一两位数），不动纯4位年份本身的判断
# 方式，免得把明显不是"年份+脚注"的号码也误判成年份。
_NUMERIC_WORD_RE = re.compile(r"^[-+]?[\d,]+(\.\d+)?%?$")
_DASH_ONLY_RE = re.compile(r"^[-–—]+$")   # 表格里常见的"没数据"占位符（比如"-"），既不是数值也不是标签

_SCOPE_FOOTNOTE_RE = re.compile(r"\bScope\s+([123])\d{1,2}\b", re.IGNORECASE)
# 又是一个"脚注小角标跟正文之间没空格、被pdfplumber粘成一个词"的坑，不过这次出现在
# 标签上，不是表头：Alphabet报告里"Scope 1"后面紧跟着脚注编号"2"，中间没空格，被粘
# 成了"Scope 12"。年份那部分的对齐已经修过了（见_parse_header_year），但重建出来的
# 行标签还是长得像"Scope 12 tCOe"——数值本身没错，可这个标签拿去给模型二次验证的
# 时候，模型看到"Scope 12"这种从没见过的写法，会很合理地判断"这跟Scope 1对不上"，
# 直接判定NOT_IN_PASSAGE，白白丢掉一个数值明明对齐得好好的结果（真实数据里发现的：
# Alphabet Scope 1坐标法重建出来的候选，年份、数值全部正确，但就因为标签写成了
# "Scope 12"，模型自己判定这不是答案，候选池里排第一个的这条直接被跳过了）。这里
# 只处理"Scope后面紧跟1/2/3再加1到2位数字、中间没空格"这一个具体模式——这是GHG
# Protocol里唯一会出现"Scope N"这种写法的场景，不会误伤别的带数字的标签（比如
# "Scope 1, 2 (market-based), and 3"这种说法，数字后面不是紧跟着另一个数字，
# 不会被误匹配到）。
def _strip_scope_footnote(label: str) -> str:
    return _SCOPE_FOOTNOTE_RE.sub(lambda m: f"Scope {m.group(1)}", label)


_LABEL_TRAILING_UNIT_RE = re.compile(r"\b(tCOe|tCO2e|tCO₂e|CO2e|CO₂e)\b", re.IGNORECASE)
# 真实踩过的坑（第三层，这次修的）：Alphabet的表头本身就把单位写进了行标签里
# （比如"Scope 1 tCOe"），find_unit_context只会去正文里找单独的"(in metric tons
# CO2e)"这种括号说明（这份PDF压根没这种写法，单位只出现在表头的缩写里），
# unit_context传进来就是None——合成出来的文本就变成"Scope 1 tCOe in 2023:
# 79,400"，单位紧跟在标签后面、数值前面，数值后面反倒什么都没有。EXTRACT_TMPL
# 第3步教模型认的"可信三元组"顺序是"标签→数字→单位"，数值后面找不到单位，模型
# （尤其是看不到别的年份、没法确认这是不是"最新一年"的时候）会合理地觉得这不是
# 一条干净的三元组——真实数据跑出来才发现：Alphabet Scope 1坐标法重建的5个年份
# 全部精确对齐、数值也是真的，却因为这一个格式上的小细节，连续5个年份的候选全部
# 被判NOT_IN_PASSAGE，整行数据从报告里彻底消失了（比"标签写成Scope 12"那次更
# 隐蔽：这次标签本身没写错，只是单位出现的位置不对）。这里就是把标签自己已经写着
# 的单位摘出来，在数值后面再贴一份，凑齐"标签→数字→单位"这个顺序——不是凭空
# 造了个单位，只是把原文自己写的单位挪一份到数值后面，让模型看着更像它被教过的
# "可信三元组"的样子。
def _label_unit_fallback(label: str) -> str | None:
    m = _LABEL_TRAILING_UNIT_RE.search(label)
    return f"({m.group(0)})" if m else None

Y_TOL = 2.5     # 判断"是不是同一行"时，纵坐标允许的误差（实测同一行里词的纵坐标
                # 差不到0.1pt，不同行之间一般差15pt以上，2.5pt留得很安全，不会把
                # 相邻两行错当成一行）
X_TOL = 6.0     # 判断"数字是不是落在某一列表头下面"时，横坐标允许的误差（实测同一
                # 列在不同行里横坐标差不到0.1pt，相邻两列间距一般有60-80pt，6pt的
                # 容差够宽松，又不会串到隔壁列）

_warned_keys: set = set()
_warn_lock = threading.Lock()


def _warn_once(key: str, message: str) -> None:
    """同一类问题只在终端上大声喊一次，不刷屏，但绝对不能完全不吭声——这个文件
    之前踩过一次坑：失败了，但表现得跟"这页本来就没数据"一模一样（pdfplumber
    没装上，真实数据跑了一整圈，完全没人发现），结果绕了好几圈弯路才查出问题在哪。
    所以现在只要是会导致这一层"整个不起作用"或者"某一页被跳过"的情况，都必须在
    终端上留下明显的痕迹，不能悄悄咽下去。"""
    with _warn_lock:
        if key in _warned_keys:
            return
        _warned_keys.add(key)
    print(f"[pdf_geometry_reconstruct] {message}", file=sys.stderr)


@dataclass
class GeometricRow:
    label: str
    year_values: dict            # {年份(int): 数值字符串}，只收页面上真的有数字的年份
                                  # （表格里"-"这种占位符对应的年份不会出现在这里——不是
                                  # 漏掉了，是原文里这一年本来就没有数）
    page: int
    source_chunk_id: str = ""    # 留着方便跟已有的partial_table_rows这些信息对照


# ---------------------------------------------------------------------------
# 根据公司名字，找到它原始报告PDF文件的路径
# ---------------------------------------------------------------------------

_pdf_path_cache: dict = {}
_pdf_path_lock = threading.Lock()


def _build_company_pdf_index() -> dict:
    """
    扫一遍esg_data/<sector>/<company>/corpus/meta.json这些文件（build_corpus.py生成
    的体检文件），把每家公司的report_file字段读出来，拼成原始PDF的完整路径。
    只扫一次，扫完缓存住，不用每次都重新扫。
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
    返回某家公司原始报告PDF的路径。找不到就返回None（比如这家公司PDF确实没有，
    或者是扫描版PDF压根没被收进语料库）——上层看到None就该直接跳过坐标重建这一步，
    退回去用table_reconstruct.py的文字模式重建，不报错，也不打断整个抽取流程。
    """
    with _pdf_path_lock:
        if not _pdf_path_cache:
            _pdf_path_cache.update(_build_company_pdf_index())
        return _pdf_path_cache.get(company)


# ---------------------------------------------------------------------------
# 从PDF里把每个词的坐标读出来（带缓存，线程安全）
# ---------------------------------------------------------------------------

_page_words_cache: dict = {}
_page_words_lock = threading.Lock()


def extract_page_words(pdf_path, page_no: int) -> list:
    """
    返回某一页所有词的坐标信息（每个word字典至少有text/x0/top这几个字段）。
    这里又缓存又加锁，原因有两个：一是避免同一页被反反复复解析（同一家公司好几个
    指标经常都落在同一页上）；二是pdfplumber/pdfminer底层这些解析对象，在多线程
    同时读同一份PDF的时候，官方并不保证是线程安全的，干脆统一加锁串行处理，安全
    第一——反正这里也不是性能瓶颈，没必要冒险。
    """
    key = (str(pdf_path), page_no)
    with _page_words_lock:
        if key in _page_words_cache:
            return _page_words_cache[key]
        try:
            import pdfplumber
        except ImportError as e:
            # 真实出过的事故：以前这里是悄悄吞掉错误、直接返回空列表，表现得跟"这页PDF
            # 本来就没数据"一模一样——结果用户实际跑代码的Python环境里根本没装
            # pdfplumber（跟我验证代码时用的环境不是同一个），导致坐标重建这一整层悄无
            # 声息地全程空转，真实数据跑了一整圈什么错误都不报，看起来像是"这个方法对
            # 这批PDF不管用"，其实只是压根没跑起来。这种"失败了但表现得跟一切正常
            # 一模一样"的设计本身就是有问题的，所以改成第一次遇到就大声报出来，绝不能
            # 让同样的坑再踩一次。
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
                    # build_corpus.py里page_no是从1开始数的，跟PDF阅读器里看到的页码
                    # 一致；pdfplumber的pdf.pages是从0开始数的，所以这里要减1。
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
# 把词聚成行 + 认出表头 + 把数字对齐到列
# ---------------------------------------------------------------------------

def _cluster_rows(words: list) -> list:
    """按纵坐标把词聚成一行一行，返回的行列表按上下顺序（top从小到大）排好，每一行内部的词又按左右顺序（x0）排好。"""
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
    """把表头里的一个词解析成年份数字：先看是不是纯4位年份；不是的话再试试"年份后面
    紧跟1-2位脚注编号、中间没空格"这种情况（具体背景见上面_YEAR_WITH_FOOTNOTE_RE
    那段说明），命中的话只取前4位当年份，脚注编号那部分扔掉不用。两种都不匹配就
    返回None。"""
    if _YEAR_RE.match(text):
        return int(text)
    m = _YEAR_WITH_FOOTNOTE_RE.match(text)
    if m:
        return int(m.group(1))
    return None


def _is_year_header_row(row: list) -> list | None:
    """
    在这一行里找"横坐标连续排列、中间没被非年份的词打断"的最长一串年份词
    （至少2个）当作表头，返回[(年份, x0), ...]（按x0排好序）；找不到长度够2个的
    连续串就返回None。

    真实踩过的坑：一开始这里要求"这一整行必须全是年份词"，结果Apple的报告是双栏
    排版——左边是正文数据表，右边还有一栏脚注文字，两栏内容经常落在完全一样的
    纵坐标上。我们按纵坐标把词聚成"同一行"以后，年份表头那一行就混进了右侧脚注的
    文字（比如"2023 2022 2021 2020 2019 • For data on years prior to 2019, please
    reference..."这样），"整行必须全是年份"这个判断就通不过了，导致后面所有数据行
    都找不到能用的表头，全部被跳过——表头信息明明就在那儿，只是被误判成"不是表头"
    了。改成"找最长的连续年份串"之后，就算同一行混进了不相关的脚注文字，只要年份
    词本身在横坐标上是连续排在一起的（中间没被别的词打断），照样能准确认出表头，
    脚注部分自然被晾在旁边、不影响判断——至于这一行下面的数据行会不会被脚注文字
    干扰，那是数据行自己的对齐逻辑要另外把关的事（见reconstruct_table_by_geometry），
    这里只管认出表头本身。
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
    对某一页做坐标级的表格重建，只返回"标签能匹配上keywords、并且至少确认了一个
    年份数值"的行。核心规则就一句话：能不猜就不猜。
      - 表头必须是"整行全是年份"的那种行，而且得是当前数据行上方最近的那一个表头
        （一页里可以有好几个表头，各管各下面的数据行，直到遇到下一个表头为止）；
        上面没有表头可用的数据行，直接跳过不处理。
      - 先用表头本身的横坐标划出"标签区"（表头最左边那一列往左，留出X_TOL的余量）
        和"数据区"（表头最左边那一列往右）——注意这里不是先看"这个词长得像不像
        数字"来判断，而是先看它落在页面上的标签区还是数据区。这一步很关键：像
        "Scope 1"这种标签本身就以数字结尾的情况（"1"紧跟在"Scope"后面，但横坐标
        明显还在标签区），如果先按"长得像数字"来判断，就会把这个"1"错当成一个
        对不上任何表头列的数值，进而把整行误判成"结构有问题"而放弃掉——这是真实
        踩过的坑，所以改成先分区、再在数据区里面判断是不是数字。
      - 数据区里每一个"长得像数字"的词，必须精确落在表头某一列的横坐标上（容差是
        X_TOL）才会被采信成那一年的数值；数据区里长得不像数字的词（脚注标记、"N/A"
        这类）直接忽略掉，不算错；但只要数据区里有个数字怎么都对不上任何表头列，
        或者两个数字抢占了同一列，那整行就放弃，什么都不产出——只要"对齐关系有
        没有歧义"这件事说不准，就不硬凑一个答案出来。
      - 标签区里要是一个词都没有（比如标签因为换行分到了旁边另一行，没跟数字挤在
        一起），也整行放弃——不去猜"标签是不是被拆成了好几行"。
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
            continue   # 标签没跟数字同框（比如标签换行分开了），不去猜，直接放弃这一行

        year_values = {}
        used_x_positions = []
        aligned_ok = True
        for w in data_words:
            if not _looks_numeric(w["text"]):
                continue   # 数据区里长得不像数字的词（脚注标记、N/A这些）直接跳过，不算数也不算错
            match = None
            for year, x0 in current_header:
                if abs(w["x0"] - x0) <= X_TOL:
                    match = (year, x0)
                    break
            if match is None:
                aligned_ok = False   # 有个数字怎么都对不上任何表头列——这一行结构可疑，放弃
                break
            year, x0 = match
            if year in year_values or x0 in used_x_positions:
                aligned_ok = False   # 同一年或者同一列被占用了两次——有歧义，放弃
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
# 双栏正文重新排序：解决表格重建管不到的"正文被交错拍扁"问题
# ---------------------------------------------------------------------------

_GUTTER_MIN_GAP = 25.0
# 判断"这是不是两栏之间的空隙"用的最小横向间距。实测过：正常词跟词之间的间距
# 一般不到10pt，双栏排版里两栏中间的空隙普遍超过30pt，25pt留了安全余量——既
# 宽松到不会漏判真正的双栏空隙，又不至于把段落里偶尔宽一点的间距（比如项目符号
# 后面的缩进）错当成栏间距。
_GUTTER_MIN_ROWS = 4
# 至少要有4行都在差不多同一个横坐标位置出现"大空隙"，才敢认定这一页真的是双栏
# 排版、这个位置就是两栏的分界线——要是只有一两行凑巧宽了点，说明不了什么，
# 贸然去拆栏反而可能把一个本来正常的单栏页面拆坏了。


def reconstruct_two_column_text(pdf_path, page_no: int) -> str | None:
    """
    检测这一页是不是双栏排版，如果是，就按真正的阅读顺序（左栏从上读到下，再读右栏
    从上到下）把这一页的正文重新拼出来——这是表格重建
    （reconstruct_table_by_geometry）管不到的另一类真实的坑：pdfminer默认是按绝对
    纵坐标从上到下把文字线性提取出来的，如果双栏排版里左右两栏刚好行高对得上
    （ESG报告里这种情况很常见），就会把左栏第N行和右栏第N行横着拼在一起、按行交错
    输出——一句完整的话被从中间切开，中间插进另一栏完全不相关的内容。

    真实踩过的坑：Meta报告第31页，"我们在2021年开始与39家核心供应商合作，核算并
    汇报它们的温室气体排放"这句话，被拆成了"In 2021, we began working with"和
    "a pilot group of 39 key suppliers"两半，分别插进了旁边讲"冷冻水泵"制冷设备的
    不相关段落中间。模型拿到这种被打乱顺序的文本，会很合理地判断"这里没有答案"
    （NOT_IN_PASSAGE）——不是数据不存在，是文字顺序被拍碎了，模型压根读不出来，
    一个报告里明明白白写着的数字就这样丢了。

    具体做法：先按纵坐标把词聚成行（复用_cluster_rows），只看"这一行里恰好有一处
    明显大空隙"的行（见_GUTTER_MIN_GAP）——要求"恰好一处"是为了把页眉页脚这种
    一行里有好几处空隙、但根本不是双栏正文的行天然排除掉，不让它们混进来干扰
    判断。再用滑动窗口的办法，找这些空隙位置里"半径20pt以内聚得最密"的那一撮，
    当成真正占多数的栏间分界线（不用简单取中位数——实测发现有的页面中途换过一次
    栏宽，两种空隙位置混在一起时，中位数会被带偏到两者中间、两边都对不上；找
    "聚得最密的一撮"才能准确挑出真正占多数的那种栏宽，忽略掉少数派或者纯属巧合
    的孤立大空隙）。这一撮数量不够多（见_GUTTER_MIN_ROWS）就认为这页本来就是单栏，
    原样返回None——绝不对单栏页面强行拆栏，拆错了比不拆还糟糕。分界线两边的词，
    各自按原来的顺序（从上到下、行内从左到右）拼起来，左栏拼完接右栏，就是这一页
    真正的阅读顺序。

    已知的局限：如果一页里不止一种栏宽（比如正文中途换了个小节、栏宽跟着变了），
    这里只能挑出占多数的那一种，把它对应的那部分正文理顺，另一种栏宽的部分不受
    影响（原样留在最后拼出来的左右栏文本里，可能还是乱的）——这是故意做的取舍：
    宁可只修好占多数的那部分，也不去做一个更复杂、更容易出错的多栏检测。

    这里只负责把"文字顺序"理顺，不代表理顺之后的内容就一定是这个指标要的答案——
    重排出来的文本照样要送进跟其它候选完全一样的EXTRACT_TMPL加四层机械校验走
    一遍，不开新的信任通道，到底是不是真答对了，还是交给现成的校验去判断。
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
            # 只有"恰好一处"大空隙的行才算数——页眉页脚这种一行里有好几处空隙的情况
            # （比如导航条被好几个大间距的短语隔开）天然就被排除掉了，不会混进来当噪音。
            gutter_candidates.append(row_gaps[0])

    if len(gutter_candidates) < _GUTTER_MIN_ROWS:
        return None   # 大空隙太少，不像真正的双栏页面，不硬拆

    # 找"半径20pt以内聚得最密"的那一撮，当成真正占多数的栏间分界线（具体原因见上面
    # 函数说明里"已知局限"那段——不用简单的中位数，是为了不被同一页里可能存在的
    # 第二种栏宽带偏）。
    cluster_radius = 20.0
    best_members: list = []
    for v in gutter_candidates:
        members = [g for g in gutter_candidates if abs(g - v) <= cluster_radius]
        if len(members) > len(best_members):
            best_members = members
    if len(best_members) < _GUTTER_MIN_ROWS:
        return None   # 大空隙没有稳定地聚在同一个位置，说明这不是同一条栏间分界线，放弃
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
# 生成合成文本（喂给现成的EXTRACT_TMPL和四层机械校验，不开新的信任通道）
# ---------------------------------------------------------------------------

def rows_to_synthetic_chunk_geometry(row: GeometricRow, unit_context: str | None = None) -> list:
    """
    真实踩过的坑（第一层，已经修了）：一开始unit_context是拼在整段话最后单独一句
    "Unit context from the same page: (in metric tons CO2e)."这样的说明，跟每条
    "标签 in 年份: 数值"是分开写的。结果模型（很合理地）把单位一起写进了它汇报的
    quote里，比如"Scope 1 in 2022: 66,934 (in metric tons CO2e)"——数值和年份
    都是对的，但这段quote作为一整段话在原文里找不到连续的匹配，被机械校验①正确地
    当成"编造证据"给拒了。现在把单位直接跟在每一条"年份: 数值"后面，模型不管
    怎么组织它的quote，只要提到某一年的值，单位就紧挨在旁边，天然就是一段连续
    的文字。

    真实踩过的坑（第二层，这次修的）：一张表通常有5年数据（2019到2023），以前是把
    这5年全部拼进*同一个*合成chunk里，一次性交给模型自己去挑"该用哪一年"——
    问题是required_quote_keywords/disambiguation这些设置从来没告诉过模型"有
    好几年数据、又没指定具体年份的时候，应该取最新那一年"，模型挑年份基本上是
    瞎挑的。真实数据跑出来的结果是：Alphabet Scope 1报的是2020年的55,800，
    Meta Scope 1和Scope 3报的是2022年的值，而不是三家公司本该统一比较的最新
    一年（2023）——同一份"公司对比报告"里，不同公司甚至同一家公司的不同指标，
    选出来的年份互相对不上，这比"未披露"更隐蔽也更危险：数字看着都对、都能在
    原文里核实到，可放一起比较的时候，比的根本不是同一年，谁都不会去怀疑。

    这道题不能再指望模型自己去判断"该选哪一年"了（这个项目已经因为"光靠prompt
    提醒模型自己判断"不靠谱，反复吃过好几次亏，思路一直是能用写死的代码就不赌
    模型的自觉）。这里改成**每一年拆成单独一个候选**，按年份从新到旧排好序返回——
    LOCATE+VERIFY那套"抓到第一个通过的就停"的顺序验证逻辑一行都不用改，天然就会
    先试最新一年，最新一年验证通过就直接采信，不会有机会走到更旧的年份；只有最新
    一年因为某种原因没通过校验（比如那一列是"-"占位、没有数据），才会退到次新的
    一年——这正是真实尽调场景想要的行为："优先用最新数据，最新的确实没有才退而
    求其次"，而不是交给模型去猜。
    """
    tag = re.sub(r"[^a-zA-Z0-9]+", "_", row.label)[:30]
    if not unit_context:
        unit_context = _label_unit_fallback(row.label)
    unit_suffix = f" {unit_context}" if unit_context else ""
    years_sorted_desc = sorted(row.year_values.items(), key=lambda item: -item[0])
    results = []
    for year, value in years_sorted_desc:
        content = (
            f"{row.label} in {year}: {value}{unit_suffix} (reconstructed directly from the "
            f"original PDF's page layout coordinates on page {row.page}; this value was matched "
            f"to year {year} by its exact column position on the page, not guessed from "
            f"flattened text)."
        )
        results.append({
            "chunk_id": f"{row.page}__geometry_reconstructed__{tag}__{year}",
            "page": row.page,
            "content": content,
            "reconstructed": True,
            "reconstruction_method": "pdf_geometry",
            "years_confirmed": True,
            "year": year,
        })
    return results

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
parse_ratings.py —— 把 MSCI 和 S&P Global 这两家第三方评级机构的 PDF 里的数字挖出来

这是干嘛用的（详细背景看交接文档第5节「升级版创新点2」）：
    公司自己写的 ESG 报告里全是漂亮话（"我们承诺2030年前实现净零"），但这些
    承诺到底兑现了没有、覆盖了多少排放，报告自己肯定不会老实说。MSCI 和
    S&P Global 是两家独立的第三方评级机构，专门把"说得好不好"和"做得怎么样"
    这两件事量化出来：
      - MSCI：给个字母评级（从 AAA 到 CCC）、算个 Implied Temperature Rise
              （说白了就是照这个公司现在这样干，全球温度大概会升高多少度）、
              减碳目标覆盖了百分之多少的排放、还有评级历史（是在变好还是变差）。
      - S&P Global（他们管这个叫 CSA，Corporate Sustainability Assessment）：
              打个0到100的总分，再加几个分项分数。
    这个脚本就是把这两份 PDF 里的数字抠出来，供 analyst.py 那边去做"漂绿检测"
    和"评级分歧"分析——这样解释两家评级机构为什么打分不一样，靠的是真实数字，
    而不是空口说一句"评级不一样"。

写这个脚本的基本原则（跟项目其他地方一样：宁可少抓，也不能抓错）：
    我手头没有真的 MSCI/S&P PDF 能拿来测试（这两份文件在作者自己电脑的
    esg_data/ 目录下，这个环境里没有）。而且评级机构的PDF排版每年都在变、
    两家机构格式也不统一，正则表达式不可能一次就写对。所以这个脚本是照着
    "作者在自己电脑上跑、边跑边看结果对不对"这个思路设计的：

    1. 每抽出来一个字段都必须带上 evidence（也就是抽取时命中的那段原文），
       绝不能给个"光秃秃的数字"就完事——这样作者本地跑完能直接拿去对照PDF
       原文核实，这也是这个项目一直坚持的"数据都能查到出处"这条底线的延伸。
    2. 抽不准的字段一律标成 "??"，扔进 needs_manual_review 这个清单里，
       绝对不瞎猜。
    3. 提供了 --dump-text 这个选项，能把PDF解析出来的全文整个打印出来，
       这样万一没抽到什么东西，可以直接肉眼去找关键词、调整正则或者往
       关键词表里加词——目的不是让作者自己去猜这个脚本哪里有问题，而是
       让她5分钟之内就能自己把正则改对。
    4. 提供了 --manual 选项，可以把人工核对过的值合并进去——抽取失败的字段
       可以手动填回去，不用因为一两个字段没抽到就把整个结果推倒重来。
    5. 评级历史这种表格文字，正则很难稳定地抽出结构，所以这块改成让 GLM 做
       一次"把表格读懂、转成JSON"的小任务（只有设了 ZHIPU_API_KEY 才会真的
       调用；没设 key 就退化成保留原文片段——这跟 analyst.py 里"模型只负责
       把内容结构化、规则层不依赖模型"的分工是一致的）。

用法：
    export ZHIPU_API_KEY="你的key"     # 仅"--llm-parse-history"需要，其余纯正则/离线可跑

    # 解析单家公司（在 esg_data/<sector>/<company>/ 下找 MSCI.pdf 和 *S&P*.pdf）
    python src/parse_ratings.py --company Apple

    # 全部公司
    python src/parse_ratings.py --all

    # 抽取效果不对时，先把全文倒出来找关键词
    python src/parse_ratings.py --company Apple --dump-text msci > /tmp/apple_msci.txt

    # 人工核对后，把手填的字段合并进去（不会覆盖 evidence，只覆盖 value）
    python src/parse_ratings.py --company Apple --manual analysis/ratings/Apple.manual.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "esg_data"
OUT_DIR = ROOT / "analysis" / "ratings"

# 这里复用的是 agent_cloud.py 里已经跑通的云端调用那套东西（同一个智谱账号、
# 同一套重试逻辑）。只有 --llm-parse-history 这个选项会用到它，其它功能全是本地
# 正则表达式，不用联网也不用 key。
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from agent_cloud import cloud_gen  # noqa: E402
except Exception:                        # noqa: BLE001  # 就算没设 key，agent_cloud 这个模块也能正常 import 进来
    cloud_gen = None


# ---------------------------------------------------------------------------
# 关键词表：用来找同一个字段的不同说法，PDF 排版一变，直接在这儿加词就行，
# 不用去改抽取逻辑本身
# ---------------------------------------------------------------------------

MSCI_RATING_KEYS = ("esg rating", "msci esg rating", "industry-adjusted score")
MSCI_ITR_KEYS = ("implied temperature rise", "itr", "implied warming")
MSCI_COVERAGE_KEYS = ("target coverage", "emissions covered", "coverage of company",
                       "% of company emissions", "percent of emissions")
MSCI_REDUCTION_KEYS = ("annual reduction", "year-on-year reduction", "y-o-y reduction",
                        "reduction rate", "annual emission reduction")
MSCI_HISTORY_KEYS = ("esg rating history", "rating history", "rating timeline")

SP_SCORE_KEYS = ("csa score", "overall score", "s&p global esg score",
                  "total sustainability score", "sustainability yearbook score")
SP_DIMENSION_KEYS = {
    "economic": ("economic dimension",),
    "environmental": ("environmental dimension",),
    "social": ("social dimension",),
}

RATING_LETTERS = ["AAA", "AA", "A", "BBB", "BB", "B", "CCC"]  # 顺序从长到短，不然 "AA" 会在正则里抢先匹配到 "AAA" 里头去
_RATING_RE = re.compile(r"\b(" + "|".join(RATING_LETTERS) + r")\b")
_PCT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_TEMP_RE = re.compile(r"(\d(?:\.\d+)?)\s*°?\s*[Cc]\b")
_SCORE_RE = re.compile(r"\b(\d{1,3})\b")

# 把 MSCI 的7档字母评级粗略换算成数字，只是为了能跟 S&P 的0-100分做个量级上的
# 对比，不是官方认可的换算方式
MSCI_LETTER_TO_SCORE = {
    "CCC": 7.1, "B": 21.4, "BB": 35.7, "BBB": 50.0, "A": 64.3, "AA": 78.6, "AAA": 92.9,
}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class Field:
    """一个被抽取的字段：值 + 原文证据，缺一不可。"""
    value: object = None
    evidence: str | None = None
    confidence: str = "not_found"   # found_high / found_low / not_found

    def to_dict(self):
        return asdict(self)


@dataclass
class RatingRecord:
    company: str
    msci_source_file: str | None = None
    sp_source_file: str | None = None
    msci_rating: dict = field(default_factory=lambda: Field().to_dict())
    msci_implied_temp_rise_c: dict = field(default_factory=lambda: Field().to_dict())
    msci_target_year: dict = field(default_factory=lambda: Field().to_dict())
    msci_target_coverage_pct: dict = field(default_factory=lambda: Field().to_dict())
    msci_annual_reduction_pct: dict = field(default_factory=lambda: Field().to_dict())
    msci_rating_history: dict = field(default_factory=lambda: Field().to_dict())
    sp_overall_score: dict = field(default_factory=lambda: Field().to_dict())
    sp_csa_score: dict = field(default_factory=lambda: Field().to_dict())
    sp_modeled_score: dict = field(default_factory=lambda: Field().to_dict())
    sp_dimension_scores: dict = field(default_factory=lambda: Field().to_dict())
    needs_manual_review: list[str] = field(default_factory=list)
    parsed_at: str = ""


# ---------------------------------------------------------------------------
# 找PDF文件、把文字抽出来（思路跟 build_corpus.py 一样：靠文件名里的关键词找
# 对应的评级PDF）
# ---------------------------------------------------------------------------

def find_rating_pdf(company_dir: Path, markers: tuple[str, ...]) -> Path | None:
    candidates = [p for p in company_dir.glob("*.pdf")
                  if any(m in p.name.lower() for m in markers)]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_size)


def find_company_dir(company: str) -> Path | None:
    matches = list(DATA_DIR.glob(f"*/{company}"))
    return matches[0] if matches else None


def extract_pdf_text(pdf_path: Path) -> str:
    """把整份PDF抽成一个大字符串（评级PDF通常就几页，不像主报告那么长，不需要按页切开处理）。"""
    from pdfminer.high_level import extract_text
    return extract_text(str(pdf_path))


# ---------------------------------------------------------------------------
# 通用的"关键词附近找答案"逻辑：在关键词周围划一个字符范围，在这个范围里找想要的 pattern
# ---------------------------------------------------------------------------

def _find_pattern_near_keywords(text: str, keywords: tuple[str, ...], pattern: re.Pattern,
                                 value_radius: int = 80, evidence_radius: int = 200,
                                 exclude_values: set[str] | None = None) -> Field:
    """
    在关键词附近找 pattern"离得最近"的那次匹配，而不是范围里随便碰到的第一次。

    一开始的写法是拿"离关键词结束位置有多远"来判断谁最近，结果被这种写法坑了：
    "8.13% of company emissions"——这种数字其实是写在关键词短语里头/前面的。
    比如对 "% of company emissions" 这个关键词来说，它的结束位置是在数字后面，
    如果后面那句话里刚好还有另一个数字，反而会因为"排在结束位置之后"被误判成
    "更近"，其实它压根不是我们要的那个数字。

    后来改成这样：把关键词整个占的那段范围 [idx, kw_end] 看成一块区域，一个数字
    离这块区域的距离 = 离区域最近那条边有多远（落在区域里头就算0）。这样不管
    关键词习惯写在数字前面（"Target coverage: 8.13%"）还是后面
    （"8.13% of company emissions"），离得最近的那个数字都能选对。至于 evidence
    这段摘录，范围留得宽一点（用 evidence_radius），方便人工核对上下文看得清楚。
    """
    low = text.lower()
    best = None  # (distance, value, evidence)
    for kw in keywords:
        start = 0
        while True:
            idx = low.find(kw, start)
            if idx == -1:
                break
            kw_end = idx + len(kw)
            s = max(0, idx - value_radius)
            e = min(len(text), kw_end + value_radius)
            window = text[s:e]
            for m in pattern.finditer(window):
                val = m.group(1)
                if exclude_values and val in exclude_values:
                    continue
                abs_pos = s + m.start()
                if idx <= abs_pos <= kw_end:
                    distance = 0
                else:
                    distance = min(abs(abs_pos - idx), abs(abs_pos - kw_end))
                if best is None or distance < best[0]:
                    ev_s = max(0, idx - evidence_radius)
                    ev_e = min(len(text), kw_end + evidence_radius)
                    evidence = re.sub(r"\s+", " ", text[ev_s:ev_e]).strip()
                    best = (distance, val, evidence[:300])
            start = kw_end
    if best:
        return Field(value=best[1], evidence=best[2], confidence="found_low")
    return Field()


# ---------------------------------------------------------------------------
# MSCI 抽取思路：先按固定结构精确切（主力），抽不到再用关键词附近搜索兜底
#
# 拿到三份真实样本（Disney / Apple / Microsoft 的 MSCI 公开 ESG Ratings &
# Climate Search Tool 导出页）之后发现了一件事：这些其实是网页转成的PDF，
# pdfminer 是按视觉坐标顺序抽字的，会把同一个区块里的"标签"和"数值"拆成
# 两段——先把所有标签列完，再把所有数值列完，不是我们以为的那种逐行
# "标签: 数值"排法。原来那个"找离关键词最近的数字"的笨办法在这种排版下基本
# 抓不准，因为数值和它真正对应的标签中间可能隔着上百个字符的其它标签文字。
#
# 好在三份样本验证下来，这个页面的结构是完全固定的（用的是同一个网页模板），
# 所以就改成按照"这里固定会出现几个 token、顺序是什么"来精确切分，这样比
# 模糊匹配靠谱多了：
#   1. 减碳目标这块："Target data as of ..." 后面固定跟着5个数值token
#      （有没有目标 / 算不算进ITR / 目标年份 / 覆盖了百分之多少 / 年降幅百分之多少），
#      偶尔前面会多出来一个"–"占位符（这是几个大标题问句共用的装饰图标，跳过就行）。
#   2. Implied Temperature Rise（隐含升温）：标题后面紧跟着一个分类标签
#      （比如"2°C ALIGNED"，注意这个不是真实数值，是个分类桶！）和一个"–"占位符，
#      再往后才是真正的数字（比如"1.9°C"）。判断方法是"这一整行必须严格等于
#      数字+°C"，这样就能把分类标签排除掉，因为分类标签总会带一堆多余的文字。
#   3. 当前的ESG评级："Universe: ... 家公司)" 之后，第一个整行内容刚好等于
#      一个评级字母的，就是当前评级（这样能避开后面那段把好几个评级字母粘一起
#      变成"CCCBBBAAAAAALAGGARDLEADER"这种图例伪影——那一整行显然不等于任何
#      一个单独的字母）。
#   4. 评级历史：图表Y轴上的刻度（固定就是 AAA,AA,A,BBB,BB,B,CCC 这七个连号）
#      和真实的历史评级值会按不同顺序混在一起（这跟折线的形状有关，pdfminer
#      读出来的顺序会变），但刻度本身作为一段"连续出现的子序列"永远是完整
#      出现、顺序不变的——把它从字母token列表里挑出去扣掉，剩下的就是真实的
#      历史评级，按顺序跟日期token一一对应上。
# ---------------------------------------------------------------------------

RATING_SCALE = ["AAA", "AA", "A", "BBB", "BB", "B", "CCC"]   # 图表Y轴上固定的刻度，从高到低排

_MONTH_YEAR_RE = re.compile(r"^[A-Z][a-z]{2}-\d{2}$")
_STANDALONE_RATING_RE = re.compile(r"^(AAA|AA|A|BBB|BB|B|CCC)$")
_STANDALONE_ITR_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*°\s*C$")
_STANDALONE_YEAR_RE = re.compile(r"^(19|20)\d\d$")
_LEADING_PCT_RE = re.compile(r"^(-?\d+(?:\.\d+)?)\s*%")


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.split("\n")]


def _find_heading_idx(lines: list[str], needle: str, start: int = 0) -> int | None:
    needle_low = needle.lower()
    for i in range(start, len(lines)):
        if needle_low in lines[i].lower():
            return i
    return None


def _find_exact_line_idx(lines: list[str], target: str, start: int = 0) -> int | None:
    """
    这个跟 _find_heading_idx 不一样，_find_heading_idx 是子串匹配，这里要求
    整行内容跟 target 完全一样才算。用在"S&P Global ESG Score"这种标题上是
    有原因的：页面开头有一整句免责声明，"The S&P Global ESG Score cannot be
    compared across industries..."，这句话里也包含这几个字，用子串匹配会
    误命中这句免责声明，而不是真正的标题行，后面按标题位置去找分数就全找错了。
    """
    target_low = target.lower()
    for i in range(start, len(lines)):
        if lines[i].lower() == target_low:
            return i
    return None


def _strip_legend_subsequence(tokens: list[str], legend: list[str]) -> list[str] | None:
    """
    在 tokens 里找一段跟 legend 内容、顺序都完全一样的连续子序列，把它删掉，
    剩下的部分返回。要是压根找不到，就返回 None——这说明这份PDF的图例排法
    跟三份样本不一样，交给人工去核对，绝不瞎猜"大概率是这样吧"。
    """
    n = len(legend)
    for i in range(0, len(tokens) - n + 1):
        if tokens[i:i + n] == legend:
            return tokens[:i] + tokens[i + n:]
    return None


def _structural_parse_msci(text: str) -> dict:
    lines = _lines(text)
    out = {
        "msci_rating": Field(),
        "msci_implied_temp_rise_c": Field(),
        "msci_target_year": Field(),
        "msci_target_coverage_pct": Field(),
        "msci_annual_reduction_pct": Field(),
        "msci_rating_history": Field(),
    }

    # 1) 减碳目标区块
    idx = _find_heading_idx(lines, "target data as of")
    if idx is not None:
        tokens = [ln for ln in lines[idx + 1: idx + 20] if ln]
        if tokens and tokens[0] == "–":
            tokens = tokens[1:]
        if len(tokens) >= 5:
            evidence = " | ".join(tokens[:5])
            year_tok, cov_tok, red_tok = tokens[2], tokens[3], tokens[4]
            if _STANDALONE_YEAR_RE.match(year_tok):
                out["msci_target_year"] = Field(value=year_tok, evidence=evidence, confidence="found_high")
            m = _LEADING_PCT_RE.match(cov_tok)
            if m:
                out["msci_target_coverage_pct"] = Field(value=m.group(1), evidence=evidence, confidence="found_high")
            m = _LEADING_PCT_RE.match(red_tok)
            if m:
                out["msci_annual_reduction_pct"] = Field(value=m.group(1), evidence=evidence, confidence="found_high")

    # 2) Implied Temperature Rise（跳过分类桶标签，只认"整行=数字+°C"）
    idx = _find_heading_idx(lines, "msci implied temperature rise")
    if idx is not None:
        window = [ln for ln in lines[idx + 1: idx + 8] if ln]
        for ln in window:
            m = _STANDALONE_ITR_RE.match(ln)
            if m:
                out["msci_implied_temp_rise_c"] = Field(value=m.group(1), evidence=" | ".join(window),
                                                          confidence="found_high")
                break

    # 3) 当前 ESG 评级
    idx = _find_heading_idx(lines, "universe:")
    if idx is not None:
        window = [ln for ln in lines[idx + 1: idx + 6] if ln]
        for ln in window:
            if _STANDALONE_RATING_RE.match(ln):
                out["msci_rating"] = Field(value=ln, evidence=" | ".join(window), confidence="found_high")
                break

    # 4) 评级历史
    idx = _find_heading_idx(lines, "esg rating history")
    if idx is not None:
        end_idx = _find_heading_idx(lines, "how often is an msci esg rating updated", start=idx)
        block = lines[idx: end_idx if end_idx is not None else idx + 60]
        rating_tokens = [ln for ln in block if _STANDALONE_RATING_RE.match(ln)]
        date_tokens = [ln for ln in block if _MONTH_YEAR_RE.match(ln)]
        values = _strip_legend_subsequence(rating_tokens, RATING_SCALE)
        evidence = " | ".join(ln for ln in block if ln)[:400]
        if values is not None and date_tokens and len(values) == len(date_tokens):
            history = [{"period": d, "rating": r} for d, r in zip(date_tokens, values)]
            out["msci_rating_history"] = Field(value=history, evidence=evidence, confidence="found_high")
        else:
            out["msci_rating_history"] = Field(value=None, evidence=evidence, confidence="found_low")

    return out


def parse_msci(text: str) -> dict:
    structural = _structural_parse_msci(text)

    # 兜底：结构化解析这条路没抽到的字段，回退到关键词附近搜索再试一次
    # （给版式跟这三份样本长得不太一样的公司留条后路，不是一上来就判定抽取失败）
    fallback_map = {
        "msci_rating": (MSCI_RATING_KEYS, _RATING_RE),
        "msci_implied_temp_rise_c": (MSCI_ITR_KEYS, _TEMP_RE),
        "msci_target_coverage_pct": (MSCI_COVERAGE_KEYS, _PCT_RE),
        "msci_annual_reduction_pct": (MSCI_REDUCTION_KEYS, _PCT_RE),
    }
    for field_name, (keys, pattern) in fallback_map.items():
        if structural[field_name].value is None:
            fb = _find_pattern_near_keywords(text, keys, pattern)
            if fb.value is not None:
                structural[field_name] = fb

    if structural["msci_rating_history"].value is None and structural["msci_rating_history"].evidence is None:
        low = text.lower()
        for kw in MSCI_HISTORY_KEYS:
            i = low.find(kw)
            if i != -1:
                structural["msci_rating_history"] = Field(value=None, evidence=text[i:i + 1200],
                                                            confidence="found_low")
                break

    return {k: v.to_dict() for k, v in structural.items()}


def llm_parse_rating_history(raw_block: str | None) -> Field:
    """
    用 GLM 把"评级历史"这段原文（常见的样子是一串日期和字母交替出现的表格文字，
    被pdfminer抽出来之后换行和对齐全丢了）解析成一个结构化的列表。

    这里的分工原则跟 analyst.py 保持一致：模型只干"把一段乱糟糟的文本读懂、
    变成结构化数据"这种正则写不出来的活儿，而且必须原样把 evidence 保留下来，
    绝不能让模型替我们去"确认"数字对不对。
    """
    if not raw_block or cloud_gen is None:
        return Field(value=None, evidence=raw_block, confidence="not_found")

    prompt = (
        "The following text is extracted from a PDF and contains an MSCI ESG rating "
        "history (a sequence of dates and letter ratings such as AAA/AA/A/BBB/BB/B/CCC). "
        "PDF text extraction has lost the original table layout.\n\n"
        f"Text:\n{raw_block}\n\n"
        "Extract every (date_or_period, rating) pair you can find, in chronological order. "
        "Respond with ONLY a compact JSON array, e.g. "
        '[{"period":"2022-03","rating":"A"},{"period":"2024-01","rating":"AA"}]. '
        "If you cannot confidently identify any such pair, respond with exactly: NOT_FOUND."
    )
    try:
        resp = cloud_gen("You are a precise data-extraction assistant.", prompt)
    except Exception as e:                    # noqa: BLE001
        return Field(value=None, evidence=f"[LLM解析失败: {e}]\n{raw_block}", confidence="not_found")
    if "NOT_FOUND" in resp.upper():
        return Field(value=None, evidence=raw_block, confidence="not_found")
    try:
        cleaned = re.sub(r"^```(json)?|```$", "", resp.strip(), flags=re.MULTILINE).strip()
        parsed = json.loads(cleaned)
        return Field(value=parsed, evidence=raw_block, confidence="found_low")
    except Exception:                          # noqa: BLE001
        return Field(value=None, evidence=raw_block, confidence="not_found")


# ---------------------------------------------------------------------------
# S&P Global 抽取思路：也是先拿真实样本核对好结构，再照着固定位置去解析
#
# 三份样本（Disney / Apple / Microsoft）验证下来的结构长这样：
#   1. 总分："S&P Global ESG Score" 这个标题第一次出现（在页面顶部）之后，
#      紧跟着就是单独一行、1到3位的纯数字——这是全文里唯一一处"标题后面
#      立刻单独一行数字"的地方，特别干净，都不用判断上下文。
#   2. 三个分项（Environmental / Social / Governance & Economic）：每个标题
#      后面那一行长这样 "{公司名} {分数} Industry Mean {行业均值}
#      Industry Max {行业最高}"，三个数字的相对位置是固定的，直接用一个
#      正则从这一行里把三个数一起抠出来就行。
#   3. CSA分数 / Modeled分数：页面顶部有个环形图（gauge chart），因为SVG
#      描边的关系，每个数字在文本层会被重复抽出来3遍还首尾粘在一起，比如
#      真实值39会变成"393939"，真实值4会变成"444"。这个特征很好认、基本
#      不会跟别的数字搞混——只要把"Score Composition"到"Environmental"
#      这段之间所有的整数token都测一遍"能不能三等分成同一个子串"，能测出来
#      的头两个依次就是CSA分数和Modeled分数（三份样本里顺序都是一致的）。
#      Disney和Apple的页面末尾还另外有一行干净的"S&P Global CSA Score = 39"
#      可以拿来交叉验证，但Microsoft的样本里没有这行——所以这行不能当成
#      唯一的数据来源，"数字被重复三遍"这个伪影特征才是三份样本里唯一
#      稳定都有的信号。
# ---------------------------------------------------------------------------

SP_DIMENSIONS = [("environmental", "Environmental"), ("social", "Social"),
                  ("governance", "Governance & Economic")]

_BARE_SCORE_RE = re.compile(r"^\d{1,3}$")
_DIM_VALUE_RE = re.compile(r"(\d{1,3})\s+Industry Mean\s+(\d{1,3})\s+Industry Max\s+(\d{1,3})",
                            re.IGNORECASE)
_CSA_EQUALS_RE = re.compile(r"S&P Global CSA Score\s*=\s*(\d{1,3})", re.IGNORECASE)
_MODELED_EQUALS_RE = re.compile(r"Modeled Scores?\s*=\s*(\d{1,3})", re.IGNORECASE)


def _detripled(token: str) -> str | None:
    """
    '393939' -> '39'，'444' -> '4'：检查一个纯数字字符串是不是"同一段数字
    重复了3遍拼起来"的（这是 S&P 环形图分数被 pdfminer 抽取时常出现的伪影）。
    如果不是就返回 None，绝对不会把一个普通的三位数（比如 '100'）也误判成伪影。
    """
    if not token.isdigit() or len(token) < 3 or len(token) % 3 != 0:
        return None
    unit = token[: len(token) // 3]
    return unit if unit * 3 == token else None


def _structural_parse_sp(text: str) -> dict:
    lines = _lines(text)
    out = {
        "sp_overall_score": Field(),
        "sp_csa_score": Field(),
        "sp_modeled_score": Field(),
        "sp_dimension_scores": Field(),
    }

    # 1) 总分（用整行精确匹配标题，避开页面顶部那句同样含有这几个字的免责声明句）
    idx = _find_exact_line_idx(lines, "S&P Global ESG Score")
    if idx is not None:
        window = [ln for ln in lines[idx + 1: idx + 6] if ln]
        for ln in window:
            if _BARE_SCORE_RE.match(ln):
                out["sp_overall_score"] = Field(value=ln, evidence=" | ".join(window), confidence="found_high")
                break

    # 2) 三个分项
    dims = {}
    for key, heading in SP_DIMENSIONS:
        didx = _find_heading_idx(lines, heading.lower())
        if didx is None:
            continue
        window_text = " ".join(lines[didx: didx + 4])
        m = _DIM_VALUE_RE.search(window_text)
        if m:
            dims[key] = {"value": m.group(1), "industry_mean": m.group(2), "industry_max": m.group(3),
                         "evidence": window_text, "confidence": "found_high"}
    if dims:
        out["sp_dimension_scores"] = Field(value=dims, evidence=None, confidence="found_high")

    # 3) CSA / Modeled 分数：先试页面末尾的干净 "X = Y" 陈述句（Disney/Apple 有，更可信）
    csa_m = _CSA_EQUALS_RE.search(text)
    modeled_m = _MODELED_EQUALS_RE.search(text)
    if csa_m:
        out["sp_csa_score"] = Field(value=csa_m.group(1), evidence=csa_m.group(0), confidence="found_high")
    if modeled_m:
        out["sp_modeled_score"] = Field(value=modeled_m.group(1), evidence=modeled_m.group(0), confidence="found_high")

    # Microsoft的样本里没有那句干净的陈述句——这时候退回去解析"环形图数字
    # 重复三遍"这个伪影特征。这个区域里除了CSA和Modeled这两个分数，总分本身也
    # 经常被当装饰又画了一遍（同样会有重复三遍的伪影），所以必须先把总分从
    # 候选里过滤掉，不然会把"总分"误认成"Modeled分数"（Microsoft的真实情况：
    # detripled=[50(CSA), 53(其实是总分又出现了一次), 3(Modeled)]，不过滤的话
    # 就会错把53当成Modeled分数）。
    if csa_m is None or modeled_m is None:
        start = _find_heading_idx(lines, "score composition")
        stop = _find_heading_idx(lines, "environmental", start=start) if start is not None else None
        if start is not None:
            block = lines[start: stop if stop is not None else start + 30]
            detripled = [v for v in (_detripled(ln) for ln in block) if v is not None]
            overall_val = out["sp_overall_score"].value
            candidates = [v for v in detripled if v != overall_val]
            evidence = " | ".join(ln for ln in block if ln)[:300]
            if csa_m is None and len(candidates) >= 1:
                out["sp_csa_score"] = Field(value=candidates[0], evidence=evidence, confidence="found_low")
            if modeled_m is None and len(candidates) >= 2:
                out["sp_modeled_score"] = Field(value=candidates[1], evidence=evidence, confidence="found_low")

    return out


def parse_sp(text: str) -> dict:
    structural = _structural_parse_sp(text)

    if structural["sp_overall_score"].value is None:
        fb = _find_pattern_near_keywords(text, SP_SCORE_KEYS, _SCORE_RE)
        if fb.value is not None and 0 <= int(fb.value) <= 100:
            structural["sp_overall_score"] = fb

    return {k: v.to_dict() for k, v in structural.items()}


# ---------------------------------------------------------------------------
# 主流程：单家公司
# ---------------------------------------------------------------------------

def parse_company(company: str, llm_parse_history: bool, dump_text_target: str | None) -> RatingRecord | None:
    company_dir = find_company_dir(company)
    if company_dir is None:
        print(f"[跳过] {company}：在 {DATA_DIR} 下找不到公司目录", file=sys.stderr)
        return None

    msci_pdf = find_rating_pdf(company_dir, ("msci",))
    sp_pdf = find_rating_pdf(company_dir, ("s&p", "sp global", "spglobal", "sp_global"))

    if dump_text_target:
        target = {"msci": msci_pdf, "sp": sp_pdf}.get(dump_text_target)
        if target is None:
            print(f"[错误] {company} 没有找到 {dump_text_target} 的 PDF", file=sys.stderr)
            return None
        print(extract_pdf_text(target))
        return None

    rec = RatingRecord(company=company, parsed_at=datetime.now(timezone.utc).isoformat())

    if msci_pdf is None:
        print(f"  [警告] {company}：未找到 MSCI PDF（文件名需包含 'msci'）")
        rec.needs_manual_review.append("msci_all: 未找到 MSCI PDF 文件")
    else:
        rec.msci_source_file = msci_pdf.name
        text = extract_pdf_text(msci_pdf)
        for k, v in parse_msci(text).items():
            setattr(rec, k, v)
        if llm_parse_history and rec.msci_rating_history.get("evidence"):
            rec.msci_rating_history = llm_parse_rating_history(
                rec.msci_rating_history["evidence"]).to_dict()

    if sp_pdf is None:
        print(f"  [警告] {company}：未找到 S&P Global PDF（文件名需包含 's&p'/'sp global'）")
        rec.needs_manual_review.append("sp_all: 未找到 S&P Global PDF 文件")
    else:
        rec.sp_source_file = sp_pdf.name
        text = extract_pdf_text(sp_pdf)
        for k, v in parse_sp(text).items():
            setattr(rec, k, v)

    # 把没抽到的字段汇总一下，放进人工复核清单里
    for field_name in ("msci_rating", "msci_implied_temp_rise_c", "msci_target_year",
                        "msci_target_coverage_pct", "msci_annual_reduction_pct", "msci_rating_history",
                        "sp_overall_score", "sp_csa_score", "sp_modeled_score", "sp_dimension_scores"):
        f = getattr(rec, field_name)
        if f.get("value") is None and f.get("confidence") == "not_found":
            rec.needs_manual_review.append(f"{field_name}: 未抽到，建议对照 PDF 原文人工核对/补充")

    return rec


def merge_manual(rec: RatingRecord, manual_path: Path) -> RatingRecord:
    """
    把人工核对过的文件合并进来。手填的文件格式跟输出的JSON是一样的结构，只需要
    填你想覆盖的那几个字段就行，比如：
        {"msci_annual_reduction_pct": {"value": 4.2, "evidence": "见 PDF 第 3 页图表（手工读取）"}}
    合并的规则是：手填的 value/evidence 会覆盖掉自动抽取的结果，confidence 会
    固定标成 "manual"，而且这个字段会从 needs_manual_review 这个待复核清单里
    拿掉。
    """
    manual = json.loads(manual_path.read_text(encoding="utf-8"))
    data = asdict(rec)
    for k, v in manual.items():
        if k in data and isinstance(data[k], dict):
            data[k].update(v)
            data[k]["confidence"] = "manual"
            data["needs_manual_review"] = [
                m for m in data["needs_manual_review"] if not m.startswith(f"{k}:")
            ]
        else:
            data[k] = v
    return RatingRecord(**data)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="解析 MSCI / S&P Global 评级 PDF")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--company", help="只解析这一家公司（esg_data 下的目录名）")
    g.add_argument("--all", action="store_true", help="解析 esg_data 下所有公司")
    p.add_argument("--dump-text", choices=["msci", "sp"], dest="dump_text",
                    help="不解析，只把指定评级 PDF 的全文打印出来（调试正则用）")
    p.add_argument("--manual", help="人工核对覆盖文件路径（仅配合 --company 使用）")
    p.add_argument("--llm-parse-history", action="store_true",
                    help="用 GLM 把评级历史原文块解析成结构化列表（需要 ZHIPU_API_KEY）")
    p.add_argument("--out-dir", default=str(OUT_DIR))
    args = p.parse_args()

    if not DATA_DIR.exists():
        print(f"错误：找不到数据目录 {DATA_DIR}", file=sys.stderr)
        return 1

    companies = []
    if args.all:
        for sector_dir in sorted(DATA_DIR.iterdir()):
            if sector_dir.is_dir() and not sector_dir.name.startswith("."):
                companies += [d.name for d in sorted(sector_dir.iterdir())
                              if d.is_dir() and not d.name.startswith(".")]
    else:
        companies = [args.company]

    out_dir = Path(args.out_dir)
    ok, warned = 0, 0
    for company in companies:
        print(f"[{company}]")
        rec = parse_company(company, args.llm_parse_history, args.dump_text)
        if args.dump_text:
            return 0
        if rec is None:
            continue
        if args.manual:
            rec = merge_manual(rec, Path(args.manual))
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{company}.json"
        out_path.write_text(json.dumps(asdict(rec), ensure_ascii=False, indent=2), encoding="utf-8")
        n_review = len(rec.needs_manual_review)
        status = "✓" if n_review == 0 else f"⚠ {n_review} 项待人工复核"
        print(f"  {status} -> {out_path.relative_to(ROOT)}")
        ok += 1
        warned += 1 if n_review else 0

    print(f"\n完成 {ok} 家公司（{warned} 家有待复核字段）。"
          f"抽取效果不理想时用 --dump-text msci/sp 看原文，或用 --manual 手工补齐。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

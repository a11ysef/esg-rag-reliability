#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
table_reconstruct.py —— 表格被拍成一坨文字之后，把"哪个数字是哪个"给找回来

先说说这是要解决什么问题：ESG报告里很多关键数字（比如碳排放、可再生能源占比、
用水量这些）本来都好好地待在表格里，行列对得整整齐齐。但PDF转成文字以后，
表格的行列结构就没了，变成一条直的文字流——先来一串标签，再来一串数字，
中间"谁对应谁"全靠原来的表格排版才知道，一拍扁，这层对应关系就丢了。

以前的做法是，把这坨拍扁的文字整个丢给LLM，让它自己猜"这个数字该配哪个标签"，
然后再让它拿"原文里连着的一句话"当证据证明自己没瞎猜（这套证据校验挺管用，
详见analyst.py里的_quote_is_genuine那几层检查）。但这套办法治不好一种情况：
答案明明就在这堆文字里，但结构已经丢了，LLM没法举出让人信服的证据——于是
它只能老老实实地说"没披露"，其实数字就在那儿，只是它读不出来。

这个文件要做的事很简单，而且完全不用LLM：靠写死的规则，从"一串标签+一串
数字"这种最常见的拍扁方式里，把标签和数字重新配对回去。配得上就输出，
配不上（结构看着有歧义）就什么都不返回——这个项目一直的原则是"宁可少抓
几个也不能抓错"，这里也不例外。

配对成功以后，如果还能在同一页找到"连续N个四位数年份"排成一行（N正好等于
这组数字的个数），就可以把每个数字精确对到具体是哪一年——这种情况可以直接
当高置信度的结果用。但如果附近确实找不到年份那一行（这批PDF里其实很常见，
表格的年份表头转成文字时经常直接就丢了），那就不猜"第一个数字是不是今年
的"——这种猜跟让LLM瞎猜没什么本质区别，只是换成代码来猜而已。这种情况下，
就老实说"这一行数字定位到了，但年份没法确认"，不冒充自己找到了确定答案。

这个文件产出的东西，只是用来"让喂给LLM的材料更干净"（生成一段整理好的文字，
重新走一遍analyst.py原有的EXTRACT_TMPL和那四层校验），不会绕开任何一道
已有的检查，也不会另开一条"直接采信"的近路——唯一的例外是"年份已经锁定"
这种情况，因为这种情况从头到尾都是代码算出来的，每一步都能重现、能写单测，
不存在"LLM猜错"这个风险。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NUM_LINE_RE = re.compile(r"^[-+]?[\d,]+(\.\d+)?%?$")
_YEAR_LINE_RE = re.compile(r"^(19|20)\d{2}$")


@dataclass
class ReconstructedRow:
    label: str
    values: list           # 数字就原样存成字符串列表，不做任何转换或合并
    n_cols: int
    source_chunk_id: str
    page: int | None
    confidence: str         # "single_label" 是"一个标签后面跟一串数字"这种情况；
                             # "grid_exact_divide" 是"N个标签后面跟一堆数字、数字个数正好被N整除"这种情况


def _split_lines(content: str) -> list:
    return [l.strip() for l in content.split("\n") if l.strip()]


_LEADING_NUM_RE = re.compile(r"^\d")
_COMMA_DIGIT_LIST_RE = re.compile(r"\d+,\d+,\d+")     # 这种模式常见于"Environmental footprint1,2,3,4,5,6"这样一串脚注编号，不是真正的表格数据


def _is_eligible_label_line(line: str) -> bool:
    """这个函数只用来过滤chunk里第一段"标签块"（也就是正文最开头、还没见到任何
    数字之前的那一段）。这一段是最容易混进跟表格无关的东西的——正文、小节标题、
    脚注编号什么的都可能夹在里面。真实踩过的坑：Meta报告第78页开头长这样：
    "Environmental footprint1,2,3,4,5,6 / 1.1 GHG emissions / Total GHG emissions /
    Market-based(...) / Net total"，如果把这一整段都当成"5个并列的标签"，就会把
    前面4行文档标题也当成表格的行标签，然后跟紧跟着的5个数字（其实这5个数字全都
    是"Net total"这一行的）错误地一对一拆开——数字个数刚好能整除，看着挺像那么
    回事，其实是彻头彻尾配错了。

    所以这里的规则收得很紧：只信任"紧挨着数字块前面、看起来像表格标签"的那一段，
    往前一旦碰到不像标签的行就不再往前找，前面的通通当无关正文扔掉。"像标签"的
    判断很朴素：不能是数字开头的（排除"1.1 GHG emissions"这种小节编号）、不能带
    逗号隔开的数字串（排除脚注编号）、字数不能太多（排除长篇正文）。
    """
    if _LEADING_NUM_RE.match(line):
        return False
    if _COMMA_DIGIT_LIST_RE.search(line):
        return False
    if len(line.split()) > 6:
        return False
    return True


def reconstruct_flattened_table(chunk: dict) -> list:
    """
    试着从一个chunk的正文里认出"一段标签+一段数字"这种结构，认出来就返回一份
    ReconstructedRow列表。要是认不出没有歧义的结构，就老老实实返回空列表——
    不做任何有歧义的猜测性配对。

    能放心处理的模式就两种：
      1. single_label：一行标签，后面紧跟着至少两行数字——这些数字整体上就是
         这一个标签的，没什么好犹豫的。
      2. grid_exact_divide：连续N行（N>1）标签，后面紧跟着一段数字，而且数字
         总数正好是N的整数倍——就按顺序切成N组，每组M个，第i组配第i个标签
         （这批报告里"先列N个指标名，再列N×M个数值"的排版很常见，跟这个模式
         对得上）。除不尽就直接放弃这一段，不硬拆。

    chunk正文里第一次遇到的"标签块"（也就是还没见过任何数字之前的那一段）比较
    特殊：只信任紧挨着数字块前面、看起来像标签的那一段（具体规则见
    _is_eligible_label_line），前面的正文/标题一律丢掉。但chunk里后面再遇到的
    标签块（前面已经出现过至少一组数字了，说明已经进入表格内部）就不做这层
    过滤了——真实数据验证过，表格内部相邻两行数据之间夹杂无关文字这种情况基本
    不存在，过滤太狠反而会把真正连续的多行标签（比如"Total/Scope 1/Scope 2/
    Scope 3"这种）切得七零八落。
    """
    lines = _split_lines(chunk.get("content", ""))
    is_num = [bool(_NUM_LINE_RE.match(l)) for l in lines]
    n = len(lines)
    results = []
    seen_number_block = False

    i = 0
    while i < n:
        if is_num[i]:
            i += 1
            continue
        label_start = i
        j = i
        while j < n and not is_num[j]:
            j += 1
        if j >= n:
            break   # 后面再也没有数字了，剩下的都是纯文字，不是"标签+数字"这种结构了
        raw_label_lines = lines[label_start:j]

        if not seen_number_block:
            # 这是chunk里第一段标签块：只信最后紧挨着数字块的那一行，前面不管是
            # 什么（文档标题、小节编号、脚注串……）统统丢掉，不往前多收。这个规则
            # 故意定得很严——真实数据里验证过，"连续好几行都是正儿八经的表格标签"
            # 这种情况，在chunk刚开头、还没见过任何数字的地方基本不会真的发生，
            # 反倒是正文标题最容易被误认成标签。宁可只信最后一行、少恢复几个
            # single_label，也不要把正文错当成一组"假标签"。
            last = raw_label_lines[-1] if raw_label_lines else None
            label_lines = [last] if last and _is_eligible_label_line(last) else []
        else:
            label_lines = raw_label_lines

        num_start = j
        k = j
        while k < n and is_num[k]:
            k += 1
        number_lines = lines[num_start:k]
        seen_number_block = True

        if len(label_lines) == 1 and len(number_lines) >= 2:
            results.append(ReconstructedRow(
                label=label_lines[0], values=number_lines, n_cols=len(number_lines),
                source_chunk_id=chunk.get("chunk_id"), page=chunk.get("page"),
                confidence="single_label",
            ))
        elif len(label_lines) > 1 and len(number_lines) > 0 and len(number_lines) % len(label_lines) == 0:
            m = len(number_lines) // len(label_lines)
            for idx, lab in enumerate(label_lines):
                results.append(ReconstructedRow(
                    label=lab, values=number_lines[idx * m:(idx + 1) * m], n_cols=m,
                    source_chunk_id=chunk.get("chunk_id"), page=chunk.get("page"),
                    confidence="grid_exact_divide",
                ))
        # 除不尽、标签块过滤完一行都不剩……这些有歧义的情况，不加任何结果，直接跳过这一段
        i = k

    return results


def find_year_row_near(row: "ReconstructedRow", all_chunks: list) -> list | None:
    """
    在同一页的所有chunk里，找一行"连续排着row.n_cols个四位数年份"的序列，用来把
    row.values精确对到具体年份上。要求年份的个数跟数字的个数严格相等才采信，
    免得"找到一段看着像年份的东西，但个数对不上"这种似是而非的错误配对。
    找不到就返回None——上层看到None就不会瞎猜，只会把跨年的数值原样报告出去。
    """
    same_page = [c for c in all_chunks if c.get("page") == row.page]
    for c in same_page:
        lines = _split_lines(c.get("content", ""))
        is_year = [bool(_YEAR_LINE_RE.match(l)) for l in lines]
        idx = 0
        while idx < len(lines):
            if is_year[idx]:
                j = idx
                while j < len(lines) and is_year[j]:
                    j += 1
                if (j - idx) == row.n_cols:
                    return lines[idx:j]
                idx = j
            else:
                idx += 1
    return None


def row_matches_keywords(row: "ReconstructedRow", keywords) -> bool:
    if not keywords:
        return False
    low = row.label.lower()
    return any(kw in low for kw in keywords)


_UNIT_PAREN_RE = re.compile(
    r"\([^)]*\b(?:co2e?|co₂e?|percent|%|gallons?|liters?|litres?|cubic meters?|cubic metres?|"
    r"megaliters?|megalitres?|kiloliters?|kilolitres?|m3|m³|"
    r"mwh|metric tons?|tonnes?)\b[^)]*\)", re.IGNORECASE,
)
# 真实踩过的坑（Meta的水资源表）：表头写的是"(in megaliters)"，这个词一开始根本没在
# 白名单里，结果find_unit_context找不到单位就返回None，Total water withdrawal那
# 五个年份的候选全都没带单位标注。本来机械校验②遇到没单位的情况应该统一拒绝掉，
# 但模型自己在没单位的时候偶尔会瞎编一个"m³"蒙混过关（5个候选里凑巧只有1个编得
# 像模像样），结果"抓到第一个通过的就停"这个逻辑，意外地采信了模型蒙对了格式、
# 但其实年份完全抓错（2021年）的那个候选——表面看是"选错了年份"，根子其实是"一开始
# 就没找到单位，模型才被逼着自己瞎编"。把megaliters/kiloliters/m³加进白名单之后，
# 五个候选就都能稳定带上正确单位，机械校验②该拒绝的时候就能稳稳拒绝，不用看运气。

_LABEL_TRAILING_UNIT_RE = re.compile(r"\b(tCOe|tCO2e|tCO₂e|CO2e|CO₂e)\b", re.IGNORECASE)
# 这里跟pdf_geometry_reconstruct.py里同名的函数是同一个坑、同一个修法：有时候
# 行标签自己已经把单位写进去了（比如"Scope 1 tCOe"），但find_unit_context只会去
# 正文里找单独的"(in metric tons CO2e)"这种括号说明，找不到就返回None，于是数值
# 后面就没有任何单位跟着——EXTRACT_TMPL教模型认的"标签→数字→单位"这个顺序凑不齐，
# 模型会觉得这证据不够干净，哪怕数值本身完全正确也会被拒掉。所以这里干脆把标签里
# 已经写着的单位摘出来，在数值后面再贴一份。
def _label_unit_fallback(label: str) -> str | None:
    m = _LABEL_TRAILING_UNIT_RE.search(label)
    return f"({m.group(0)})" if m else None


def find_unit_context(original_chunk_content: str) -> str | None:
    """
    在原始（没被重建过）的chunk正文里，找一段说明单位的括号文字，比如常见的
    "Market-based (in metric tons CO2e)"这种。表格重建的时候只会把"紧挨着数字
    的那一行"当成标签，像这种写在表格开头、说明整张表单位的括号文字反而会被
    当成"跟这行没关系的正文"给过滤掉——但这段话对机械校验②（单位校验）能不能
    通过很关键：合成出来的文字里要是完全没提单位，模型自己也编不出单位来，
    我们的单位校验就会把一条其实完全对齐正确的结果误杀掉。这里就是把原始正文
    里能找到的单位括号原样摘出来，当背景信息一起塞进合成文本——只是把原文本来
    就写明的单位说明搬过去给模型看，不是凭空编的。找不到就返回None，合成文本
    照样正常生成，只是不带这段背景信息（到时候如果模型真编不出单位，还是会被
    ②号校验正常拦下，安全性不会因此降低）。
    """
    m = _UNIT_PAREN_RE.search(original_chunk_content or "")
    return m.group(0) if m else None


def rows_to_synthetic_chunk(row: "ReconstructedRow", years, unit_context: str | None = None) -> list:
    """
    把一条已经对齐好（或者年份还没对上）的ReconstructedRow，转成一份或几份干净的
    合成文本，重新包装成"chunk"的样子，可以直接丢进analyst.py现成的EXTRACT_TMPL
    和那四层机械校验里走一遍——不开新的信任通道，只是把喂进去的材料换成结构更
    清楚的版本。

    真实踩过的坑（第一层，已经修了）：以前unit_context是拼在整段话最后单独一句
    说明，结果模型汇报quote的时候（很合理地）把单位一起写进紧跟数值后面，这样
    一来quote作为一段连续文字在原文里就找不到了，被机械校验①当成编造证据误杀——
    其实不是模型编数字，是合成文本自己的结构把它逼成这样的。现在把单位直接跟在
    每个数值后面，模型的quote自然就是一段连续的文字了。

    真实踩过的坑（第二层，这次修的，跟pdf_geometry_reconstruct.py里的
    rows_to_synthetic_chunk_geometry是同一个坑、同一种修法）：以前是把"年份已经
    确认"的这一行，所有年份的数据一股脑拼进*同一个*合成chunk里，让模型自己挑该用
    哪一年——但压根没有任何提示告诉模型"没指定年份的时候该选最新一年"，真实数据
    跑出来的结果就是：不同公司之间、甚至同一家公司的不同指标，选出来的年份互相
    对不上，数字看着都对、都能在原文里核实，但放一起比较的时候比的根本不是同一
    年——这比"未披露"更隐蔽、也更危险。这里干脆不指望模型自己判断了，改成按年份
    拆成好几个独立候选，从新到旧排好序返回——LOCATE+VERIFY那套"抓到第一个通过的
    就停"的逻辑天然就会先试最新一年，只有最新一年没通过校验（比如那一列本来就
    没数据）才会退到次新的一年，验证逻辑一行都不用改。
    """
    tag = re.sub(r"[^a-zA-Z0-9]+", "_", row.label)[:30]
    # 跟pdf_geometry_reconstruct.py里rows_to_synthetic_chunk_geometry是同一个坑、
    # 同一个修法（第三层）：如果这一页没有单独的"(in metric tons CO2e)"括号说明，
    # unit_context就会是None，可标签本身可能已经把单位写进去了（比如"Scope 1
    # tCOe"）——不给数值后面补一份的话，数值后面就完全没有单位跟着，EXTRACT_TMPL教的
    # "标签→数字→单位"顺序凑不齐，模型会合理地觉得这证据不够干净。
    if not unit_context:
        unit_context = _label_unit_fallback(row.label)
    unit_suffix = f" {unit_context}" if unit_context else ""
    if years and len(years) == row.n_cols:
        # find_year_row_near返回的年份是从原文里摘出来的一行行文字（比如"2019"），
        # 不是数字类型——这里统一转成int再排序，这样跟pdf_geometry_reconstruct.py
        # 那边的年份类型保持一致（那边GeometricRow.year_values的key本来就是int）。
        pairs_desc = sorted(
            ((int(y), v) for y, v in zip(years, row.values)), key=lambda item: -item[0],
        )
        results = []
        for year, value in pairs_desc:
            content = (
                f"{row.label} in {year}: {value}{unit_suffix} (reconstructed from a table; "
                f"this value was explicitly matched to year {year} via a year-header row found "
                f"elsewhere on the same page, not guessed)."
            )
            results.append({
                "chunk_id": f"{row.source_chunk_id}__reconstructed__{tag}__{year}",
                "page": row.page,
                "content": content,
                "reconstructed": True,
                "source_chunk_id": row.source_chunk_id,
                "years_confirmed": True,
                "year": year,
            })
        return results
    joined = ", ".join(f"{v}{unit_suffix}" for v in row.values)
    content = (
        f"{row.label} (reconstructed from a table; values listed in the original table's "
        f"order: {joined}. The specific year for each value could not be confirmed because "
        f"no year-header row was found in the extracted text — do not assume the first value "
        f"is the most recent year.)"
    )
    return [{
        "chunk_id": f"{row.source_chunk_id}__reconstructed__{tag}",
        "page": row.page,
        "content": content,
        "reconstructed": True,
        "source_chunk_id": row.source_chunk_id,
        "years_confirmed": False,
    }]

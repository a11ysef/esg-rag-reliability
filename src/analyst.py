#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyst.py —— 这是"可信ESG尽调"项目最后一步：把指标从报告里挖出来、做成对比表、
打一个可信度分数、标出风险点，再看看公司说的漂亮话和评级机构给的分数对不对得上。

这个文件是干嘛的：
    前面几个脚本已经做了脏活累活——build_corpus 把语料整理干净了，rag_baseline/benchmark
    证明了"光靠prompt管不住AI瞎编"，agent_cloud 证明了"先定位再核对"这套打法能把
    "答不上来却硬答"的情况压到0。这个文件把同一个思路——"答不上来就老实说答不上来，
    别瞎编"——从"回答一道题"升级成"写一份完整的分析报告"。

这个文件做三件事：
    1. 把指标一个一个挖出来做对比（extract_indicator）
       每个"公司+指标"的组合，先按相似度找几段可能相关的原文（这一步叫LOCATE），
       再让AI核对一下这段原文里的标签、口径对不对（这一步叫VERIFY）。
       挖到数字要附上原文和页码；挖不到就老实标"未披露"，绝对不让AI编数字。
    2. 生成一个"可信度"小结（build_trust_panel）
       每份报告开头都有一句话："一共N个指标，X个查到了原文出处，Y个没披露"。
       这不是锦上添花，是让看报告的人一眼就知道这份分析靠不靠谱、水分有多少。
    3. 自动标风险（evaluate_risks）
       把ESG数据翻译成"这家公司值不值得投、有什么坑"这种人话。这一层特意写成
       全是死规则、没有一句是让AI自己下结论的——金融尽调这种场景，风险判断必须
       能一条一条讲清楚是怎么算出来的，不能是AI一拍脑袋说"我觉得有风险"。
       AI只干它擅长的事（读懂一段话在说什么），下判断这件事完全交给写死的规则，
       这样才能写单元测试，也才能跟别人讲清楚这个判断到底是怎么来的。

    还有一个进阶功能（公司说的承诺漂不漂亮、跟实际做的差多少，以及不同评级机构
    打分打架的时候怎么办）依赖另一个脚本 parse_ratings.py 生成的文件
    analysis/ratings/<company>.json（里面存着MSCI的字母评级、隐含升温幅度、
    减排目标覆盖了多少排放，还有S&P Global的0-100分）。这份文件不存在的时候
    就自动跳过这部分，不影响前面两个核心功能——这是故意这么设计的，让两个脚本
    可以各自单独改、互不牵连。

复用了谁的代码：
    检索用的底层功能（cloud_embed/cloud_gen/cosine_top_k）是直接从 agent_cloud.py
    搬过来用的——"给三家公司的语料重新算一遍向量"这件事没必要在这个项目里再做
    一次，analysis/cloud_chunks/*.json 里存的就是唯一一份"标准答案"向量，这个
    文件只负责读、不负责重新算。

怎么在没有API key、没有真实PDF的情况下确认代码是对的：
    加 --selftest 参数：会拿假的embedding、假的AI回复函数替掉真实的云端调用，
    完整跑一遍流程（检索->挖数字->解析JSON->算可信度->跑风险规则->生成报告），
    每一步都用断言检查结果对不对。这个过程不用联网、不用API key，几秒钟就跑完，
    建议每次改完代码都先跑一下--selftest，确认没有明显问题了，再拿真实数据、
    真实API key去跑全量。

用法：
    export ZHIPU_API_KEY="你的key"

    # 先跑离线自检，确认代码逻辑没问题（不花API的钱）
    python src/analyst.py --selftest

    # 真实运行：默认分析 Alphabet / Apple / Meta Platforms 三家
    python src/analyst.py

    # 只跑部分公司 / 部分指标（调试用，省着点花API的钱）
    python src/analyst.py --companies Apple --indicators scope1_emissions renewable_energy_pct

    # 没有 parse_ratings.py 生成的评级数据时，跳过漂绿/评级分歧那部分
    python src/analyst.py --skip-ratings

    # 调整并发线程数（默认 6 个）：调小一点能缓解触发限流，调大一点跑得更快
    python src/analyst.py --workers 4

并发是怎么设计的（按"公司 x 指标"来并发，默认一共 21 个任务、开 6 个线程）：
    每次调用 extract_indicator() 内部还是老老实实一个个试候选片段，试到一个能用的
    就停手，这个逻辑没变——真正能同时跑的，是任务和任务之间（不同公司、不同指标
    互不依赖，谁也不用等谁）。唯一需要小心的共享状态是硬盘上那份 query embedding
    缓存文件，已经加了锁保护（看 get_query_vec 那个函数），防止好几个线程同时写
    这个文件、把别人刚写进去的东西覆盖掉——这种"好几个人同时写同一个文件、写着
    写着把别人的更新弄丢了"的坑这个项目里已经踩过不止一次，具体的教训写在
    get_query_vec 和 run_selftest 的注释里。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLOUD_CHUNK_DIR = ROOT / "analysis" / "cloud_chunks"
RATINGS_DIR = ROOT / "analysis" / "ratings"
OUT_DIR = ROOT / "analysis" / "outputs"
QUERY_EMB_CACHE = ROOT / "analysis" / "analyst_query_emb.json"

DEFAULT_COMPANIES = ["Alphabet", "Apple", "Meta Platforms"]   # 这三家的数据已经反复跑过、验证过了

TOP_K = 20   # 一开始是5，后来调到10，现在是20——不是拍脑袋加大的，是因为TOP_K=10那次
             # 跑出来一堆"未披露"，我把每一条都手动查了一遍才定下这个数字。具体做法：
             # 拿8个指标的查询向量，跟公司全部的文本片段（不只是前10个）都算一遍相似度，
             # 看看真正含答案的那段原文排第几名——查出来的真实情况是：Alphabet可再生能源
             # 占比那段排第11，供应链那段排第13；Apple总取水量那段排第11，Apple可再生
             # 能源的真实证据（第81页那句脚注"自2018年起100%的电力来自可再生能源"）排
             # 第14，供应链数据排第20。这些在TOP_K=10的时候全部被漏掉了——说明不是校验
             # 太严格、也不是数据真的没有，就是候选池开得不够大。20能盖住上面这些真实
             # 排名，还留了点余量。
             # 同时也确认了：有一类"未披露"不是靠加大TOP_K能救回来的，比如Meta的可再生
             # 能源占比、废弃物转移率，把它全部的语料都搜了一遍，报告里压根没用百分比
             # 披露过这两项（Meta给的是绝对用量/MW装机容量），再怎么加大TOP_K也没用——
             # 这种情况会老老实实写进案例文档的"局限性"部分，不会为了凑数字硬编。
MAX_VERIFY = 20  # 跟上面的TOP_K保持一致，理由一样（TOP_K找出几个候选就全部核对完，
                 # 不提前打断）。代价是真正"确实没披露"的指标要多跑几次AI调用才能死心，
                 # 不过配合下面的并发（见DEFAULT_WORKERS），实测下来总耗时还能接受。

TWO_COLUMN_MAX_PAGES = 5
# 双栏正文重排这一层（把被排版拆乱的正文重新拼顺）只对这次检索里相似度排名最高的
# 前几页跑，不会对TOP_K找出来的所有页（可能有十几二十页）都跑一遍——真正相关的内容
# 本来就集中在排名靠前的地方，排名靠后的页面就算真的是双栏排版被拍乱了，本身跟这个
# 指标关系也不大，重排了大概率还是用不上，不值得为了这几页把AI调用量再翻好几倍。
# 5是拍出来的经验值：留了点余量（正常答案在TOP_K=20里排名不会特别靠后，前5页已经
# 盖住了目前实测到的真实情况），多花的成本也还可控。

DEFAULT_WORKERS = 6  # 默认开几个线程同时跑。每个"公司+指标"组合内部的extract_indicator()
                     # 还是老老实实一个个候选片段挨个试、找到就停（这部分不并发，具体
                     # 看extract_indicator的说明）——真正同时跑的是"公司x指标"这个外层
                     # （3家公司 x 7项指标 = 21个基本互相独立、谁也不用等谁的任务）。
                     # 6这个数字是试出来的：智谱的API没有公开说并发上限是多少，开太大
                     # 容易触发限流（虽然已经写了_retry自动重试顶一下，但等待重试本身
                     # 也要花时间，等于白跑一趟）；开太小又体现不出并发的好处。
                     # 想调的话用 --workers 这个参数。

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_cloud  # noqa: E402  # 借用它现成的云端调用和检索功能，不用重写一遍
from agent_cloud import cloud_embed, cloud_gen, cosine_top_k  # noqa: E402
from table_reconstruct import (  # noqa: E402
    reconstruct_flattened_table, find_year_row_near, row_matches_keywords, rows_to_synthetic_chunk,
    find_unit_context,
)
import pdf_geometry_reconstruct  # noqa: E402  # 自检的时候要临时替换掉它里面的extract_page_words函数，所以要拿到模块本身
from pdf_geometry_reconstruct import (  # noqa: E402
    resolve_pdf_path, reconstruct_table_by_geometry, rows_to_synthetic_chunk_geometry, GeometricRow,
    reconstruct_two_column_text,
)

try:
    from parse_ratings import MSCI_LETTER_TO_SCORE, RATING_LETTERS  # noqa: E402
except Exception:                                              # noqa: BLE001
    RATING_LETTERS = ["AAA", "AA", "A", "BBB", "BB", "B", "CCC"]
    MSCI_LETTER_TO_SCORE = {"CCC": 7.1, "B": 21.4, "BB": 35.7, "BBB": 50.0,
                             "A": 64.3, "AA": 78.6, "AAA": 92.9}

RATING_ORDER = {letter: i for i, letter in enumerate(reversed(RATING_LETTERS))}  # 把字母评级换成数字好比大小：CCC=0 ... AAA=6


# ---------------------------------------------------------------------------
# 指标定义
# ---------------------------------------------------------------------------

@dataclass
class IndicatorSpec:
    id: str
    label_cn: str
    label_en: str
    category: str            # 大类：emissions（排放）/ energy（能源）/ water（水）/ waste（废弃物）/ supply_chain（供应链）
    query: str                # 拿去做向量检索的英文查询语句（贴近报告里实际的用词，搜出来的结果更准）
    disambiguation: str        # 提醒AI别把"看起来差不多但其实不对"的数字当成答案（是之前真吃过亏才加的）
    valid_units: tuple[str, ...] | None = None
    # 这是一份"合法单位"名单（小写、只要是子串命中一个就算过），用纯Python代码去检查，
    # 不指望AI自己判断"这个数字的单位对不对得上这个指标"。背景是：就算prompt写得再细，
    # AI（用的GLM-4-Flash）碰到真正乱的多列表格（标签和数字根本对不上号）时，还是会
    # 挑一个看着沾边但单位完全不对的数字硬答（比如把"天然气用量 1,007,071 MMBtu"
    # 当成"Scope 1排放"报出来）。既然AI自己把关不住，那就加一道代码关卡兜底：单位
    # 不在名单里，直接当没找到，不采信、也不展示给用户——这跟后面风险规则那部分
    # "AI只管读、代码来判断"是同一个思路。留空（None）表示这个指标的单位本来就
    # 五花八门，没法定死一个名单（比如supply_chain_emissions这个指标）。
    required_quote_keywords: tuple[str, ...] | None = None
    # 第二道关卡：AI回复里的quote字段（它自己说的"原文证据"）必须真的出现这里列的
    # 关键词之一（小写子串，命中一个就算过），不然直接拒绝，不管数字和单位看起来
    # 多像真的。背景：单位那道关卡挡住了"把天然气用量当成Scope 1"这种低级错误后，
    # AI又犯了个更隐蔽的错——挑了个单位没错、但标签其实是"公司总排放量（Scope1+2
    # 合并）"而不是单独的"Scope 1"（这两行紧挨在同一张表里，长得很像）。光在prompt
    # 里提醒它"别拿总数替代"没用，它还是会犯——所以干脆不信它的自我判断，直接检查
    # 它交上来的证据原文里有没有真的写着"Scope 1"这几个字。
    table_row_keywords: tuple[str, ...] | None = None
    # 这份关键词是给table_reconstruct.py那部分"把表格拆碎的行重新拼起来"用的，
    # 用来判断某一行的标签算不算跟这个指标沾边（同样是小写子串匹配）。
    # 跟上面的required_quote_keywords不是一回事：required_quote_keywords管的是
    # AI自己写的quote里必须出现什么字，这个table_row_keywords管的是"重新拼出来的
    # 表格行"要不要被当成这个指标的候选。没单独给的话，默认就用
    # required_quote_keywords那份（scope1/2/3已经有"scope 1"/"scope1"这类词了，
    # 拿来匹配表格行标签也够用）；没有required_quote_keywords的指标（比如可再生
    # 能源占比、取水量）才需要单独给一份。


# 这是Scope 1/2/3这三个指标共用的合法单位名单。里面的"tcoe"是真实数据跑出来才补上的：
# 用pdfplumber去读Alphabet报告第75/76页表格"Unit"这一列的字符坐标，确认那一格
# 打印出来的原文就是干干净净4个字符"tCOe"——中间那个"2"不是提取的时候漏掉了，是
# 这份PDF压根就没把它当成文字画上去（很可能"CO₂"下标那个"2"是用图形画的，不是
# 可以提取的文字），所以不管用什么文字提取方案都不可能从这页拿到带"2"的写法。
# "tCOe"其实就是ESG报告里"每公吨二氧化碳当量"常见的简写，不是编造也不是放水，
# 把它加进名单里，不然这份报告里靠这张表算出来的Scope 1/2/3全都会被单位这道关卡
# 拦住，哪怕标签、数值、年份其实全部对得上。
EMISSIONS_VALID_UNITS = ("co2", "co₂", "tcoe")

INDICATORS: list[IndicatorSpec] = [
    IndicatorSpec(
        id="scope1_emissions", label_cn="Scope 1 排放（直接排放）", label_en="Scope 1 GHG emissions",
        category="emissions",
        query="Scope 1 direct greenhouse gas (GHG) emissions in metric tons of CO2 equivalent",
        disambiguation="必须是标注为 'Scope 1' 的数字，不是 'Total emissions'（总排放），"
                        "不是 'Scope 2'，也不是往年基线值。",
        valid_units=EMISSIONS_VALID_UNITS,
        required_quote_keywords=("scope 1", "scope1"),
    ),
    IndicatorSpec(
        id="scope2_emissions", label_cn="Scope 2 排放（间接-能源）", label_en="Scope 2 GHG emissions (market-based)",
        category="emissions",
        query="Scope 2 market-based indirect greenhouse gas emissions in metric tons of CO2 equivalent",
        disambiguation="优先取 'market-based'口径；必须明确标注为 'Scope 2'，不要跟 Scope 1 或"
                        "location-based 口径混淆。",
        valid_units=EMISSIONS_VALID_UNITS,
        required_quote_keywords=("scope 2", "scope2"),
    ),
    IndicatorSpec(
        id="scope3_emissions", label_cn="Scope 3 排放（价值链）", label_en="Scope 3 GHG emissions",
        category="emissions",
        query="Scope 3 value chain total greenhouse gas emissions in metric tons of CO2 equivalent",
        disambiguation="必须标注为 'Scope 3' 的合计数，不是某个子类目（如仅'Purchased goods'）"
                        "单独的数字，除非报告只披露了这一项。",
        valid_units=EMISSIONS_VALID_UNITS,
        required_quote_keywords=("scope 3", "scope3"),
    ),
    IndicatorSpec(
        id="renewable_energy_pct", label_cn="可再生能源占比", label_en="Renewable energy percentage",
        category="energy",
        query="percentage of total electricity or energy consumption from renewable sources",
        disambiguation="要的是「占比」（百分比），不是可再生能源的绝对用量（MWh/GWh）。",
        valid_units=("%", "percent", "percentage"),
        # 这是真实数据跑出来才发现的坑：报告原文里这一行的标签写法比下面这四个关键词
        # 宽得多——Alphabet原文表格这一行标签是"Electricity purchased from renewable
        # sources"（完全没提到"percentage"或"renewable %"这几个字），Meta原文表格
        # 更极端，整行标签干脆就一个字"Renewable"（单位"%"是表头单独一列，不在行
        # 标签里）。原来那四个关键词哪个都对不上，这两家公司这一行数据在候选生成
        # 这一步就被挡在外面了，AI根本没机会看到它、判断的机会都没给。不是AI判错，
        # 是候选池里从一开始就没有这一行。所以补上这两种真实见过的写法，还加了个
        # 很容易误伤别的行的裸词"renewable"——敢加这么宽是因为上面valid_units那道
        # 单位校验本来就会挡住单位不是%的行（比如误配到"Renewable electricity
        # capacity operational"这种用GW做单位的行，会在那道校验被拦下，然后接着
        # 试下一个候选），多几个候选不会让准确率变差，只会让真正对的那一行多一次
        # 被试到的机会。
        table_row_keywords=("renewable electricity percentage", "renewable energy percentage",
                             "% renewable", "renewable %", "electricity purchased from renewable",
                             "renewable"),
    ),
    IndicatorSpec(
        id="water_withdrawal", label_cn="总取水量", label_en="Total water withdrawal",
        category="water",
        query="total water withdrawal or water consumption in cubic meters or megaliters",
        disambiguation="要「取水量/用水量」的总数，不是节水目标或水效比率（如 WUE），"
                        "除非报告没有披露总量。",
        valid_units=("gallon", "liter", "litre", "cubic meter", "cubic metre", "m3", "m³",
                     "megaliter", "megalitre", "kiloliter", "kilolitre"),
        table_row_keywords=("water withdrawal",),
        # 这里以前还有个更宽泛的关键词"total water"，是真实数据跑出来才发现的问题：
        # Meta第86页表格里同时有一行"Total water recycled"（回收水量，跟"取水量"是
        # 完全不同的口径，方向甚至是反过来的），"total water"这个词会把它也误认成
        # 候选行——虽然因为同一页找不到年份行，最后只会进partial_table_rows（不算
        # "已核实"），不会污染报告里的核心数字，但展示出来的"疑似匹配"本身就是错的，
        # 容易误导人工去复核。所以收紧成只认"water withdrawal"这个更精确的说法，
        # 宁可少捞到几行候选，也不要把口径完全不一样的数字标成"疑似匹配这个指标"。
    ),
    IndicatorSpec(
        id="waste_diversion_pct", label_cn="废弃物转移率（不进填埋场）", label_en="Waste diversion rate",
        category="waste",
        query="percentage of operational waste diverted from landfill, waste diversion rate",
        disambiguation="要「转移率/回收率」的百分比，不是废弃物总重量（吨）。",
        valid_units=("%", "percent", "percentage"),
        table_row_keywords=("waste diversion", "diversion rate"),
    ),
    IndicatorSpec(
        id="supply_chain_emissions", label_cn="供应链排放/供应商审计", label_en="Supply chain emissions or supplier audits",
        category="supply_chain",
        query="supply chain or supplier greenhouse gas emissions, supplier code of conduct audits, "
              "or supplier sustainability assessments",
        disambiguation="可以是供应链相关排放数字，也可以是供应商审计覆盖率/数量——"
                        "只要是明确针对「供应商/供应链」的数据即可，不要用公司自身运营的数字替代。"
                        "供应商参与度/覆盖面的具体数字也算数，例如"
                        "「与39家核心供应商合作核算其排放」「28%的供应商已设定科学碳目标」——"
                        "不需要原文出现'audit'这个词才算数，只要是可核实的、针对供应商的具体数字即可。",
        # 后面这段补充说明是真实数据跑出来才加的：查Meta第31页的调用记录，发现AI看着
        # 这样一句干净、能核实的话——"In 2021, we began working with a pilot group of
        # 39 key suppliers to calculate and report their GHG emissions"——依然判定
        # NOT_IN_PASSAGE（没找到）。大概率是原来disambiguation里"审计覆盖率/数量"这
        # 几个字让AI误以为一定要看到"audit"这个词才算数。这里只是把本来就该算数的
        # 范围说得更明白，没有放宽标准（这个指标本来就没设valid_units和
        # required_quote_keywords，就是因为一开始设计时就知道它的披露形式五花八门，
        # 没法用固定词表卡死）。
    ),
]

INDICATOR_BY_ID = {spec.id: spec for spec in INDICATORS}


# ---------------------------------------------------------------------------
# AI提示词模板（沿用agent_cloud.py里那个"NOT_IN_PASSAGE"的说法，让整个项目"这段
# 没答案就老实说没有"的做法保持一致）
# ---------------------------------------------------------------------------

EXTRACT_TMPL = (
    # 下面这版prompt是从agent_cloud.py里那个已经跑过eval、证明有效的VERIFY_TMPL
    # 改过来的——排查发现最早那版（用"Target metric: X\nDisambiguation rule: Y"
    # 这种抽象标签式的写法，还要求quote必须是"完整的一句话"）会让AI在真实PDF表格
    # 那种"标签紧挨着数字但凑不成一句完整话"的段落上，大量说"没找到"，哪怕数字
    # 明明就摆在那里。
    # 改成像正常提问一样的框架，并且允许拿"表格行片段"当证据之后，又冒出个反方向
    # 的新问题：AI碰到真正杂乱的多行多列表格（一段文字里塞了七八个不同指标、几十个
    # 数字混一起，标签和数字根本对不上号）时，会矬子里拔将军，随便挑一个看着沾边的
    # 数字就报出来——比如把"天然气用量（MMBtu）"错当成"Scope 1排放（tCO2e）"，单位
    # 都对不上。所以这版又加了一条明确的规矩：只信"标签后面紧跟着唯一一个数字、
    # 再跟着单位"这种干净的组合；要是标签后面是一堆数字挤在一起、根本看不出哪个数字
    # 对应哪个标签，就算其中有个数字看着眼熟，也当成证据不够，宁可不答。
    "You are extracting one specific ESG metric from a passage of a sustainability report.\n\n"
    "Passage (from page {p}):\n{c}\n\n"
    "Question: What is {label_en} ({label}){year_hint}?\n"
    "Matching rule: {disambiguation}\n\n"
    "Steps:\n"
    "1. Identify exactly what the question asks: the subject, the year, and the specific metric/scope.\n"
    "2. Find the number in the passage whose label matches ALL of those. Nearby numbers referring to "
    "a different year, scope, or baseline are NOT the answer.\n"
    "3. This passage may come from a table that was flattened into plain text. Trustworthy evidence "
    "looks like a clean triplet: the label, then immediately its ONE number, then its unit — that is "
    "fine even though it is not a grammatical sentence. It is NOT trustworthy if the label is followed "
    "by a long run of several numbers before any unit or the next label shows up (a multi-row or "
    "multi-column table dumped as one blob, where it is unclear which number belongs to which row/label). "
    "In that ambiguous case, do NOT guess which number is the right one, even if one of them looks "
    "plausible — treat it as not found.\n"
    "4. If found with trustworthy evidence, respond with ONLY a compact JSON object on one line, "
    "no markdown fences:\n"
    '   {{"value": "<number as written, keep commas/decimals/%>", "unit": "<unit>", '
    '"year": "<year if stated, else null>", "quote": "<verbatim excerpt that shows the label next to '
    'its number — a table row or fragment is fine, it does NOT need to be a full sentence, <=200 chars>"}}\n'
    "5. If this passage does NOT contain this exact metric with trustworthy evidence, respond with "
    "exactly the token NOT_IN_PASSAGE and nothing else. Do not guess, do not substitute a related number, "
    "do not pick a number just because it appears near the right label."
)

COMMIT_TMPL = (
    "You are reviewing a passage of a company sustainability report for forward-looking "
    "climate/emissions commitments (e.g. 'net zero by 2030', 'carbon neutral', "
    "'science-based target').\n\n"
    "Passage (from page {p}):\n{c}\n\n"
    "If this passage contains such a commitment, respond with ONLY a compact JSON object:\n"
    '  {{"commitment": "<verbatim commitment sentence, <=200 chars>", "target_year": "<year or null>"}}\n'
    "If it does not, respond with exactly NOT_IN_PASSAGE and nothing else."
)


# ---------------------------------------------------------------------------
# 检索相关的基础功能
# ---------------------------------------------------------------------------

def load_company_chunks(company: str) -> list[dict]:
    path = CLOUD_CHUNK_DIR / f"{company}.json"
    if not path.exists():
        raise SystemExit(
            f"找不到 {company} 的云端向量：{path}\n"
            f"先运行 agent_cloud.py（哪怕只跑 --limit 1 也会触发阶段一重算向量），"
            f"或确认 analysis/cloud_chunks/ 目录已从上一个对话的产物里带过来。"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _load_query_cache() -> dict:
    if QUERY_EMB_CACHE.exists():
        return json.loads(QUERY_EMB_CACHE.read_text(encoding="utf-8"))
    return {}


def _save_query_cache(cache: dict) -> None:
    QUERY_EMB_CACHE.parent.mkdir(parents=True, exist_ok=True)
    QUERY_EMB_CACHE.write_text(json.dumps(cache), encoding="utf-8")


_query_cache_lock = threading.Lock()
# 这是并发改造以后加的唯一一把锁。get_query_vec()做的事是"先看缓存里有没有，
# 没有就去调用云端embedding接口，算完写回缓存字典，再把整个字典存到磁盘上"——
# 这一套"读了再改再写"的操作，一旦好几个线程同时在跑（并发跑多个公司/指标的时候
# 基本必然发生，尤其是刚开始跑、大家都在查同一批指标的时候），会出两种真实的并发
# 问题：一是两个线程都发现"缓存里没有"，于是各自都花钱调了一次embedding（浪费
# 但不算错）；二是更严重的，两个线程前后脚把整个缓存字典写到磁盘上，后写的那次
# 用的是自己内存里那份"旧"的字典内容，会把另一个线程刚写进去的条目给覆盖冲掉——
# 这份缓存文件（QUERY_EMB_CACHE）之前就是因为这个问题反复出过状况。所以宁可
# 牺牲一点点并发效率（反正一共就8种不同的query，抢锁的时间很短），也要保证这个
# 磁盘文件不会被写坏。


def get_query_vec(query: str, cache: dict) -> list[float]:
    """
    指标的查询语句是固定的、三家公司共用同一份（同一个query会拿去分别检索三家
    公司的语料），把结果缓存到磁盘上，就不用每次重跑都重新花钱、重新等embedding
    算完，这跟agent_cloud.py里问题向量缓存的做法是一个道理。

    并发的时候把这整段（包括调用云端embedding本身）都加了锁：这个函数一共只有
    8种不同的query会传进来（7个指标 + 1个承诺话术的查询），缓存一旦建好，后面
    全是在内存字典里查一下，锁带来的开销可以忽略不计；真正花时间、真正值得
    并发跑的是extract_indicator()里对每个候选片段做核对（VERIFY）那部分，
    那部分完全不受这把锁的影响。
    """
    with _query_cache_lock:
        if query not in cache:
            cache[query] = cloud_embed(query)
            _save_query_cache(cache)
        return cache[query]


def retrieve(query: str, chunks: list[dict], cache: dict, k: int = TOP_K):
    qv = get_query_vec(query, cache)
    return cosine_top_k(qv, chunks, k)


# ---------------------------------------------------------------------------
# 解析AI返回的JSON（解析不出来就当"没抽到"，不瞎猜）
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", flags=re.MULTILINE)


def _parse_json_response(resp: str) -> dict | None:
    cleaned = _FENCE_RE.sub("", resp.strip()).strip()
    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else None
    except Exception:                          # noqa: BLE001
        pass
    # 兜底方案：JSON解析失败就用正则硬抓"value"字段，因为AI偶尔会漏写引号或多说几句废话
    m = re.search(r'"value"\s*:\s*"([^"]+)"', cleaned)
    if m:
        unit_m = re.search(r'"unit"\s*:\s*"([^"]*)"', cleaned)
        year_m = re.search(r'"year"\s*:\s*"?([\w]*)"?', cleaned)
        quote_m = re.search(r'"quote"\s*:\s*"([^"]*)"', cleaned)
        return {"value": m.group(1), "unit": unit_m.group(1) if unit_m else None,
                "year": year_m.group(1) if year_m else None,
                "quote": quote_m.group(1) if quote_m else None}
    return None


_MAGNITUDE_WORDS = {"thousand": 1e3, "million": 1e6, "billion": 1e9}
# 这次是真实数据直接暴露出_to_float原来的一个漏洞：它只会处理"79,400"这种纯数字加
# 千分位逗号的写法，碰到AI（正确地）照原文写出来的"10.8 million"/"3.4 million"/
# "6.4 billion gallons"这种带英文数量级单位的写法，float()直接报错返回None。这
# 本来只是让numeric_value这个字段是空的（不影响disclosed这个"披露没披露"的状态，
# 因为老版本的extract_indicator只检查value字段是不是有内容），但后来给
# extract_indicator加上"value必须能转成数字才采信"这道新关卡之后，问题就露出来
# 了——Alphabet那两条本来完全抽对的Scope2/Scope3结果（"10.8 million"、
# "3.4 million"）被这道新关卡连带误杀。这是自己加校验的时候引入的问题，不是数据
# 或AI的错，修法是让_to_float认识这几个常见的数量级单位词，而不是把新校验放松掉。


def _to_float(s) -> float | None:
    if s is None:
        return None
    text = str(s).strip().lower().replace(",", "").replace("%", "").strip()
    for word, mult in _MAGNITUDE_WORDS.items():
        if word in text:
            try:
                return float(text.replace(word, "").strip()) * mult
            except Exception:                   # noqa: BLE001
                return None
    try:
        return float(text)
    except Exception:                           # noqa: BLE001
        return None


def _unit_is_plausible(unit, value, valid_units: tuple[str, ...]) -> bool:
    """检查这个指标该有的单位（看IndicatorSpec.valid_units那份名单）有没有出现——
    是把unit和value这两个字段拼一起看，不是只死板地看unit字段。为什么这么做：
    实测发现AI有时候会把"82%"整个写进value字段，却把unit字段空着（严格来说是没
    完全按格式来），要是只看unit字段，就会把一个明明答对了的结果误判成"单位不对"
    给拒掉——这是校验逻辑本身的漏洞，不是AI的错，不该因为这个把正确答案也一起误杀。"""
    combined = f"{unit or ''} {value or ''}".lower()
    return any(kw in combined for kw in valid_units)


def _quote_has_required_keyword(quote, required_keywords: tuple[str, ...]) -> bool:
    """检查AI自己交上来的quote（它说的"原文证据"）里，是不是真的出现了这个指标要求的
    关键词（看IndicatorSpec.required_quote_keywords那份名单）。没有quote，或者quote
    里一个关键词都没提到，就返回False——用来挡住"单位是对的，但其实抓错了旁边那行
    总数/别的口径"这种更隐蔽的错误。"""
    if not quote:
        return False
    low = str(quote).lower()
    return any(kw in low for kw in required_keywords)


def _value_near_required_keyword(quote, value, required_keywords: tuple[str, ...],
                                  max_distance: int = 80) -> bool:
    """比_quote_has_required_keyword更严一步：不只要求关键词和数字都在quote里出现过，
    还要求它们在原文里离得够近（默认不超过80个字符）。背景：实测又发现更隐蔽的第三种
    错法——quote把一整段乱糟糟的表格都抄了进来，"Scope 2"这个词跟真正命中的数字其实
    隔着好几行，中间夹着好几个不相关的标签和数字（比如Apple第88页那张表），光看"关键词
    出现过没有"这道关卡拦不住这种情况。要是在quote里找不到value的确切位置，或者离
    关键词太远，就一律当没通过，这次候选不采信。

    这里还修过一个跟千分位逗号有关的坑：真实数据里，Meta Scope 1（2022年）这一条，
    quote是"Scope 1 in 2022: 66,934 (in metric tons CO2e)"（带千分位逗号，是AI原样
    抄出来的真实证据，_quote_is_genuine那道更根本的校验也确认过是真的），但AI自己
    填的value字段是"66934"（没有逗号——JSON数字字段本来就该是纯数字，这么写完全
    合理）。这道检查原来直接拿没逗号的"66934"去quote原文里找，"66,934"中间夹着个
    逗号，自然永远找不到，于是把这个关键词对、数字对、证据也是真的候选，硬说成
    "关键词和数值离得太远"给拒了——其实根本不是离得远，是这道检查自己没考虑到
    两边可能一个带逗号一个不带。跟这个项目之前修过的好几个坑是同一类问题：校验
    代码自己没把边界情况想全，误伤了本该采信的真实结果。修法是：比较之前先把
    quote和value两边的逗号都去掉，在同一个"去掉逗号后"的字符串里统一算位置，
    这样关键词和数值之间的相对距离不受影响。"""
    if not quote or not value:
        return False
    low = str(quote).lower().replace(",", "")
    val_idx = low.find(str(value).lower().strip().replace(",", ""))
    if val_idx == -1:
        return False
    return any(
        abs(m.start() - val_idx) <= max_distance
        for kw in required_keywords
        for m in re.finditer(re.escape(kw), low)
    )


def _quote_is_genuine(quote, chunk_content) -> bool:
    """这是最后一道、也是最根本的一道校验：quote是不是原文chunk里真实连续存在的一段文字，
    而不是AI东拼西凑出来的"看起来像证据"的句子。背景：接入真实API之后发现，Apple
    Scope 2那次抽错的例子（1,066,257）里，前面三道校验（单位、关键词、关键词跟数字的
    距离）全部都通过了——因为那三道检查看的都是AI自己写的quote内部逻辑通不通，而AI
    交上来的quote是"Scope 2 emissions (market-based, metric tons CO2e)1 1,066,257"，
    读起来很像回事、挑不出毛病，但这句话在原文里根本不是连续出现的——原文里这个标签
    和这个数字之间隔着十几个不相关的数字。也就是说AI可以把标签文本和它挑中的数字手动
    拼在一起再交上来，把前面三道检查全部骗过去。这道检查不再看quote内部说不说得通，
    而是直接拿quote去跟原文chunk的真实文本做字符串比对——把两边的换行和多余空格都
    压成一个空格、转成小写，看quote是不是chunk原文里真实存在的一段连续文字（如果AI
    用"..."省略号断开了quote，就按省略号切开分别检查每一段，只要求每一段各自连续
    存在，不要求这几段紧挨着）。这是唯一一道不管AI"怎么说"、只看原文"实际写了什么"的
    校验，堵住了前面三道校验都堵不住的漏洞。"""
    if not quote or not chunk_content:
        return False
    norm_quote = re.sub(r"\s+", " ", str(quote)).strip().lower()
    norm_content = re.sub(r"\s+", " ", str(chunk_content)).strip().lower()
    parts = [p.strip() for p in re.split(r"\.\.\.|…", norm_quote) if p.strip()]
    if not parts:
        return False
    return all(part in norm_content for part in parts)


# ---------------------------------------------------------------------------
# 挖指标：先LOCATE（定位候选片段）再VERIFY（核对），一个个候选试过去，试中了就停手
# ---------------------------------------------------------------------------

@dataclass
class IndicatorResult:
    company: str
    indicator_id: str
    label_cn: str
    category: str
    disclosed: bool
    value: str | None = None
    numeric_value: float | None = None
    unit: str | None = None
    year: str | None = None
    page: int | None = None
    chunk_id: str | None = None
    quote: str | None = None
    similarity: float | None = None
    steps_tried: int = 0
    note: str = ""
    attempts: list = field(default_factory=list)   # 这是给调试用的：每个候选片段的页码、相似度、
                                                     # AI原始回复，不管最后有没有抽到都会记下来——
                                                     # 想搞清楚"为什么没抽到"，是检索压根没找到
                                                     # 对的那一页，还是AI觉得那一页里没有答案，
                                                     # 全靠这份明细，光看最后的"未披露"三个字看不出来。
    partial_table_rows: list = field(default_factory=list)
    # 这是table_reconstruct.py定位到了、但因为原文缺年份表头没法确定是哪一年的表格行——
    # 不算作disclosed（不冒充"已核实"），只是在报告里如实说明"系统找到了这一行，
    # 这几年的数值是什么，但不确定哪个数字对应现在这一年"，这比干巴巴一句"未披露"
    # 提供的信息更多，但也没有违反"拿不准就不采信"这条底线。


_TOTAL_LABEL_RE = re.compile(r"(?<!sub)\btotal\b", re.IGNORECASE)


def _prefers_total(content: str) -> bool:
    """检查标签里是不是明确写了"total"（合计/总计），但要排除"subtotal"（小计）
    冒充总数的情况——这是为了修一个真实抓到的bug：Apple第77页同时有两行，一行是
    "Product life Gross emissions (Scope 3)"（产品口径的子类目，数字是真的，只是
    不是这个指标要的那个），另一行是"Total gross scope 3 emissions (corporate
    and product)"（真正的合计数）。按坐标重建出来的候选是按页面从上到下排的，
    子类目行刚好排在合计行前面。而LOCATE+VERIFY是"试中了就停手"的设计，子类目行
    先被拿去验证——它本身就是真实合法的数据，四层机械校验一道都挑不出毛病，直接
    通过、循环立刻停手，真正该用的合计行永远轮不到被试。哪怕disambiguation里
    已经写明"必须是合计数，不能是子类目"，这句话也只是prompt里的文字，指望AI
    自己判断挡不住——这跟之前"光靠prompt提醒AI不能拿总数替代"不管用、只能加
    required_quote_keywords这道硬校验是同一个道理：不能指望AI自己分清"合计"
    和"子类目"，得在排候选顺序这一步就用代码把合计行排到前面优先试。
    这只是缓解，不是彻底解决——如果合计行的标签压根没出现"total"这个词（比如
    用的是中文"合计"，或者别的说法），这个办法就认不出来，还是会退回"谁排前面
    先被试到谁赢"的老样子。
    """
    return bool(_TOTAL_LABEL_RE.search(content))


def _reconstruct_candidates(company: str, spec: IndicatorSpec, hits: list, all_chunks: list[dict]):
    """
    把跟这个指标的行标签对得上的重建结果找出来，按"年份能不能确认"分成两类：
      - confirmed：标签、年份、数值这三样都是靠代码算出来的，没有让AI参与猜测。
        打包成一个干净的"合成片段"，跟正常检索到的候选一样，完整走一遍
        EXTRACT_TMPL加四层机械校验（不抄近路、不跳过任何一道已有的校验），
        只是喂给AI看的内容比原始被拍碎的文本更干净、更好认。
      - unconfirmed：能确定这一行标签是对的，但确定不了对应哪一年。这种不会
        塞进候选池让AI去验证（送进去也没意义——就算AI"确认"了，我们也不会
        采信这个结果），直接原样返回，交给extract_indicator在"实在没别的办法"
        的时候，如实记到partial_table_rows里给人工看。

    两条重建路径按优先级顺序跑，互不冲突：
      1. pdf_geometry_reconstruct：直接读原始PDF的版面坐标来对齐年份，这条路
         置信度最高（不依赖任何文字层面的规律，就算年份表头在提取文字的时候
         丢了或者错位了也能处理，这是table_reconstruct.py那条路解决不了的——
         具体看两边各自模块开头的说明）。找不到原始PDF、或者这一页坐标对不上，
         就安安静静什么都不返回，不报错也不打断流程。
      2. table_reconstruct：在已经检索到的候选文本里找"标签块挨着数字块"这种
         规律，作为兜底
         处理坐标法覆盖不到的页面。
    """
    keywords = spec.table_row_keywords or spec.required_quote_keywords
    if not keywords:
        return [], []
    seen_labels = set()
    confirmed_synthetic = []
    unconfirmed_rows = []

    # 1) 坐标级重建：直接读原始PDF的版面坐标
    pdf_path = resolve_pdf_path(company)
    if pdf_path is not None:
        seen_pages = sorted({c.get("page") for c, _ in hits if c.get("page")})
        # 同一页里随便找个原始片段的正文，从里面摘出单位说明（比如"in metric tons CO2e"）
        page_content_by_no = {}
        for c, _ in hits:
            p = c.get("page")
            if p is not None and p not in page_content_by_no:
                page_content_by_no[p] = c.get("content", "")
        for page_no in seen_pages:
            for grow in reconstruct_table_by_geometry(pdf_path, page_no, keywords):
                dedup_key = ("geom", grow.label.lower(), tuple(sorted(grow.year_values.items())))
                if dedup_key in seen_labels:
                    continue
                seen_labels.add(dedup_key)
                unit_ctx = find_unit_context(page_content_by_no.get(page_no, ""))
                # 每一年拆成一个独立候选、按从新到旧排好序返回（具体看
                # rows_to_synthetic_chunk_geometry的说明）——用extend而不是append，
                # 把这一行每一年的数据都变成候选池里单独的一条，这样"试中就停"
                # 天然会优先选中最新一年，不用再改后面任何验证逻辑。
                confirmed_synthetic.extend(rows_to_synthetic_chunk_geometry(grow, unit_ctx))

    # 2) 文字模式重建（兜底用）
    for chunk, _sim in hits:
        for row in reconstruct_flattened_table(chunk):
            if not row_matches_keywords(row, keywords):
                continue
            dedup_key = (row.label.lower(), tuple(row.values))
            if dedup_key in seen_labels:
                continue
            seen_labels.add(dedup_key)
            unit_ctx = find_unit_context(chunk.get("content", ""))
            years = find_year_row_near(row, all_chunks)
            if years:
                # 跟上面一样：rows_to_synthetic_chunk现在也是按年份从新到旧拆成
                # 多个候选返回，直接extend进候选池，"试中就停"自然会挑到最新一年。
                confirmed_synthetic.extend(rows_to_synthetic_chunk(row, years, unit_ctx))
            else:
                unconfirmed_rows.append({
                    "label": row.label, "values": row.values, "page": row.page,
                    "source_chunk_id": row.source_chunk_id,
                })

    # 3) 合计数优先排：标签里明确带"total"字样的候选排到前面优先验证（详细道理看
    #    _prefers_total那个函数）——用的是Python内置sort的稳定排序，同一优先级
    #    内部还是保留原来"坐标法在前、文字兜底在后"的顺序，这一步只多调整了
    #    "带total的排前面"这一个维度，不动其它已有的顺序逻辑。
    confirmed_synthetic.sort(key=lambda c: 0 if _prefers_total(c.get("content", "")) else 1)
    return confirmed_synthetic, unconfirmed_rows


def _reconstruct_two_column_candidates(company: str, hits: list) -> list:
    """
    这是"双栏正文重排"这一层的入口：只对这次检索到的候选片段里、相似度排名最高的
    前几页（看TWO_COLUMN_MAX_PAGES）试一下pdf_geometry_reconstruct里的
    reconstruct_two_column_text，能认出双栏分界线，就把"按正确阅读顺序重新拼好"
    的这一页也加进候选池；认不出来（这页本来就是单栏，或者虽然是双栏但没找到
    稳定的分界线）就跳过这一页，不强求。

    这跟表格重建（_reconstruct_candidates）是两条完全独立、互不冲突的路：表格
    重建解决的是"数字表格的年份对不齐"，这里解决的是"正文段落被双栏排版拆乱、
    拼错顺序"——这两类问题长得不一样、修法也不一样，所以分成两个函数，不共用
    候选池、不共用去重逻辑，也不会互相覆盖。这里生成出来的候选照样要走跟其它
    候选完全一样的EXTRACT_TMPL加四层机械校验，没有开小灶、没有绕过任何校验。

    真实数据背景：Meta报告第31页那次真实事故——"我们在2021年开始与39家核心供应商
    合作核算排放，到2023年底28%的供应商已设定科学碳目标"这段完整的话，被双栏排版
    拆乱、拼错了顺序，AI看到这种乱序文本就判定"没找到"，报告里明明白白写着的真实
    数字就这样丢了。这一层的目标就是把这种"数据其实在，只是文字顺序被拆乱了"的
    情况捞回来。
    """
    pdf_path = resolve_pdf_path(company)
    if pdf_path is None:
        return []
    seen_pages: list = []
    for chunk, _sim in hits:
        p = chunk.get("page")
        if p is not None and p not in seen_pages:
            seen_pages.append(p)
        if len(seen_pages) >= TWO_COLUMN_MAX_PAGES:
            break
    results = []
    for page_no in seen_pages:
        text = reconstruct_two_column_text(pdf_path, page_no)
        if not text:
            continue
        results.append({
            "chunk_id": f"{page_no}__two_column_reordered",
            "page": page_no, "content": text,
            "reconstructed": True, "reconstruction_method": "two_column_reorder",
        })
    return results


def extract_indicator(company: str, spec: IndicatorSpec, chunks: list[dict], cache: dict) -> IndicatorResult:
    hits = retrieve(spec.query, chunks, cache, TOP_K)

    # 表格重建：优先试一下"代码算出来的、年份能确定"的重建候选（这类置信度最高，
    # 排到最前面优先验证），原来那些正常检索到的候选完全保留在后面不受影响——
    # 这一步只是新增候选，不删掉任何原有候选，也不改变判断原有候选的方式。
    confirmed_synthetic, unconfirmed_rows = _reconstruct_candidates(company, spec, hits, chunks)
    synthetic_hits = [(synth, 1.0) for synth in confirmed_synthetic]

    # 双栏正文重排：解决表格重建管不到的"正文段落被双栏排版拆乱"问题（具体看
    # _reconstruct_two_column_candidates的说明）。给它的相似度比表格重建候选
    # 低一点点（0.99而不是1.0）——因为"文字顺序理顺了"不等于"内容一定是答案"，
    # 排在更确定的表格重建候选之后，但还是排在原始检索候选之前，让AI优先看到
    # 更干净的版本。同样不会删掉或覆盖任何原有候选。
    two_column_candidates = _reconstruct_two_column_candidates(company, hits)
    two_column_hits = [(c, 0.99) for c in two_column_candidates]

    hits = synthetic_hits + two_column_hits + list(hits)

    attempts = []
    extra_candidates = len(synthetic_hits) + len(two_column_hits)
    for step, (chunk, sim) in enumerate(hits[:MAX_VERIFY + extra_candidates], start=1):
        # 这是一个真实踩过、排查了很久的坑（用一个专门写的诊断脚本
        # debug_alphabet_scope1.py，一共做了7组对照实验才真正搞清楚，不是拍脑袋猜的）。
        # 现象：Question里本来不带年份，AI自己也不知道"该核对哪一年"——结果Alphabet
        # Scope 1那5个年份的坐标重建候选，标签、数值、单位格式全都修对了，还是全部被
        # 判"没找到"。
        # 第一轮排查：以为"只要在Question里提一次年份"就够了（做了个实验，确实成功），
        # 于是把这个year_hint机制加进代码、上线了。结果真实数据重新跑一遍，Alphabet
        # Scope1还是全部"没找到"。直接去看真实调用记下来的Question原文，发现year_hint
        # 确实一字不差地传给AI了——问题不是"没传进去"。
        # 第二轮：怀疑是"年份在句子里的位置离指标名太远"，试着挪了下位置——结果还是
        # 全部失败，这个猜测被推翻了。
        # 第三轮：把"成功的那次实验"和"失败的真实案例"逐字对比，才发现真正的区别是——
        # 成功那次把年份说了两遍（英文里说了一次"for 2023"，中文括注里又说了一次
        # "，2023年"），失败的版本只在英文里提了一次。单独去掉中文里那次重复年份，
        # 别的都不动——马上又变回"没找到"；反过来，把中文也补上年份——AI就给出了
        # 正确答案。cloud_gen用的是temperature=0（也就是不带随机性的确定性输出），
        # 这不是运气问题，是三组各自只改一个变量的对照实验共同指向的结论：这个AI
        # 在被要求输出严格JSON的时候比较谨慎，年份只提一次它不放心，中英文都点名
        # 同一年才会真正采信——具体是什么原理不好说，但这个行为已经用真实API反复
        # 验证过、能稳定复现。候选池本来就是按年份拆开的（rows_to_synthetic_chunk_
        # geometry / rows_to_synthetic_chunk这两个函数干的事），年份是代码算出来的、
        # 不是AI猜的，没道理只在一种语言里告诉它——所以年份已知的时候中英文都带上，
        # 只有候选本身年份不确定（原始检索出来的候选，真的需要AI自己判断"这是哪一年"）
        # 时才两边都留空。
        year_confirmed = bool(chunk.get("years_confirmed") and chunk.get("year"))
        year_hint = f" for {chunk['year']}" if year_confirmed else ""
        label_for_prompt = f"{spec.label_cn}，{chunk['year']}年" if year_confirmed else spec.label_cn
        prompt = EXTRACT_TMPL.format(p=chunk["page"], c=chunk["content"], label=label_for_prompt,
                                      label_en=spec.label_en, disambiguation=spec.disambiguation,
                                      year_hint=year_hint)
        resp = cloud_gen("You are a careful ESG data analyst who never guesses.", prompt)
        attempts.append({"step": step, "page": chunk["page"], "chunk_id": chunk.get("chunk_id"),
                          "similarity": round(sim, 4),
                          "content_preview": chunk["content"][:200].replace(chr(10), " ⏎ "),
                          "raw_response": resp[:300]})
        if "NOT_IN_PASSAGE" in resp.upper():
            continue
        parsed = _parse_json_response(resp)
        if parsed is None or not parsed.get("value"):
            continue
        if _to_float(parsed.get("value")) is None:
            # 这是第0道机械关卡（比_quote_is_genuine那道还早一步，也是真实数据跑出来
            # 才发现必须补上的）：value必须是个能转成数字的东西。背景：TOP_K从10调到
            # 20之后，Meta Scope 3在第11个候选片段（第23页，一句减排目标的话术
            # "Not exceeding our 2021 baseline Scope 3 emissions by the end of 2031"）
            # 上，AI把这句"承诺目标"错当成了"排放数值"报出来——value字段填的是
            # "Not exceeding our 2021 baseline"这种完整短语，根本不是数字。为什么会
            # 被前面几道关卡放过：这句话本身是原文里真实连续存在的文字（骗过了第①道），
            # AI自己填的unit是"tons CO2e"，拼起来能匹配上"co2"这个词（骗过了第②道），
            # quote里确实有"Scope 3"这几个字（骗过了第③道），数字意义上的value跟
            # 关键词在原文里也确实挨得近（骗过了第④道）——前面四道关卡统统只检查
            # "这个证据自己说不说得通、是不是来自原文"，没有一道检查过"这个value到底
            # 像不像一个数字"。这里补上：转不成数字就直接拒绝，不管前面四道过没过。
            # 这跟这个项目里每一道机械校验的来历都一样——不是提前设计好的，是被一个
            # 具体的真实错误逼出来的。
            attempts[-1]["rejected_reason"] = (
                f"value_not_numeric: value='{parsed.get('value')}' 不是一个数字"
                f"（很可能是模型把承诺/目标话术误当成了实际数值）"
            )
            continue
        if not _quote_is_genuine(parsed.get("quote"), chunk["content"]):
            # 第①道机械关卡（最根本的一道，具体看_quote_is_genuine的说明）：quote必须
            # 是原文里真实连续存在的文字，不能是AI自己拼凑出来的"看起来像证据"的句子。
            # 这道对所有指标都适用，不只是有required_quote_keywords的那几个。
            attempts[-1]["rejected_reason"] = (
                f"quote_not_genuine: quote='{str(parsed.get('quote'))[:80]}' 在原文 chunk 里找不到"
            )
            continue
        if spec.valid_units and not _unit_is_plausible(parsed.get("unit"), parsed.get("value"), spec.valid_units):
            # 第②道机械关卡：AI自己判断"这个数字对不对得上标签"不太可靠（具体道理看
            # 上面IndicatorSpec.valid_units那段说明），单位不在名单里就不采信，当成
            # 这个候选没找到，接着看下一个候选。
            attempts[-1]["rejected_reason"] = (
                f"unit_mismatch: got unit='{parsed.get('unit')}' value='{parsed.get('value')}', "
                f"expected one of {spec.valid_units}"
            )
            continue
        if spec.required_quote_keywords and not _quote_has_required_keyword(
            parsed.get("quote"), spec.required_quote_keywords
        ):
            # 第③道机械关卡：单位对得上不代表标签也对得上——同一张表里"Scope 1"跟它
            # 旁边的"总数/合并口径"往往单位完全一样，光靠单位这道关卡挡不住。这里
            # 再检查AI自己交上来的证据原文里，有没有真的出现这个指标要求的关键词
            # （看IndicatorSpec.required_quote_keywords那份名单），没有就不采信。
            attempts[-1]["rejected_reason"] = (
                f"quote_missing_keyword: quote='{str(parsed.get('quote'))[:80]}', "
                f"expected one of {spec.required_quote_keywords}"
            )
            continue
        if spec.required_quote_keywords and not _value_near_required_keyword(
            parsed.get("quote"), parsed.get("value"), spec.required_quote_keywords
        ):
            # 第④道机械关卡：关键词和数字都在quote里出现过，不代表它们说的是同一件
            # 事——这道用来挡住"quote把一整段乱糟糟的表格都抄进来，关键词和真正命中
            # 的数字其实隔着好几行不相关内容"这种更隐蔽的错法（具体看
            # _value_near_required_keyword的说明）。
            attempts[-1]["rejected_reason"] = (
                f"keyword_too_far_from_value: quote='{str(parsed.get('quote'))[:80]}', "
                f"value='{parsed.get('value')}'"
            )
            continue
        return IndicatorResult(
            company=company, indicator_id=spec.id, label_cn=spec.label_cn, category=spec.category,
            disclosed=True, value=parsed.get("value"), numeric_value=_to_float(parsed.get("value")),
            unit=parsed.get("unit"), year=parsed.get("year"), page=chunk["page"],
            chunk_id=chunk.get("chunk_id"), quote=parsed.get("quote"), similarity=round(sim, 4),
            steps_tried=step, attempts=attempts,
        )
    note = "报告中未定位到明确匹配该指标口径的数字，标记为未披露（而非强行取一个相近的数字）。"
    if unconfirmed_rows:
        note += (
            f" 补充：表格重建定位到 {len(unconfirmed_rows)} 条疑似匹配的表格行，"
            f"但原文缺少年份表头，无法确认具体对应哪一年，因此未采信为「已核实」——"
            f"跨期数值详见 partial_table_rows，供人工核查。"
        )
    return IndicatorResult(
        company=company, indicator_id=spec.id, label_cn=spec.label_cn, category=spec.category,
        disclosed=False, steps_tried=min(len(hits), MAX_VERIFY),
        note=note, attempts=attempts, partial_table_rows=unconfirmed_rows,
    )


# ---------------------------------------------------------------------------
# 挖出公司说过的承诺话术（这是判断"漂绿"的第一步：先看看报告里自己说了什么）
# ---------------------------------------------------------------------------

COMMIT_QUERY = ("net zero commitment, carbon neutral pledge, science-based emissions "
                "reduction target and target year")


@dataclass
class CommitmentQuote:
    page: int
    quote: str
    target_year: str | None
    similarity: float


def extract_commitments(company: str, chunks: list[dict], cache: dict, max_quotes: int = 3) -> list[CommitmentQuote]:
    hits = retrieve(COMMIT_QUERY, chunks, cache, TOP_K)
    found: list[CommitmentQuote] = []
    for chunk, sim in hits:
        if len(found) >= max_quotes:
            break
        resp = cloud_gen("You are a careful ESG analyst who never guesses.",
                          COMMIT_TMPL.format(p=chunk["page"], c=chunk["content"]))
        if "NOT_IN_PASSAGE" in resp.upper():
            continue
        parsed = _parse_json_response(resp)
        if parsed and parsed.get("commitment"):
            found.append(CommitmentQuote(page=chunk["page"], quote=parsed["commitment"],
                                          target_year=parsed.get("target_year"), similarity=round(sim, 4)))
    return found


# ---------------------------------------------------------------------------
# 算可信度小结
# ---------------------------------------------------------------------------

def build_trust_panel(results: list[IndicatorResult]) -> dict:
    total = len(results)
    verified = [r for r in results if r.disclosed]
    undisclosed = [r for r in results if not r.disclosed]
    pct = round(100 * len(verified) / total, 1) if total else 0.0
    return {
        "total_indicators": total,
        "verified_count": len(verified),
        "verified_pct": pct,
        "undisclosed_count": len(undisclosed),
        "undisclosed_indicators": [r.label_cn for r in undisclosed],
        "verified_indicators": [
            {"label": r.label_cn, "value": r.value, "unit": r.unit, "page": r.page} for r in verified
        ],
    }


# ---------------------------------------------------------------------------
# 加载评级数据，顺便把不同量级的分数换算成能放一起比的样子
# ---------------------------------------------------------------------------

def load_ratings(company: str, ratings_dir: Path) -> dict | None:
    path = ratings_dir / f"{company}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _rating_field_value(ratings: dict | None, field_name: str):
    if not ratings:
        return None
    f = ratings.get(field_name)
    return f.get("value") if isinstance(f, dict) else None


def msci_letter_to_score(letter: str | None) -> float | None:
    if not letter:
        return None
    return MSCI_LETTER_TO_SCORE.get(letter.strip().upper())


# ---------------------------------------------------------------------------
# 风险预警：全是写死的规则，每一条都能查清楚是怎么算出来的，不让AI自己拍脑袋下结论
# ---------------------------------------------------------------------------

@dataclass
class RiskFlag:
    rule_id: str
    severity: str    # 风险等级：high（高）/ medium（中）/ info（提示）
    message: str


def _rule_scope3_dominance(indicators: dict[str, IndicatorResult], ratings: dict | None) -> RiskFlag | None:
    s1 = indicators.get("scope1_emissions")
    s2 = indicators.get("scope2_emissions")
    s3 = indicators.get("scope3_emissions")
    if not (s1 and s2 and s3 and s1.disclosed and s2.disclosed and s3.disclosed):
        return None
    if s1.numeric_value is None or s2.numeric_value is None or s3.numeric_value is None:
        return None
    base = s1.numeric_value + s2.numeric_value
    if base <= 0 or s3.numeric_value <= 5 * base:
        return None
    coverage = _rating_field_value(ratings, "msci_target_coverage_pct")
    coverage_f = _to_float(coverage)
    if coverage_f is not None and coverage_f >= 50:
        return None   # 目标已经覆盖了一半以上的排放，不算风险，不用预警
    ratio = round(s3.numeric_value / base, 1)
    coverage_note = f"，MSCI 数据显示减碳目标仅覆盖 {coverage}% 的排放" if coverage_f is not None else "，且未取得目标覆盖度数据用于交叉核实"
    return RiskFlag(
        rule_id="scope3_dominance", severity="high",
        message=f"Scope 3 排放（约 {s3.value} {s3.unit or ''}）是 Scope 1+2 合计的约 {ratio} 倍"
                f"{coverage_note}——价值链层面的转型风险敞口大，减排目标的覆盖广度需要重点核查。",
    )


def _rule_supply_chain_gap(indicators: dict[str, IndicatorResult], ratings: dict | None) -> RiskFlag | None:
    sc = indicators.get("supply_chain_emissions")
    if sc and not sc.disclosed:
        return RiskFlag(
            rule_id="supply_chain_gap", severity="medium",
            message="报告中未找到供应链相关排放数据或供应商审计披露——这是尽调阶段应重点向公司索取的信息缺口，"
                    "而非本系统的检索失败（已对该指标做过多候选片段核对）。",
        )
    return None


def _rule_low_verification_rate(trust_panel: dict) -> RiskFlag | None:
    if trust_panel["verified_pct"] < 50:
        return RiskFlag(
            rule_id="low_verification_rate", severity="medium",
            message=f"本次仅在 {trust_panel['total_indicators']} 项核心指标中核实到 "
                    f"{trust_panel['verified_count']} 项（{trust_panel['verified_pct']}%），"
                    f"该公司在这些维度上的可验证披露不足，后续分析结论应视为初步、需要人工补充核查。",
        )
    return None


def _rule_rating_downgrade(ratings: dict | None) -> RiskFlag | None:
    history = _rating_field_value(ratings, "msci_rating_history")
    if not isinstance(history, list) or len(history) < 2:
        return None
    try:
        prev, curr = history[-2], history[-1]
        prev_ord, curr_ord = RATING_ORDER[prev["rating"].upper()], RATING_ORDER[curr["rating"].upper()]
    except Exception:                          # noqa: BLE001
        return None
    if curr_ord < prev_ord:
        return RiskFlag(
            rule_id="rating_downgrade", severity="medium",
            message=f"MSCI 评级从 {prev['rating']}（{prev.get('period', '?')}）下调至 "
                    f"{curr['rating']}（{curr.get('period', '?')}），评级趋势值得关注。",
        )
    return None


GREENWASHING_COVERAGE_THRESHOLD = 30.0


def _rule_greenwashing_gap(commitments: list[CommitmentQuote], ratings: dict | None) -> RiskFlag | None:
    if not commitments:
        return None
    coverage = _to_float(_rating_field_value(ratings, "msci_target_coverage_pct"))
    if coverage is None or coverage >= GREENWASHING_COVERAGE_THRESHOLD:
        return None
    quote = commitments[0].quote
    snippet = quote if len(quote) <= 80 else quote[:77] + "…"
    return RiskFlag(
        rule_id="greenwashing_gap", severity="high",
        message=f"报告中有承诺表述「{snippet}」（第 {commitments[0].page} 页），"
                f"但 MSCI 数据显示相关减排目标仅覆盖公司 {coverage}% 的排放——"
                f"承诺的言辞力度与其实际覆盖范围不成比例，存在漂绿风险，建议在尽调中要求公司说明差距。",
    )


RATING_DIVERGENCE_THRESHOLD = 25.0


def _rule_rating_divergence(ratings: dict | None) -> RiskFlag | None:
    letter = _rating_field_value(ratings, "msci_rating")
    sp_score = _to_float(_rating_field_value(ratings, "sp_overall_score"))
    msci_score = msci_letter_to_score(letter)
    if msci_score is None or sp_score is None:
        return None
    diff = abs(msci_score - sp_score)
    if diff < RATING_DIVERGENCE_THRESHOLD:
        return None
    return RiskFlag(
        rule_id="rating_divergence", severity="medium",
        message=f"MSCI（{letter}，粗略折算约 {msci_score:.0f}/100）与 S&P Global"
                f"（{sp_score:.0f}/100）对该公司的评估分歧较大（约 {diff:.0f} 分），"
                f"两家机构方法论权重不同（例如对 Scope 3、供应链治理的权重），"
                f"建议交叉核查具体分项，不要只看单一评级结论。"
                f"（字母-数字折算仅为粗略量级对比，非官方换算，用于提示分歧方向而非精确差值。）",
    )


def evaluate_risks(indicators: dict[str, IndicatorResult], trust_panel: dict,
                    commitments: list[CommitmentQuote], ratings: dict | None) -> list[RiskFlag]:
    candidates = [
        _rule_scope3_dominance(indicators, ratings),
        _rule_supply_chain_gap(indicators, ratings),
        _rule_low_verification_rate(trust_panel),
        _rule_rating_downgrade(ratings),
        _rule_greenwashing_gap(commitments, ratings),
        _rule_rating_divergence(ratings),
    ]
    return [c for c in candidates if c is not None]


# ---------------------------------------------------------------------------
# 单家公司完整分析
# ---------------------------------------------------------------------------

@dataclass
class CompanyAnalysis:
    company: str
    indicators: list[IndicatorResult]
    trust_panel: dict
    commitments: list[CommitmentQuote]
    ratings: dict | None
    risks: list[RiskFlag]


def analyze_company(company: str, chunks: list[dict], cache: dict, ratings_dir: Path,
                     indicator_ids: list[str] | None = None,
                     skip_ratings: bool = False, skip_commitments: bool = False) -> CompanyAnalysis:
    specs = [INDICATOR_BY_ID[i] for i in indicator_ids] if indicator_ids else INDICATORS
    results = [extract_indicator(company, spec, chunks, cache) for spec in specs]
    by_id = {r.indicator_id: r for r in results}
    trust_panel = build_trust_panel(results)
    ratings = None if skip_ratings else load_ratings(company, ratings_dir)
    commitments = [] if skip_commitments else extract_commitments(company, chunks, cache)
    risks = evaluate_risks(by_id, trust_panel, commitments, ratings)
    return CompanyAnalysis(company=company, indicators=results, trust_panel=trust_panel,
                            commitments=commitments, ratings=ratings, risks=risks)


# ---------------------------------------------------------------------------
# 报告渲染
# ---------------------------------------------------------------------------

def _fmt_value_unit(value: str, unit: str | None) -> str:
    """'35' + '%' -> '35%'；'79,400' + 'metric tons CO2e' -> '79,400 metric tons CO2e'。"""
    if not unit:
        return value
    return f"{value}{unit}" if unit.strip().startswith("%") else f"{value} {unit}"


def _rating_trend_str(history) -> str:
    """[{'period':'Dec-20','rating':'A'}, ...] -> 'A→BBB→BBB→BBB→BBB'。"""
    if not isinstance(history, list) or not history:
        return "—"
    try:
        return "→".join(h["rating"] for h in history)
    except Exception:                          # noqa: BLE001
        return "—"


def _dim_cell(dim: dict | None) -> str:
    """{'value':'54','industry_mean':'44','industry_max':'96'} -> '54（行业均值44 / 最高96）'。"""
    if not dim or dim.get("value") is None:
        return "—"
    mean, mx = dim.get("industry_mean"), dim.get("industry_max")
    if mean is not None and mx is not None:
        return f"{dim['value']}（行业均值{mean} / 最高{mx}）"
    return str(dim["value"])


def _cell(result: IndicatorResult) -> str:
    if not result.disclosed:
        return "*未披露*"
    year = f"，{result.year}年" if result.year else ""
    return f"{_fmt_value_unit(result.value, result.unit)}（第{result.page}页{year}）"


def render_markdown_report(analyses: list[CompanyAnalysis]) -> str:
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    companies = [a.company for a in analyses]
    lines = [
        "# ESG 尽调分析报告",
        "",
        f"生成时间：{now}　覆盖公司：{', '.join(companies)}",
        "",
        "## 方法论说明",
        "",
        "每一项指标均由 Agent 对报告原文做「定位（按语义相似度取候选片段）+ 核对（验证标签/口径与问题"
        "完全匹配）」后抽取，附带原文引用与页码；未能定位到明确匹配的指标一律标注为「未披露」，"
        "不做推测或替换为相近数字。评级与漂绿/评级分歧部分依赖 `parse_ratings.py` 从 MSCI / S&P Global "
        "评级 PDF 中抽取的数据，若该数据缺失则相应部分从报告中省略而非编造。",
        "",
        "## 一、核心指标对比",
        "",
    ]

    spec_list = INDICATORS if not analyses else [INDICATOR_BY_ID[r.indicator_id] for r in analyses[0].indicators]
    header = "| 指标 | " + " | ".join(companies) + " |"
    sep = "|---|" + "---|" * len(companies)
    lines += [header, sep]
    for spec in spec_list:
        row = [spec.label_cn]
        for a in analyses:
            r = next((x for x in a.indicators if x.indicator_id == spec.id), None)
            row.append(_cell(r) if r else "—")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    lines += ["## 二、可信度面板", ""]
    for a in analyses:
        tp = a.trust_panel
        lines.append(f"**{a.company}**：{tp['total_indicators']} 个指标中，"
                     f"**{tp['verified_count']} 个已核实**（{tp['verified_pct']}%），"
                     f"**{tp['undisclosed_count']} 个未披露**"
                     + (f"（{ '、'.join(tp['undisclosed_indicators']) }）" if tp["undisclosed_indicators"] else "")
                     + "。")
    lines.append("")

    lines += ["## 三、风险预警", ""]
    for a in analyses:
        lines.append(f"**{a.company}**")
        if not a.risks:
            lines.append("- 未触发预设风险规则（这不代表零风险，只反映本次抽取到的指标组合未命中规则条件）。")
        for r in a.risks:
            tag = {"high": "🔴 高", "medium": "🟡 中", "info": "🔵 提示"}.get(r.severity, r.severity)
            lines.append(f"- [{tag}] {r.message}")
        lines.append("")

    if any(a.ratings for a in analyses):
        lines += ["## 四、第三方评级速览（MSCI / S&P Global）", ""]

        lines += ["**MSCI**", "",
                  "| 公司 | 评级 | 评级趋势（近5次） | Implied Temperature Rise | 目标年份 | 目标覆盖度 | 年降幅 |",
                  "|---|---|---|---|---|---|---|"]
        for a in analyses:
            r = a.ratings or {}
            itr = _rating_field_value(r, "msci_implied_temp_rise_c")
            coverage = _rating_field_value(r, "msci_target_coverage_pct")
            reduction = _rating_field_value(r, "msci_annual_reduction_pct")
            row = [a.company,
                   str(_rating_field_value(r, "msci_rating") or "—"),
                   _rating_trend_str(_rating_field_value(r, "msci_rating_history")),
                   f"{itr}°C" if itr else "—",
                   str(_rating_field_value(r, "msci_target_year") or "—"),
                   f"{coverage}%" if coverage else "—",
                   f"{reduction}% p.a." if reduction else "—"]
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

        lines += ["**S&P Global**", "",
                  "| 公司 | 总分（/100） | CSA 分数 | Modeled 分数 | Environmental | Social | Governance & Economic |",
                  "|---|---|---|---|---|---|---|"]
        for a in analyses:
            r = a.ratings or {}
            dims = _rating_field_value(r, "sp_dimension_scores") or {}
            row = [a.company,
                   str(_rating_field_value(r, "sp_overall_score") or "—"),
                   str(_rating_field_value(r, "sp_csa_score") or "—"),
                   str(_rating_field_value(r, "sp_modeled_score") or "—"),
                   _dim_cell(dims.get("environmental")),
                   _dim_cell(dims.get("social")),
                   _dim_cell(dims.get("governance") or dims.get("governance_economic"))]
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")
        lines.append("（S&P 分项格式为「公司分数（行业均值 / 行业最高）」，用于判断公司在该维度上相对同行的位置。）")
        lines.append("")

    if any(a.commitments for a in analyses):
        lines += ["## 五、承诺话术摘录（用于与评级数据交叉核对）", ""]
        for a in analyses:
            if not a.commitments:
                continue
            lines.append(f"**{a.company}**")
            for c in a.commitments:
                lines.append(f"- 第 {c.page} 页：「{c.quote}」" + (f"（目标年份：{c.target_year}）" if c.target_year else ""))
            lines.append("")

    lines += ["## 六、抽取证据明细（供审计/复核）", ""]
    for a in analyses:
        lines.append(f"**{a.company}**")
        for r in a.indicators:
            if r.disclosed:
                vu = _fmt_value_unit(r.value, r.unit)
                base = f"- {r.label_cn}：{vu}（第{r.page}页，相似度{r.similarity}，第{r.steps_tried}次核对命中）"
                if r.quote:
                    base += f"　原文：「{r.quote}」"
                lines.append(base)
            else:
                base = f"- {r.label_cn}：未披露（已核对{r.steps_tried}个候选片段，均不匹配）"
                lines.append(base)
                if r.partial_table_rows:
                    lines.append(
                        f"　　⚠️ 表格重建定位到 {len(r.partial_table_rows)} 条疑似匹配的表格行，"
                        f"但原文缺少年份表头，无法确认对应哪一年，因此不计入「已核实」，仅供人工核查："
                    )
                    for row in r.partial_table_rows:
                        lines.append(
                            f"　　　- 第{row.get('page')}页「{row.get('label')}」："
                            f"{', '.join(row.get('values', []))}（chunk: {row.get('source_chunk_id')}）"
                        )
        lines.append("")

    lines += [
        "## 七、局限性说明",
        "",
        "本报告的指标覆盖面受限于评测集验证过的三家公司与预设的 7 个核心指标；"
        "「未披露」代表本系统未能在检索到的候选片段中核实到匹配数字，不完全等同于公司确实未披露"
        "（可能存在于未被检索到的段落中，检索命中率并非 100%，详见 DATA_FACTS.md 第5节）。"
        "评级分歧的字母-数字换算为粗略近似，仅用于提示方向，不构成投资建议。",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _print_indicator_result(company: str, r: "IndicatorResult", debug: bool, prefix: str = "    ") -> None:
    status = f"✓ {_fmt_value_unit(r.value, r.unit)}（p.{r.page}）" if r.disclosed else "✗ 未披露"
    print(f"{prefix}[{company}] {r.label_cn}: {status}")
    if not r.disclosed and r.partial_table_rows:
        print(f"{prefix}    ⚠️ 表格重建定位到 {len(r.partial_table_rows)} 条疑似匹配行（年份未确认，未计入已核实）：")
        for row in r.partial_table_rows:
            print(f"{prefix}      - p.{row.get('page')} 「{row.get('label')}」: {row.get('values')}")
    if not r.disclosed and debug:
        for att in r.attempts:
            resp_preview = att["raw_response"].replace(chr(10), " ")[:100]
            print(f"{prefix}    [p.{att['page']} / {att.get('chunk_id')}, sim={att['similarity']}]")
            print(f"{prefix}      片段开头: {att.get('content_preview', '')}")
            print(f"{prefix}      模型回复: {resp_preview}")
            if att.get("rejected_reason"):
                print(f"{prefix}      ⚠️  机械校验拒绝: {att['rejected_reason']}")


def run(companies: list[str], ratings_dir: Path, out_dir: Path,
        indicator_ids: list[str] | None, skip_ratings: bool, skip_commitments: bool,
        debug: bool = False, workers: int = DEFAULT_WORKERS) -> None:
    """
    这是并发版的主流程，思路是这样的：
      - 把"公司x指标"整个拍平成一个任务清单（3家公司 x 7项指标 = 21次
        extract_indicator调用），这些任务互相独立（各自检索、各自调用AI、各自做
        机械校验），可以放心同时跑。"挖承诺话术"（每家公司跑一次）也是独立的，
        一起扔进同一个线程池。
      - extract_indicator()内部那套"一个个候选试、试中就停"的逻辑完全没动——
        同时跑的是外层的任务之间，不是同一个指标内部的多个候选（那部分本来就该
        一个个顺序试，已经找到答案就没必要再跑后面几个，硬要并发反而白花API调用）。
      - 唯一需要小心保护的共享状态是query embedding缓存（看get_query_vec那把锁），
        其它状态（chunks只读，每个IndicatorResult各管各的）天生就是线程安全的，
        不用额外加锁。
      - trust_panel/ratings/risks这些"汇总好几个指标才能算"的东西，要等一家公司
        的全部指标任务都跑完了再算（这些计算本身很快，是纯Python代码，不值得
        并发，其实也没法并发——因为算它们得靠同一家公司全部指标的结果都到齐）。
    """
    cache = _load_query_cache()
    specs = [INDICATOR_BY_ID[i] for i in indicator_ids] if indicator_ids else INDICATORS

    # 一开始就把"PDF坐标级表格重建"这一层到底能不能用检查清楚，大声告诉用户——
    # 之前吃过一次亏：pdfplumber在实际运行的Python环境里没装成功，但它失败的样子
    # 跟"这页PDF本来就没有可用数据"长得一模一样，跑完一整圈真实数据都看不出任何
    # 异常，排查了好几轮才找到真正原因。不能再让这种"看着一切正常、其实整层功能
    # 根本没生效"的情况，在一开始运行的时候就被漏掉不检查。
    try:
        import pdfplumber  # noqa: F401
        print("  [PDF坐标重建] pdfplumber 可用，这一层增强已启用。")
    except ImportError as e:
        print(f"  [PDF坐标重建] ⚠️ 未启用：无法导入 pdfplumber（{e}）。"
              f"这不会导致报错，但 Scope1/2/3 等表格类指标会退回到旧流程，"
              f"很可能重新看到大量'未披露'。请在当前这个 Python 环境里执行 "
              f"`pip install pdfplumber` 后再跑。")

    print(f"=== 并发抽取：{len(companies)} 家公司 x {len(specs)} 个指标，{workers} 个线程 ===")
    company_chunks: dict[str, list[dict]] = {}
    for company in companies:
        chunks = load_company_chunks(company)
        company_chunks[company] = chunks
        print(f"  {company}: 语料 {len(chunks)} chunk")

    indicator_results: dict[str, dict[str, IndicatorResult]] = {c: {} for c in companies}
    commitments_results: dict[str, list[CommitmentQuote]] = {c: [] for c in companies}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for company in companies:
            chunks = company_chunks[company]
            for spec in specs:
                fut = executor.submit(extract_indicator, company, spec, chunks, cache)
                futures[fut] = ("indicator", company, spec.id)
            if not skip_commitments:
                fut = executor.submit(extract_commitments, company, chunks, cache)
                futures[fut] = ("commitments", company, None)

        total = len(futures)
        print(f"  已提交 {total} 个任务，等待完成...\n")
        done = 0
        for fut in as_completed(futures):
            kind, company, ident = futures[fut]
            done += 1
            try:
                result = fut.result()
            except Exception as e:                      # noqa: BLE001
                # 一个任务出错（网络问题、API报错什么的）不该拖累其它任务——记一下
                # 错误、接着跑剩下的任务就行，最后这一项在indicator_results里会是
                # 空的，下面组装报告的时候得能容忍"这个指标压根没结果"的情况
                # （处理办法是标成未披露，再附一句错误说明）。
                print(f"  [{done}/{total}] ✗ [{company}] {ident or '承诺话术'} 任务出错：{e}")
                if kind == "indicator":
                    indicator_results[company][ident] = IndicatorResult(
                        company=company, indicator_id=ident,
                        label_cn=INDICATOR_BY_ID[ident].label_cn,
                        category=INDICATOR_BY_ID[ident].category,
                        disclosed=False, note=f"抽取过程出错，未完成核对：{e}",
                    )
                continue
            if kind == "indicator":
                indicator_results[company][ident] = result
                _print_indicator_result(company, result, debug, prefix=f"  [{done}/{total}] ")
            else:
                commitments_results[company] = result
                print(f"  [{done}/{total}] [{company}] 承诺话术: 找到 {len(result)} 条")

    analyses = []
    for company in companies:
        results = [indicator_results[company][spec.id] for spec in specs]
        by_id = {r.indicator_id: r for r in results}
        trust_panel = build_trust_panel(results)
        ratings = None if skip_ratings else load_ratings(company, ratings_dir)
        commitments = commitments_results[company]
        risks = evaluate_risks(by_id, trust_panel, commitments, ratings)
        analyses.append(CompanyAnalysis(company=company, indicators=results, trust_panel=trust_panel,
                                         commitments=commitments, ratings=ratings, risks=risks))

    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "esg_comparison.json"
    json_path.write_text(json.dumps(
        [{
            "company": a.company,
            "indicators": [asdict(r) for r in a.indicators],
            "trust_panel": a.trust_panel,
            "commitments": [asdict(c) for c in a.commitments],
            "ratings": a.ratings,
            "risks": [asdict(r) for r in a.risks],
        } for a in analyses], ensure_ascii=False, indent=2), encoding="utf-8")

    md_path = out_dir / "esg_comparison_report.md"
    md_path.write_text(render_markdown_report(analyses), encoding="utf-8")

    print(f"\n完成。结构化数据 -> {json_path.relative_to(ROOT)}")
    print(f"对比报告 -> {md_path.relative_to(ROOT)}")


# ---------------------------------------------------------------------------
# 离线自检：不用联网、不用API key，就能确认代码逻辑本身没写错
# ---------------------------------------------------------------------------

def run_selftest() -> int:
    """
    用可控的假embedding、假AI回复函数替掉真实的云端调用，造一份"我自己知道正确答案
    是什么"的语料，完整跑一遍流程，每一步都用断言检查结果对不对。

    假embedding是怎么设计的：把每个"概念"对应到一个固定维度上（这一维是1、其它维
    是接近0的小噪声），query和chunk的向量都按同样的规则、从文本里的关键词生成——
    这样检索排出来的顺序完全可以预测，不依赖任何真实模型，也不会因为随机数种子不同
    而结果不稳定。
    假的AI回复函数是怎么设计的：模拟AI在VERIFY这一步真正该做的事——检查这段文字
    里有没有这个指标的关键词，有就算"找到"，没有就回复"没找到"；这样就能真实测到
    "指标压根不存在的时候，系统会老实说'未披露'，而不是随便抓一个数字充数"这条
    这个项目最看重的底线。
    """
    # 注意：这里不能写`import analyst`——当这个文件是用`python src/analyst.py`
    # 这种方式、作为主程序运行的时候，`import analyst`会因为src/目录在
    # sys.path里，又重新加载出"另外一份"模块实例，后面打的猴子补丁是打在那份
    # 新实例上的，主程序里真正在跑的函数完全看不到这些补丁。要用
    # sys.modules[__name__]拿到"当前正在运行的这一份"模块才对。
    self_mod = sys.modules[__name__]

    concepts = ["scope 1", "scope 2", "scope 3", "renewable", "water withdrawal",
                "waste diversion", "supplier", "net zero"]

    def fake_embed(text: str) -> list[float]:
        low = text.lower()
        vec = [0.05] * len(concepts)
        for i, kw in enumerate(concepts):
            if kw in low:
                vec[i] = 1.0
        return vec

    def fake_gen(system: str, user: str) -> str:
        m = re.search(r"Passage \(from page (\d+)\):\n(.*?)\n\n(?:Question|If this passage)",
                       user, flags=re.DOTALL)
        passage = m.group(2) if m else user
        low = passage.lower()
        if "Matching rule:" in user:
            # 这是在模拟EXTRACT_TMPL这个prompt模板："标签必须对得上"，从段落里找
            # 一个"数字紧跟着单位"的模式。要求数字和单位挨在一起，是为了避免误配到
            # "Scope 1"这几个字里的那个"1"——真实的AI显然不会犯这种低级错，这里
            # 只是让这个假函数的行为看起来合理点，不要给自检脚本挖一个跟真实AI
            # 完全无关的假坑。
            num_m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(metric tons CO2e|%|cubic meters)", passage)
            label_m = re.search(r"Question: What is .+?\((.+?)\)\?", user)
            target = (label_m.group(1) if label_m else "").lower()
            keyword_map = {
                "scope 1": "scope 1", "scope 2": "scope 2", "scope 3": "scope 3",
                "可再生能源占比": "renewable", "总取水量": "water withdrawal",
                "废弃物转移率": "waste diversion", "供应链排放": "supplier",
            }
            required_kw = next((v for k, v in keyword_map.items() if k in target), None)
            if required_kw and required_kw in low and num_m:
                year_m = re.search(r"(20\d\d)", passage)
                return json.dumps({"value": num_m.group(1), "unit": num_m.group(2) or "",
                                    "year": year_m.group(1) if year_m else None,
                                    "quote": passage.strip()[:150]})
            return "NOT_IN_PASSAGE"
        else:
            # 这是在模拟COMMIT_TMPL这个prompt模板
            if "net zero" in low or "carbon neutral" in low:
                year_m = re.search(r"by (20\d\d)", passage)
                return json.dumps({"commitment": passage.strip()[:150],
                                    "target_year": year_m.group(1) if year_m else None})
            return "NOT_IN_PASSAGE"

    self_mod.cloud_embed = fake_embed
    self_mod.cloud_gen = fake_gen
    # 这一行是修一个关键问题的：get_query_vec()内部不管传进来的cache是不是自检
    # 用的临时字典，都会无条件调用_save_query_cache()把它写到硬盘上那个跟真实
    # 运行共用的缓存文件（QUERY_EMB_CACHE）里。这意味着不打这个补丁的话，每次跑
    # --selftest都会把这里假造的8维向量覆盖写进真实缓存，污染下一次真实运行的
    # 检索结果——这正是之前排查过的"相似度全都诡异地趋近于0"那次问题的真正根源
    # （当时只是把缓存文件删了绕过去，没有真正揪出这个会反复发作的病根）。自检
    # 必须做到完全只读、不碰任何真实文件。
    self_mod._save_query_cache = lambda cache: None

    chunk_defs = [
        (10, "Scope 1 direct greenhouse gas emissions were 79,400 metric tons CO2e in 2023."),
        (11, "Scope 2 market-based emissions were 120,000 metric tons CO2e in 2023."),
        (12, "Scope 3 value chain emissions totaled 5,000,000 metric tons CO2e in 2023, "
             "driven mainly by purchased goods and services."),
        (20, "35% of our global operations were powered by renewable electricity in 2023."),
        (25, "Total water withdrawal across all facilities was 1,200,000 cubic meters in 2023."),
        (40, "We are committed to achieving net zero emissions across our value chain by 2030."),
        # 这里故意不放废弃物/供应商相关的内容 -> 这两项应该被判定成"未披露"
    ]
    chunks = [{"chunk_id": f"TestCo-{i}", "page": p, "content": c, "vec": fake_embed(c)}
              for i, (p, c) in enumerate(chunk_defs)]

    ratings_low_coverage = {
        "msci_rating": {"value": "AA"},
        "msci_implied_temp_rise_c": {"value": "1.5"},
        "msci_target_coverage_pct": {"value": "8.13"},
        "msci_rating_history": {"value": [{"period": "2022-01", "rating": "A"},
                                           {"period": "2024-01", "rating": "B"}]},   # 故意造一次评级下调
        "sp_overall_score": {"value": "38"},
    }

    cache: dict = {}
    print("=== [自检 1] 指标抽取：应该找到 6 项，2 项标未披露 ===")
    results = [extract_indicator("TestCo", spec, chunks, cache) for spec in INDICATORS]
    by_id = {r.indicator_id: r for r in results}

    assert by_id["scope1_emissions"].disclosed and by_id["scope1_emissions"].numeric_value == 79400.0, \
        by_id["scope1_emissions"]
    assert by_id["scope1_emissions"].page == 10
    assert by_id["scope2_emissions"].numeric_value == 120000.0
    assert by_id["scope3_emissions"].numeric_value == 5000000.0
    assert by_id["renewable_energy_pct"].value == "35"
    assert by_id["water_withdrawal"].numeric_value == 1200000.0
    assert by_id["waste_diversion_pct"].disclosed is False, "语料里没有废弃物数据，必须标未披露而不是瞎猜"
    assert by_id["supply_chain_emissions"].disclosed is False, "语料里没有供应链数据，必须标未披露"
    print("  通过：4 项排放/能源/用水正确抽取带页码，2 项正确标记未披露。")

    print("=== [自检 2] 可信度面板计算 ===")
    tp = build_trust_panel(results)
    assert tp["total_indicators"] == 7
    assert tp["verified_count"] == 5
    assert tp["undisclosed_count"] == 2
    assert tp["verified_pct"] == round(100 * 5 / 7, 1)
    print(f"  通过：{tp['verified_count']}/{tp['total_indicators']} 已核实 ({tp['verified_pct']}%)。")

    print("=== [自检 3] 承诺话术抽取 ===")
    commitments = extract_commitments("TestCo", chunks, cache)
    assert len(commitments) >= 1 and "net zero" in commitments[0].quote.lower()
    assert commitments[0].target_year == "2030"
    print(f"  通过：抽到承诺「{commitments[0].quote}」，目标年份 {commitments[0].target_year}。")

    print("=== [自检 4] 风险规则引擎 ===")
    risks = evaluate_risks(by_id, tp, commitments, ratings_low_coverage)
    rule_ids = {r.rule_id for r in risks}
    assert "scope3_dominance" in rule_ids, "Scope3远超Scope1+2且覆盖度8.13%<50%，必须触发转型风险"
    assert "supply_chain_gap" in rule_ids, "供应链数据缺失，必须触发尽调重点提示"
    assert "greenwashing_gap" in rule_ids, "有net zero承诺但覆盖度8.13%<30%，必须触发漂绿风险"
    assert "rating_downgrade" in rule_ids, "评级历史 A->B 是降级，必须触发"
    assert "rating_divergence" in rule_ids, "MSCI AA(约79)与S&P 38分歧巨大，必须触发"
    print(f"  通过：触发规则 {sorted(rule_ids)}。")

    print("=== [自检 5] JSON 解析健壮性（模拟模型输出被 markdown 代码块包裹）===")
    wrapped = "```json\n" + json.dumps({"value": "42", "unit": "%", "year": "2023", "quote": "x"}) + "\n```"
    parsed = _parse_json_response(wrapped)
    assert parsed and parsed["value"] == "42", parsed
    broken = 'here is the answer: {"value": "56", "unit": "%", "quote": "..."} hope this helps'
    parsed2 = _parse_json_response(broken)
    assert parsed2 and parsed2["value"] == "56", parsed2
    print("  通过：代码块包裹、模型多话包裹两种畸形响应都能正确兜底解析。")

    print("=== [自检 5.5] _to_float认识数量级单位词的回归测试（真实数据跑出来才发现的坑）===")
    # 背景：加完"value必须是数字"这道新关卡之后，真实数据里Alphabet两条本来完全
    # 抽对的结果（"10.8 million"、"3.4 million"）被误杀了——原因是_to_float原来
    # 只认识"79,400"这种纯数字加逗号的写法，一碰到AI照原文写出来的"X million/
    # billion"这种带英文数量级单位的写法就直接返回None，新关卡就把它们当成"不是
    # 数字"给拒了。这是自己加新校验时顺带引入的问题，不是数据本身有错，修法是让
    # _to_float认识这几个常见的数量级单位词，而不是把新校验放松。这里把真实踩过的
    # 这两个值直接断言一遍，以后谁不小心改坏了_to_float、又踩到同样的坑，这条自检
    # 会立刻报错提醒。
    assert _to_float("10.8 million") == 10_800_000.0
    assert _to_float("3.4 million") == 3_400_000.0
    assert _to_float("6.4 billion") == 6_400_000_000.0
    assert _to_float("55,200") == 55200.0, "普通带逗号的整数不能受影响"
    assert _to_float("82%") == 82.0, "百分号不能受影响"
    assert _to_float("Not exceeding our 2021 baseline") is None, "真正非数字的话术必须仍然拒绝"
    print("  通过：million/billion 量级单位词能正确换算，纯数字/百分号写法不受影响，"
          "真正的非数字话术依然被拒绝。")

    print("=== [自检 6] Markdown 报告渲染不报错，且关键内容都在 ===")
    analysis = CompanyAnalysis(company="TestCo", indicators=results, trust_panel=tp,
                                commitments=commitments, ratings=ratings_low_coverage, risks=risks)
    md = render_markdown_report([analysis])
    assert "79,400" in md, "报告里应能看到抽取到的具体数值"
    assert "未披露" in md, "报告里应能看到未披露指标的标注"
    assert "转型风险" in md, "Scope3 占比过高的风险预警文案应出现在报告里"
    assert "漂绿风险" in md, "漂绿风险预警文案应出现在报告里"
    assert "可信度面板" in md and "风险预警" in md and "TestCo" in md
    print(f"  通过：报告长度 {len(md)} 字符，各章节标题齐全。")

    print("=== [自检 7] value必须是数字这道校验（回归测试：Meta真实数据跑出来的那次事故）===")
    # 背景：TOP_K从10提到20之后，真实数据里出现过一次AI把"减排目标承诺"（"Not
    # exceeding our 2021 baseline Scope 3 emissions by the end of 2031"）错当成
    # "排放数值"报出来，而且骗过了前面四道机械关卡（quote是原文里真实存在的文字、
    # unit加value拼一起能匹配上"co2"这个词、quote里确实有"Scope 3"字样、value
    # 文本跟关键词也挨得近）。这里直接模拟这个具体场景，确认新加的"value必须能
    # 转成数字"这道校验能拦住它，而且以后谁不小心把这道检查删掉或改坏了，这条
    # 自检会立刻报错，不用等真实数据再暴露一次同样的问题。
    def fake_gen_bad_value(system: str, user: str) -> str:
        if "Matching rule:" in user and "Scope 3" in user:
            return json.dumps({
                "value": "Not exceeding our 2021 baseline",
                "unit": "tons CO2e",
                "year": "by the end of 2031",
                "quote": "Not exceeding our 2021 baseline Scope 3 emissions by the end of 2031.",
            })
        return "NOT_IN_PASSAGE"

    self_mod.cloud_gen = fake_gen_bad_value
    bad_chunk = [{"chunk_id": "TestCo-bad-0", "page": 99,
                  "content": "Not exceeding our 2021 baseline Scope 3 emissions by the end of 2031.",
                  "vec": fake_embed("Not exceeding our 2021 baseline Scope 3 emissions by the end of 2031.")}]
    bad_result = extract_indicator("TestCo", INDICATOR_BY_ID["scope3_emissions"], bad_chunk, cache)
    assert bad_result.disclosed is False, \
        "承诺目标话术被误判成数值，value_not_numeric 这道校验没有生效——回归了！"
    assert any(a.get("rejected_reason", "").startswith("value_not_numeric")
               for a in bad_result.attempts), bad_result.attempts
    self_mod.cloud_gen = fake_gen   # 恢复，避免影响后面（如果以后还有新自检追加在这之后）
    print("  通过：把「减排目标承诺」误当成「排放数值」的候选会被正确拒绝，不会污染报告。")

    print("=== [自检 8] 表格重建：用Meta真实数据里踩过坑的那张表做回归测试 ===")
    # 用的是真实抓下来的Meta第78页原文（一个字都没改），这张表就是这个模块存在的
    # 直接原因：开头一段"Environmental footprint1,2,3,4,5,6 / 1.1 GHG emissions /
    # Total GHG emissions / Market-based(...) / Net total"其实是文档标题加小节
    # 文字，紧跟着的5个数字全都属于"Net total"这一行——第一版算法在这里真犯过错，
    # 把标题也当成了表格的行标签，具体看table_reconstruct.py里
    # _is_eligible_label_line的说明。这里锁定两件必须成立的事：一是"Net total"
    # 要被正确恢复成一行、5个数值一个不多一个不少；二是紧跟着的"Total/Scope1/
    # Scope2/Scope3"这一组，因为数字总数（19个，这是原文数据本身缺失导致的，不是
    # 代码的锅）除不尽标签数（4个），必须被放弃、不能硬拆出一组错误对齐的行——
    # 这条要是测不过了，说明校验又开始"宁可猜错也要凑够数字"了。
    meta_p78_content = (
        "Environmental footprint1,2,3,4,5,6\n\n1.1 GHG emissions\n\nTotal GHG emissions\n\n"
        "Market-based (in metric tons CO2e)\n\nNet total\n\n4,330,000\n\n4,984,000\n\n"
        "5,740,244\n\n8,453,471\n\n7,443,182\n\nCarbon removal (carbon credits\napplied)\n\n"
        "-\n\n145,000\n\n90,000\n\n80,000\n\n53,050\n\nTotal\n\nScope 1\n\nScope 2\n\nScope 3\n\n"
        "4,330,000\n\n5,129,000\n\n5,830,244\n\n8,533,471\n\n7,496,232\n\n44,000\n\n29,000\n\n"
        "208,000\n\n9,000\n\n55,173\n\n2,487\n\n66,934\n\n48,952\n\n1,658\n\n4,078,000\n\n"
        "5,091,000\n\n5,772,583\n\n8,466,264\n\n7,445,621\n\nLocation-based (in metric tons CO2e)"
        "\n\nTotal\n\n6,295,000\n\n8,559,000\n\n10,163,476\n\n14,007,222\n\n14,067,104\n"
    )
    meta_chunk = {"chunk_id": "TestMeta-p078-c00", "page": 78, "content": meta_p78_content}
    recon_rows = reconstruct_flattened_table(meta_chunk)
    net_total_rows = [r for r in recon_rows if r.label == "Net total"]
    assert len(net_total_rows) == 1, f"'Net total' 应该被恢复成恰好一行，实际: {recon_rows}"
    assert net_total_rows[0].values == ["4,330,000", "4,984,000", "5,740,244", "8,453,471", "7,443,182"], \
        net_total_rows[0].values
    scope_rows = [r for r in recon_rows if r.label in ("Scope 1", "Scope 2", "Scope 3", "Total")
                  and r.confidence == "grid_exact_divide"]
    assert scope_rows == [], (
        "19个数字除不尽4个标签，这组必须被放弃、不能强行拆分成错误对齐的行，"
        f"实际却恢复出了: {scope_rows}"
    )
    print(f"  通过：'Net total' 正确恢复（5个数值不多不少），"
          f"'Total/Scope1/Scope2/Scope3' 因除不尽被正确放弃（不强行拆分）。")

    print("=== [自检 9] 表格重建接入extract_indicator的完整流程（年份确认/未确认两种情况）===")
    # 造一张"假的但结构很典型"的表：先一行占位标签（让代码里的seen_number_block
    # 变成True之后，Scope 1/2/3这组连续标签才会被信任），紧跟着Scope 1/2/3三行、
    # 每行3年的数值，同一页另一个片段里放着数量刚好对得上的年份行（2021/2022/
    # 2023）——用来验证"年份能确认"这条路能顺利走完整个EXTRACT_TMPL加四层校验，
    # 产出disclosed=True，而且数值和年份都精确对应，不是蒙出来的。
    table_chunk_confirmed = {
        "chunk_id": "TestCo3-p050-c00", "page": 50,
        "content": (
            "GHG emissions summary (in metric tons CO2e)\n\nSome preceding row\n\n1\n\n2\n\n"
            "Scope 1\n\nScope 2\n\nScope 3\n\n100\n\n200\n\n300\n\n400\n\n500\n\n600\n\n"
            "700\n\n800\n\n900\n"
        ),
    }
    year_chunk = {"chunk_id": "TestCo3-p050-c01", "page": 50, "content": "2021\n\n2022\n\n2023\n"}
    table_chunk_confirmed["vec"] = fake_embed(table_chunk_confirmed["content"])
    year_chunk["vec"] = fake_embed(year_chunk["content"])

    def fake_gen_table_confirmed(system: str, user: str) -> str:
        if "reconstructed from a table" not in user:
            return "NOT_IN_PASSAGE"
        # 模拟AI的行为：从"Passage (from page N):\n...\n\nQuestion:"这一段真正的
        # 正文里（不是从整段prompt的指令文字里）老老实实抄出"2023"这一年对应的
        # 数值和单位。
        passage_m = re.search(r"Passage \(from page \d+\):\n(.*?)\n\nQuestion:", user, flags=re.DOTALL)
        passage = passage_m.group(1) if passage_m else ""
        m = re.search(r"2023:\s*([\d,]+)", passage)
        if not m:
            return "NOT_IN_PASSAGE"
        return json.dumps({
            "value": m.group(1), "unit": "metric tons CO2e", "year": "2023",
            "quote": passage,
        })

    self_mod.cloud_gen = fake_gen_table_confirmed
    confirmed_result = extract_indicator(
        "TestCo3", INDICATOR_BY_ID["scope1_emissions"], [table_chunk_confirmed, year_chunk], cache
    )
    assert confirmed_result.disclosed is True, \
        f"年份能精确对上的重建结果应该被采信，实际: disclosed={confirmed_result.disclosed}, " \
        f"attempts={confirmed_result.attempts}"
    assert confirmed_result.numeric_value == 300.0, \
        f"应该取到 Scope 1 对应 2023 年的值 300（years=[2021,2022,2023] 顺序对应 " \
        f"values=[100,200,300]，不是100/200这两个别的年份），实际: {confirmed_result.value}"
    assert confirmed_result.year == "2023"
    print(f"  通过（年份已确认）：表格重建 + 年份对齐 + 四层机械校验全链路跑通，"
          f"精确取到 Scope 1 在 2023 年的值 {confirmed_result.value}，不是蒙的。")

    # 接着验证"年份对不上"的情况：还是同样的表，但这次同一页没有可用的年份行——
    # 这时候必须保持disclosed=False（绝不允许猜"排第一个的数字就是今年"），但要
    # 把定位到的跨年数值原样报告在partial_table_rows里，而不是像以前那样什么
    # 信息都不给。
    def fake_gen_never_found(system: str, user: str) -> str:
        return "NOT_IN_PASSAGE"

    self_mod.cloud_gen = fake_gen_never_found
    unconfirmed_result = extract_indicator(
        "TestCo3", INDICATOR_BY_ID["scope1_emissions"], [table_chunk_confirmed], cache
    )
    assert unconfirmed_result.disclosed is False, \
        "同页找不到年份行时，绝不能凭空认定某个数字就是当前年份，必须保持未披露"
    assert len(unconfirmed_result.partial_table_rows) >= 1, \
        "虽然不采信为已核实，但定位到的表格行必须原样保留在 partial_table_rows 里，不能丢掉这个信息"
    assert unconfirmed_result.partial_table_rows[0]["label"] == "Scope 1"
    assert unconfirmed_result.partial_table_rows[0]["values"] == ["100", "200", "300"]
    self_mod.cloud_gen = fake_gen   # 恢复
    print(f"  通过（年份未确认）：定位到 Scope 1 这一行但年份对不上时，"
          f"保持未披露（不瞎猜），同时把跨期数值 {unconfirmed_result.partial_table_rows[0]['values']} "
          f"如实保留在 partial_table_rows 里供人工核查。")

    print("=== [自检 10] PDF版面坐标级表格重建（新的一层：直接读原始PDF，不用先把文字拍扁）===")

    def gw(text: str, x0: float, top: float) -> dict:
        return {"text": text, "x0": x0, "top": top}

    # 10.1 纯逻辑单元测试：模拟Meta第78页真实的版面（年份表头加Scope1/2/3三行，
    # Scope 2故意缺2022年的数值、用"-"占位——真实数据里这正是当年"19个数字除不尽
    # 4个标签"那个谜团的答案），另外加两个必须被正确放弃的干扰行。
    geom_words = []
    for i, y in enumerate([2019, 2020, 2021, 2022, 2023]):
        geom_words.append(gw(str(y), 180 + 81 * i, 372.9))
    geom_words += [gw("Scope", 37, 400), gw("1", 60, 400)]
    for i, v in enumerate(["44,000", "29,000", "55,173", "66,934", "48,952"]):
        geom_words.append(gw(v, 180 + 81 * i, 400))
    geom_words += [gw("Scope", 37, 421), gw("2", 60, 421)]
    for i, v in enumerate(["208,000", "9,000", "2,487", "-", "1,658"]):
        geom_words.append(gw(v, 180 + 81 * i, 421))
    geom_words += [gw("Scope", 37, 442), gw("3", 60, 442)]
    for i, v in enumerate(["4,078,000", "5,091,000", "5,772,583", "8,466,264", "7,445,621"]):
        geom_words.append(gw(v, 180 + 81 * i, 442))
    # 干扰1：标签跟数字没有落在同一行（因为换行了），这行必须整个放弃，不能瞎猜
    geom_words += [gw("Carbon", 37, 460), gw("removal", 70, 460), gw("145,000", 180, 470)]
    # 干扰2：有一个数字明显对不上任何表头列（横向偏移超过了容许范围X_TOL），这行也必须整个放弃
    geom_words += [gw("Scope", 37, 490), gw("9", 60, 490), gw("999", 195, 490)]
    for i, v in enumerate(["11,000", "22,000", "33,000", "44,000"], start=1):
        geom_words.append(gw(v, 180 + 81 * i, 490))

    pdf_geometry_reconstruct.extract_page_words = lambda pdf_path, page_no: geom_words
    geom_rows = reconstruct_table_by_geometry(
        "fake.pdf", 78, ("scope 1", "scope1", "scope 2", "scope2", "scope 3", "scope3",
                          "scope 9", "scope9"),
    )
    geom_by_label = {r.label: r.year_values for r in geom_rows}
    assert geom_by_label.get("Scope 1") == {
        2019: "44,000", 2020: "29,000", 2021: "55,173", 2022: "66,934", 2023: "48,952",
    }, f"Scope 1 应该5年全部精确对齐，实际: {geom_by_label.get('Scope 1')}"
    assert geom_by_label.get("Scope 2") == {
        2019: "208,000", 2020: "9,000", 2021: "2,487", 2023: "1,658",
    }, f"Scope 2 应该缺 2022 年（原文是'-'占位，不该被瞎猜出一个值），实际: {geom_by_label.get('Scope 2')}"
    assert geom_by_label.get("Scope 3") == {
        2019: "4,078,000", 2020: "5,091,000", 2021: "5,772,583", 2022: "8,466,264", 2023: "7,445,621",
    }, f"Scope 3 应该5年全部精确对齐，实际: {geom_by_label.get('Scope 3')}"
    assert "Carbon removal" not in geom_by_label, "标签没跟数字同框的行必须被放弃，不能瞎猜"
    assert "Scope 9" not in geom_by_label, "有数字对不上任何表头列的行必须被整行放弃，不能硬凑"
    print(f"  通过：Scope 1/2/3 三行坐标精确对齐（Scope 2 正确缺 2022 年，不瞎猜），"
          f"标签未同框、数字错位这两种歧义情况都被正确放弃，不产出任何结果。")

    # 10.1.1 回归测试：Apple真实PDF踩过的坑——双栏排版，右边脚注文字刚好跟左边
    # 数据表的表头落在同一条横线上，"整行必须全是年份"这个判断会因此把表头误认成
    # "这不是表头"，导致下面所有数据行都没有表头可用，整页数据全部漏掉。这里用
    # 简化过的真实场景复现一遍（表头行混进了右边脚注的词，数据行也混进了脚注的
    # 词），确认改成"找连续的年份串"这个判断方式之后，能正确无视脚注、认出表头，
    # 数据行也不会被脚注干扰。
    sidebar_words = [
        gw("•", 759.9, 190.2), gw("For", 768.9, 190.2), gw("data", 779.0, 190.2),
        gw("on", 792.3, 190.2), gw("years", 800.5, 190.2),
    ]
    contaminated_header_row = [gw(str(y), 442.9 + 60.1 * i, 190.2)
                                for i, y in enumerate([2023, 2022, 2021, 2020, 2019])] + sidebar_words
    contaminated_data_row = (
        [gw("Total", 189.4, 306.8), gw("gross", 207.2, 306.8), gw("scope", 227.3, 306.8),
         gw("3", 249.0, 306.8), gw("emissions", 255.1, 306.8)]
        + [gw(v, 442.9 + 60.1 * i, 306.8)
           for i, v in enumerate(["15,980,000", "20,545,800", "23,128,400", "22,550,000", "24,980,000"])]
        + [gw("see", 768.9, 306.8), gw("the", 779.8, 306.8), gw("table", 830.0, 306.8)]
    )
    pdf_geometry_reconstruct.extract_page_words = (
        lambda pdf_path, page_no: contaminated_header_row + contaminated_data_row
    )
    sidebar_rows = reconstruct_table_by_geometry("fake.pdf", 77, ("scope 3", "scope3"))
    sidebar_by_label = {r.label: r.year_values for r in sidebar_rows}
    assert "Total gross scope 3 emissions" in sidebar_by_label, (
        f"双栏排版下（表头行、数据行都混进了右侧脚注文字）依然应该能正确识别出表头、"
        f"恢复出数据行，实际恢复到的标签: {list(sidebar_by_label.keys())}"
    )
    assert sidebar_by_label["Total gross scope 3 emissions"] == {
        2023: "15,980,000", 2022: "20,545,800", 2021: "23,128,400",
        2020: "22,550,000", 2019: "24,980,000",
    }, f"实际: {sidebar_by_label['Total gross scope 3 emissions']}"
    print(f"  通过（双栏排版回归测试）：右侧脚注文字跟表头/数据行落在同一纵坐标上时，"
          f"依然能正确识别表头、恢复数据，不被无关脚注干扰。")

    # 10.2 接入extract_indicator的完整流程：确认坐标重建出来的候选真的会被送进
    # EXTRACT_TMPL加四层机械校验走一遍，不是绕开校验直接采信。
    # （这里重新指回10.1那份Scope1/2/3数据——上面10.1.1那个双栏回归测试临时改了
    # extract_page_words的返回内容，用完了要切回来，不然会影响这一步的结果。）
    pdf_geometry_reconstruct.extract_page_words = lambda pdf_path, page_no: geom_words
    geom_chunk = {
        "chunk_id": "TestCo4-p099-c00", "page": 99,
        "content": "Corporate emissions Scope 1 direct emissions data table follows.",
    }
    geom_chunk["vec"] = fake_embed(geom_chunk["content"])

    def fake_resolve_pdf_path(company: str):
        return "fake.pdf" if company == "TestCo4" else None

    self_mod.resolve_pdf_path = fake_resolve_pdf_path

    def fake_gen_geometry(system: str, user: str) -> str:
        passage_m = re.search(r"Passage \(from page \d+\):\n(.*?)\n\nQuestion:", user, flags=re.DOTALL)
        passage = passage_m.group(1) if passage_m else ""
        m = re.search(r"2023:\s*([\d,]+)", passage)
        if not m:
            return "NOT_IN_PASSAGE"
        return json.dumps({"value": m.group(1), "unit": "metric tons CO2e", "year": "2023", "quote": passage})

    self_mod.cloud_gen = fake_gen_geometry
    geom_result = extract_indicator("TestCo4", INDICATOR_BY_ID["scope1_emissions"], [geom_chunk], cache)
    self_mod.cloud_gen = fake_gen        # 恢复
    self_mod.resolve_pdf_path = resolve_pdf_path   # 恢复
    assert geom_result.disclosed is True, \
        f"坐标重建出来的候选应该能走完整链路被采信，实际: disclosed={geom_result.disclosed}, " \
        f"attempts={geom_result.attempts}"
    assert geom_result.numeric_value == 48952.0, \
        f"应该精确取到 Scope 1 在 2023 年的坐标对齐值 48,952，实际: {geom_result.value}"
    print(f"  通过：坐标重建候选正常送入 EXTRACT_TMPL + 四层机械校验全链路，"
          f"精确取到 Scope 1 在 2023 年的值 {geom_result.value}，没有绕开任何一道既有校验。")

    print("=== [自检 11] 合成文本里单位说明位置的回归测试（真实数据跑出来才发现的坑：Meta真实数据"
          "触发过一次）===")
    # 真实事故复现：Meta真实数据里，AI看到合成文本后半段提到"(in metric tons CO2e)"，
    # 很合理地把单位写进了它汇报的quote里（比如"Scope 1 in 2022: 66,934 (in metric
    # tons CO2e)"），但当时拼出来的合成文本是把单位说明放在全文最后，跟每条
    # "年份: 数值"是断开的，导致这个内容上完全正确的quote，在原文里根本找不到
    # 连续的匹配，被第①道机械校验误杀——数字对、年份也对，就因为自己拼文本时的
    # 结构问题，白白丢了一个本该采信的真实结果。这里验证修复之后：单位说明紧跟在
    # 每个数值后面，AI任何正常的quote写法都应该能在合成文本里找到连续匹配。
    geo_row = GeometricRow(label="Scope 1", year_values={2019: "44,000", 2022: "66,934"}, page=78)
    geo_synths = rows_to_synthetic_chunk_geometry(geo_row, unit_context="(in metric tons CO2e)")
    assert [c["year"] for c in geo_synths] == [2022, 2019], (
        f"现在每年应该拆成独立候选、按从新到旧排序返回，实际: {[c['year'] for c in geo_synths]}"
    )
    realistic_quote = "Scope 1 in 2022: 66,934 (in metric tons CO2e)"
    geo_2022 = next(c for c in geo_synths if c["year"] == 2022)
    assert realistic_quote in geo_2022["content"], (
        f"模型很自然会把单位一起写进 quote 里，合成文本必须让这种写法能找到连续匹配，"
        f"实际合成文本: {geo_2022['content']}"
    )

    from table_reconstruct import ReconstructedRow
    tr_row = ReconstructedRow(label="Scope 2", values=["208,000", "9,000", "273"],
                               n_cols=3, source_chunk_id="TestCo5-p078-c00", page=78,
                               confidence="single_label")
    tr_synths = rows_to_synthetic_chunk(tr_row, [2019, 2020, 2022], unit_context="(in metric tons CO2e)")
    assert [c["year"] for c in tr_synths] == [2022, 2020, 2019], (
        f"table_reconstruct.py 这一路同样应该按从新到旧拆分排序，实际: {[c['year'] for c in tr_synths]}"
    )
    realistic_quote2 = "Scope 2 in 2022: 273 (in metric tons CO2e)"
    tr_2022 = next(c for c in tr_synths if c["year"] == 2022)
    assert realistic_quote2 in tr_2022["content"], (
        f"table_reconstruct.py 这一路的合成文本也是同样的坑，同样要修，"
        f"实际合成文本: {tr_2022['content']}"
    )
    print(f"  通过：坐标重建、文字模式重建这两条路生成的合成文本，单位说明都紧跟在每个"
          f"数值后面，AI自然的quote写法（数值和单位连在一起）能在原文里找到连续匹配，"
          f"不会被第①道机械校验误杀掉本该采信的真实结果。")

    print("=== [自检 12] 合计数优先排序的回归测试（真实数据跑出来的坑：Apple Scope 3抓到了"
          "子类目而不是合计数）===")
    # 真实事故复现：Apple第77页同时有两行——"Product life Gross emissions (Scope 3)"
    # （产品口径的子类目，数字是真的，但不是这个指标要的那个）和"Total gross scope 3
    # emissions (corporate and product)"（真正的合计数）。子类目行在页面上排在合计
    # 行前面，几何重建是按页面从上到下的顺序返回候选的，而LOCATE+VERIFY是"试中就
    # 停手"的设计，子类目行先被拿去验证——它本身就是真实合法的数据，四层机械校验
    # 一道都挑不出毛病，直接通过、循环立刻停手，disambiguation里"必须是合计数，
    # 不能是子类目"这句话根本没机会起作用。这里用简化过的真实场景复现一遍，确认
    # 排序修好之后，合计行会被排到前面优先尝试。
    total_header_row = [gw(str(y), 442.9 + 60.1 * i, 190.2)
                         for i, y in enumerate([2023, 2022, 2021, 2020, 2019])]
    subtotal_row = (
        [gw("Product", 189.4, 418.2), gw("life", 224.0, 418.2), gw("Gross", 250.0, 418.2),
         gw("emissions", 288.0, 418.2), gw("(Scope", 340.0, 418.2), gw("3)", 380.0, 418.2)]
        + [gw(v, 442.9 + 60.1 * i, 418.2)
           for i, v in enumerate(["15,570,000", "19,500,000", "22,000,000", "21,000,000", "23,500,000"])]
    )
    grand_total_row = (
        [gw("Total", 189.4, 506.8), gw("gross", 207.2, 506.8), gw("scope", 227.3, 506.8),
         gw("3", 249.0, 506.8), gw("emissions", 255.1, 506.8), gw("(corporate", 320.0, 506.8),
         gw("and", 370.0, 506.8), gw("product)", 390.0, 506.8)]
        + [gw(v, 442.9 + 60.1 * i, 506.8)
           for i, v in enumerate(["15,980,000", "20,545,800", "23,128,400", "22,550,000", "24,980,000"])]
    )
    apple_page_words = total_header_row + subtotal_row + grand_total_row
    pdf_geometry_reconstruct.extract_page_words = lambda pdf_path, page_no: apple_page_words

    # 12.1 先确认原始返回顺序确实是"子类目在前、合计在后"（页面从上到下的自然
    # 顺序）——这一步是复现bug存在的前提条件，还不是修复本身。
    raw_rows = reconstruct_table_by_geometry("fake.pdf", 77, ("scope 3", "scope3"))
    raw_labels = [r.label for r in raw_rows]
    assert raw_labels and "Product life Gross emissions (Scope 3)" == raw_labels[0], (
        f"复现前提不成立：页面几何顺序应该是子类目行排在合计行前面，实际顺序: {raw_labels}"
    )
    assert "Total gross scope 3 emissions (corporate and product)" in raw_labels, \
        f"合计行应该也被正确识别到，实际: {raw_labels}"

    # 12.2 经过_reconstruct_candidates排序之后，合计候选必须排到第一个——这才是
    # 真正验证修复生效了：LOCATE+VERIFY会优先试这一个，"试中就停"也就不会再
    # 错锁进子类目了。
    self_mod.resolve_pdf_path = lambda company: ("fake.pdf" if company == "AppleTest" else None)
    apple_hit_chunk = {
        "chunk_id": "AppleTest-p077-c00", "page": 77,
        "content": "Scope 3 emissions breakdown table follows, in metric tons CO2e.",
    }
    confirmed, _unconfirmed = _reconstruct_candidates(
        "AppleTest", INDICATOR_BY_ID["scope3_emissions"], [(apple_hit_chunk, 0.9)], [apple_hit_chunk],
    )
    self_mod.resolve_pdf_path = resolve_pdf_path   # 恢复
    pdf_geometry_reconstruct.extract_page_words = lambda pdf_path, page_no: geom_words   # 恢复成 10.1 的数据
    assert confirmed, "应该至少重建出候选，实际一个都没有"
    assert "Total gross scope 3 emissions" in confirmed[0]["content"], (
        f"排序修复后，标签带'total'的合计候选应该排在第一位优先被验证，"
        f"实际排第一的候选: {confirmed[0]['content']}"
    )
    print(f"  通过：子类目行'Product life...'和合计行'Total gross scope 3...'在页面上前者在前，"
          f"但排序修复后合计候选被正确排到第一位，LOCATE+VERIFY 不会再抢先锁进子类目。")

    print("=== [自检 13] 千分位逗号的回归测试（真实数据跑出来的坑：Meta Scope 1被误判成"
          "'关键词离数值太远'）===")
    # 真实事故复现：Meta Scope 1（2022年）这条候选的quote是"Scope 1 in 2022: 66,934
    # (in metric tons CO2e)"——带着千分位逗号，是从合成文本里原样抄出来的真实证据，
    # 更根本的_quote_is_genuine那道校验完全没问题。但AI自己填的value字段是"66934"
    # （没有逗号，JSON数字字段这么写完全合理）。旧代码直接拿没逗号的"66934"去quote
    # 原文里找，"66,934"中间夹着个逗号，永远找不到，于是这个关键词对、数字对、证据
    # 也是真的候选，被误判成"关键词和数值离得太远"给拒了——根本不是真的离得远，是
    # 这道检查自己的字符串匹配没考虑到千分位逗号。这里用真实复现的quote/value组合
    # 验证修好之后能正确通过。
    assert _value_near_required_keyword(
        "Scope 1 in 2022: 66,934 (in metric tons CO2e)", "66934", ("scope 1", "scope1"),
    ) is True, "quote 带千分位逗号、value 字段不带逗号时，应该能正确匹配上，不该被当成'离得太远'"
    assert _value_near_required_keyword(
        "Scope 1 in 2022: 66,934 (in metric tons CO2e)", "12345", ("scope 1", "scope1"),
    ) is False, "value 根本不在 quote 里时，去逗号之后也不该凭空匹配上"
    print(f"  通过：quote 带千分位逗号、value 字段不带逗号（模型正常的 JSON 数字写法）时，"
          f"能正确识别出关键词和数值其实挨得很近，不再被误杀成'离得太远'。")

    print("=== [自检 14] 表头年份粘住脚注角标的回归测试（真实数据跑出来的坑：Alphabet 2019"
          "这一整列在识别表头这一步就丢了）===")
    # 真实事故复现：Alphabet报告第76页表头是"Emissions inventory Unit 20191 2020
    # 2021 2022 2023"——"2019"后面紧跟着脚注角标编号"1"，中间没有空格，pdfplumber
    # 把它们粘成了一个词"20191"。旧代码要求表头这个词必须刚好是4位数字的年份，
    # "20191"对不上，2019这一整列在识别表头这一步就丢了——不是数值对不上，是这一
    # 列压根就没被认出来。后果很隐蔽：2019那一列真实存在的数值（81,900）因为找不到
    # 对应的表头列，被误判成了标签的一部分，变成"Scope 12 tCOe 81,900"，年份表头
    # 只剩4列（2020-2023），2019年的真实数据凭空消失，还顺带污染了标签文本。这里
    # 用真实还原的场景确认修好之后，5年数据（含2019）都能正确对齐，标签也不再把
    # 数值吃进去。
    footnote_header_row = [
        gw("Emissions", 63.0, 201.9), gw("inventory", 107.0, 201.9), gw("Unit", 358.0, 201.9),
        gw("20191", 468.7, 201.6), gw("2020", 555.8, 201.9), gw("2021", 642.9, 201.9),
        gw("2022", 730.0, 201.9), gw("2023", 821.6, 201.9),
    ]
    footnote_data_row = [
        gw("Scope", 63.0, 228.9), gw("12", 91.3, 228.6), gw("tCOe", 358.0, 228.9),
        gw("81,900", 468.7, 228.9), gw("55,800", 555.8, 228.9), gw("64,100", 642.9, 228.9),
        gw("91,200", 730.0, 228.9), gw("79,400", 821.6, 228.9),
    ]
    pdf_geometry_reconstruct.extract_page_words = (
        lambda pdf_path, page_no: footnote_header_row + footnote_data_row
    )
    footnote_rows = reconstruct_table_by_geometry("fake.pdf", 76, ("scope 1", "scope1"))
    assert footnote_rows, "表头年份带脚注角标粘连时，应该依然能识别出表头并恢复数据行"
    footnote_row = footnote_rows[0]
    assert footnote_row.year_values == {
        2019: "81,900", 2020: "55,800", 2021: "64,100", 2022: "91,200", 2023: "79,400",
    }, (
        f"5年数据（含被脚注角标粘住的2019年）都应该被精确对齐，实际: {footnote_row.year_values}"
    )
    print(f"  通过：表头里'2019'后面紧跟脚注角标编号、中间没有空格粘成'20191'时，"
          f"依然能正确解析出年份 2019，5年数据完整对齐，2019年真实数值不再被误吃进标签里。")

    print("=== [自检 15] 标签脚注角标清洗的回归测试（真实数据跑出来的坑：Alphabet Scope 1"
          "数值全部对齐好了，却因为标签写成'Scope 12'被AI自己判定不是答案）===")
    # 真实事故复现：自检14修好了年份列的识别，但重建出来的行标签还是"Scope 12
    # tCOe"——"Scope"后面紧跟的脚注角标编号"2"跟"1"粘在了一起，中间没空格。这个
    # 标签本身能通过table_row_keywords的子串匹配（"scope 12"里包含"scope 1"），
    # 所以在候选池里排第一，但送进EXTRACT_TMPL之后，AI看到从没见过的"Scope 12"
    # 这种写法，很合理地判断"这跟要求的Scope 1对不上"，回了"没找到"——数值、年份
    # 全部对齐好的候选，就因为标签没洗干净，白白在AI这一关被浪费掉了。这里确认
    # 修好之后标签能正确还原成"Scope 1"，而且完整走一遍extract_indicator全流程
    # 真的能被采信。
    footnote_row_cleaned = footnote_rows[0]
    assert footnote_row_cleaned.label == "Scope 1 tCOe", (
        f"'Scope 12 tCOe' 里粘住的脚注角标'2'应该被识别并去掉，还原成'Scope 1 tCOe'，"
        f"实际: {footnote_row_cleaned.label}"
    )

    def fake_resolve_pdf_path_alphabet(company: str):
        return "fake.pdf" if company == "AlphabetTest" else None

    self_mod.resolve_pdf_path = fake_resolve_pdf_path_alphabet
    alphabet_hit_chunk = {
        "chunk_id": "AlphabetTest-p076-c00", "page": 76,
        "content": "Emissions inventory Scope 1 direct emissions data table follows, in tCOe.",
    }
    alphabet_hit_chunk["vec"] = fake_embed(alphabet_hit_chunk["content"])

    def fake_gen_alphabet(system: str, user: str) -> str:
        passage_m = re.search(r"Passage \(from page \d+\):\n(.*?)\n\nQuestion:", user, flags=re.DOTALL)
        passage = passage_m.group(1) if passage_m else ""
        m = re.search(r"2019:\s*([\d,]+)", passage)
        if not m:
            return "NOT_IN_PASSAGE"
        return json.dumps({"value": m.group(1), "unit": "tCOe", "year": "2019", "quote": passage})

    self_mod.cloud_gen = fake_gen_alphabet
    alphabet_result = extract_indicator(
        "AlphabetTest", INDICATOR_BY_ID["scope1_emissions"], [alphabet_hit_chunk], cache,
    )
    self_mod.cloud_gen = fake_gen        # 恢复
    self_mod.resolve_pdf_path = resolve_pdf_path   # 恢复
    pdf_geometry_reconstruct.extract_page_words = lambda pdf_path, page_no: geom_words   # 恢复
    assert alphabet_result.disclosed is True, (
        f"标签洗干净之后，应该能正常走完整链路被采信，实际: disclosed={alphabet_result.disclosed}, "
        f"attempts={alphabet_result.attempts}"
    )
    assert alphabet_result.numeric_value == 81900.0, (
        f"应该精确取到 Scope 1 在 2019 年的坐标对齐值 81,900，实际: {alphabet_result.value}"
    )
    print(f"  通过：'Scope 12'被正确还原成'Scope 1'后，模型不再因为标签写法陌生而误判"
          f"NOT_IN_PASSAGE，完整链路精确取到 Scope 1 在 2019 年的值 {alphabet_result.value}。")

    print("=== [自检 16] 双栏正文重排的回归测试（真实数据跑出来的坑：Meta供应链那句"
          "'39家核心供应商'被双栏排版拆得七零八落）===")
    # 真实事故复现：Meta报告第31页，"我们在2021年开始与39家核心供应商合作核算排放"
    # 这句完整的话，因为双栏排版被提取工具按纵坐标交错拍扁了，跟旁边讲"冷冻水泵"
    # 制冷设备的无关内容混在了一起，AI看到这种乱序文本，直接回了"没找到"——供应链
    # 这个指标三家公司全部未披露，这是其中最系统性的一条。这里用简化还原的双栏
    # 场景（4行，左栏讲无关的设备维护，右栏讲供应商合作，"began working with"和
    # "a pilot group of 39 key suppliers"横跨两行，按原始拍扁的顺序中间会被左栏
    # 内容打断）验证两件事：一是重排函数本身能正确识别出双栏、拼出连贯的右栏正文；
    # 二是完整接入extract_indicator之后，就算原始检索到的候选是拍扁乱序的（单独
    # 喂给AI必然找不到连贯证据），额外加入的"重排版"候选依然能让AI给出可验证的
    # 真实答案。
    def gwx(text: str, x0: float, x1: float, top: float) -> dict:
        return {"text": text, "x0": x0, "x1": x1, "top": top}

    two_col_words = (
        [gwx("We", 30, 45, 100), gwx("monitor", 48, 95, 100), gwx("equipment", 98, 155, 100),
         gwx("routinely", 158, 210, 100),
         gwx("In", 250, 265, 100), gwx("2021,", 268, 305, 100), gwx("we", 308, 323, 100),
         gwx("began", 326, 365, 100), gwx("working", 368, 415, 100), gwx("with", 418, 445, 100)]
        + [gwx("for", 30, 48, 115), gwx("cooling", 51, 95, 115), gwx("systems", 98, 145, 115),
           gwx("onsite", 148, 185, 115),
           gwx("a", 250, 257, 115), gwx("pilot", 260, 292, 115), gwx("group", 295, 332, 115),
           gwx("of", 335, 347, 115), gwx("39", 350, 367, 115), gwx("key", 370, 392, 115),
           gwx("suppliers", 395, 450, 115)]
        + [gwx("and", 30, 50, 130), gwx("facility", 53, 100, 130), gwx("maintenance", 103, 175, 130),
           gwx("crews", 178, 210, 130),
           gwx("to", 250, 262, 130), gwx("calculate", 265, 320, 130), gwx("and", 323, 343, 130),
           gwx("report", 346, 385, 130), gwx("their", 388, 415, 130)]
        + [gwx("handle", 30, 70, 145), gwx("routine", 73, 115, 145), gwx("inspections", 118, 185, 145),
           gwx("GHG", 250, 275, 145), gwx("emissions", 278, 335, 145), gwx("data", 338, 365, 145),
           gwx("each", 368, 395, 145), gwx("year.", 398, 425, 145)]
    )
    pdf_geometry_reconstruct.extract_page_words = lambda pdf_path, page_no: two_col_words
    reordered = reconstruct_two_column_text("fake.pdf", 45)
    assert reordered, "应该能识别出双栏排版并拼出重排后的正文，不该返回 None"
    normalized_reordered = re.sub(r"\s+", " ", reordered)
    assert "began working with a pilot group of 39 key suppliers" in normalized_reordered, (
        f"重排后右栏应该能拼成一句连贯的话，横跨原来两行的'39家供应商'不该再被左栏内容"
        f"打断，实际重排结果: {reordered!r}"
    )

    # 模拟"原始拍扁顺序"（没有这层修复之前，AI实际会看到的样子）：每一行左栏加
    # 右栏按横坐标混在一起整行输出，这是真实数据里能看到的那种交错文本。
    naive_flattened = "\n".join([
        "We monitor equipment routinely In 2021, we began working with",
        "for cooling systems onsite a pilot group of 39 key suppliers",
        "and facility maintenance crews to calculate and report their",
        "handle routine inspections GHG emissions data each year.",
    ])
    _target_pattern = re.compile(
        r"began\s+working\s+with\s+a\s+pilot\s+group\s+of\s+39\s+key\s+suppliers", re.DOTALL,
    )
    assert _target_pattern.search(reordered), "重排后的正文应该能匹配到完整目标短语"
    assert not _target_pattern.search(naive_flattened), (
        "复现前提不成立：拍扁交错的原始顺序里，这句话中间应该被左栏内容打断，"
        "不该原地就能连续匹配到——不然这个回归测试就没有验证到真实 bug 场景"
    )

    def fake_resolve_pdf_path_twocol(company: str):
        return "fake.pdf" if company == "TwoColTest" else None

    self_mod.resolve_pdf_path = fake_resolve_pdf_path_twocol
    naive_chunk = {
        "chunk_id": "TwoColTest-p045-c00", "page": 45,
        "content": naive_flattened,
    }
    naive_chunk["vec"] = fake_embed(naive_chunk["content"])

    def fake_gen_twocol(system: str, user: str) -> str:
        passage_m = re.search(r"Passage \(from page \d+\):\n(.*?)\n\nQuestion:", user, flags=re.DOTALL)
        passage = passage_m.group(1) if passage_m else ""
        m = _target_pattern.search(passage)
        if not m:
            return "NOT_IN_PASSAGE"
        return json.dumps({"value": "39", "unit": "suppliers", "year": None, "quote": m.group(0)})

    self_mod.cloud_gen = fake_gen_twocol
    twocol_result = extract_indicator(
        "TwoColTest", INDICATOR_BY_ID["supply_chain_emissions"], [naive_chunk], cache,
    )
    self_mod.cloud_gen = fake_gen        # 恢复
    self_mod.resolve_pdf_path = resolve_pdf_path   # 恢复
    pdf_geometry_reconstruct.extract_page_words = lambda pdf_path, page_no: geom_words   # 恢复
    assert twocol_result.disclosed is True, (
        f"原始检索到的候选单独喂给模型必然找不到连贯证据（上面已经验证过），但额外加入的"
        f"双栏重排候选应该能让模型给出可验证的真实答案，实际: disclosed={twocol_result.disclosed}, "
        f"attempts={twocol_result.attempts}"
    )
    assert twocol_result.numeric_value == 39.0, (
        f"应该精确取到重排后正文里的 39 家供应商，实际: {twocol_result.value}"
    )
    print(f"  通过：拍扁交错的原始候选单独送进模型必然找不到连贯证据，但额外加入的双栏"
          f"重排候选让模型正确给出了 {twocol_result.value} 家供应商这个可验证的真实答案，"
          f"不是靠原始候选侥幸蒙对的。")

    print("=== [自检 17] 多年数据优先取最新一年的回归测试（真实数据跑出来的坑：同一张对比"
          "报告里，不同公司/不同指标选中的年份互相对不上）===")
    # 真实事故复现：给precision/recall框架核对真实答案的时候发现的——Alphabet报的
    # Scope 1是2020年的55,800，Meta报的Scope 1/Scope 3是2022年的值，而不是本该
    # 统一拿来对比的最新一年（2023）。根本原因：以前是把5年数据拼进同一个合成
    # 片段，一次性交给AI自己选年份，disambiguation里从来没告诉AI"没指定年份的
    # 时候该选最新一年"，AI怎么选完全没有章法——数字、单位、关键词全都是真的，
    # 四层机械校验一道都挡不住，因为这些校验查的是"这个候选本身对不对"，不管
    # "这是不是该选的那一个"。这类问题最危险：不是"未披露"，也不是"抽错了假
    # 数据"，是真实数据但年份选得不统一，摆在对比报告里几乎发现不了。
    #
    # 修法：rows_to_synthetic_chunk_geometry和rows_to_synthetic_chunk都改成按
    # 年份拆成独立候选、从新到旧排序返回，不再指望AI自己判断该选哪年。这里验证
    # 完整流程：5年数据全部是真实有效的（不是只有最新一年能通过校验），确认
    # LOCATE+VERIFY"试中就停"确实会优先锁定最新一年，不会随便选中较旧的哪一年。
    pdf_geometry_reconstruct.extract_page_words = lambda pdf_path, page_no: geom_words   # 这是5年的Scope1/2/3数据
    self_mod.resolve_pdf_path = lambda company: ("YearOrderTest.pdf" if company == "YearOrderTest" else None)
    year_hit_chunk = {
        "chunk_id": "YearOrderTest-p099-c00", "page": 99,
        "content": "Corporate emissions Scope 1 direct emissions data table follows.",
    }
    year_hit_chunk["vec"] = fake_embed(year_hit_chunk["content"])

    def fake_gen_any_year(system: str, user: str) -> str:
        # 故意不区分年份、来者不拒——5年里随便哪一年单独作为候选喂过来都痛快接受，
        # 这样才能干干净净只验证"候选池本身有没有把最新一年排在最前面"，不要跟
        # "AI对某些年份更挑剔"这种别的变量混在一起。
        passage_m = re.search(r"Passage \(from page \d+\):\n(.*?)\n\nQuestion:", user, flags=re.DOTALL)
        passage = passage_m.group(1) if passage_m else ""
        m = re.search(r"in (\d{4}):\s*([\d,]+)", passage)
        if not m:
            return "NOT_IN_PASSAGE"
        return json.dumps({
            "value": m.group(2), "unit": "metric tons CO2e", "year": m.group(1), "quote": passage,
        })

    self_mod.cloud_gen = fake_gen_any_year
    year_result = extract_indicator(
        "YearOrderTest", INDICATOR_BY_ID["scope1_emissions"], [year_hit_chunk], cache,
    )
    self_mod.cloud_gen = fake_gen        # 恢复
    self_mod.resolve_pdf_path = resolve_pdf_path   # 恢复
    assert year_result.disclosed is True, (
        f"5年数据模型来者不拒，应该能顺利采信，实际: disclosed={year_result.disclosed}"
    )
    assert year_result.year == "2023", (
        f"5年数据里模型对哪一年都不挑剔时，LOCATE+VERIFY 应该优先锁定最新一年 2023，"
        f"不该随便选中较旧的年份，实际选中: {year_result.year}（值 {year_result.value}）"
    )
    assert year_result.numeric_value == 48952.0, (
        f"2023年的真实值应该是48,952，实际: {year_result.value}"
    )
    print(f"  通过：5年数据模型对哪一年都不挑剔时，候选池本身的排序（按年份从新到旧拆分）"
          f"确保 LOCATE+VERIFY 优先锁定最新一年 2023（值 {year_result.value}），不再出现"
          f"不同公司/不同指标年份对不上、拿旧数据冒充最新对比数据的问题。")

    print("=== [自检 18] 标签自带单位、数值后面却没跟单位的回归测试（真实数据跑出来的坑："
          "Alphabet Scope 1五个年份的坐标重建全都精确对齐了，却因为这个格式细节全部被"
          "AI判定'没找到'，整行从报告里消失）===")
    # 真实事故复现：Alphabet这份PDF的表头本身就把单位写进了行标签里（"Scope 1
    # tCOe"），不是"Scope 1"加独立的"(in metric tons CO2e)"括注这种写法。
    # find_unit_context在原始正文里找不到独立括注，传进来的unit_context是None——
    # 旧代码不做任何补救，拼出来的合成文本变成"Scope 1 tCOe in 2023: 79,400"，
    # 单位紧跟在标签后面、数值前面，数值后面什么都没跟。EXTRACT_TMPL教AI的可信
    # 三元组顺序是"标签→数字→单位"，数值后面找不到单位，AI很合理地判定这不是一条
    # 干净的证据——5个年份全都这样，全部被拒、标成"未披露"，而Meta同一批数据因为
    # PDF原文本身就有独立的单位括注，完全没触发这个问题，一次核对就命中。这是
    # debug以来最隐蔽的一次：不是数值错、不是年份错、也不是标签写法生僻（不是
    # "Scope 12"那种脚注粘连），单纯是"单位出现的位置"不符合喂给AI看的示范顺序。
    unit_gap_row = GeometricRow(label="Scope 1 tCOe", year_values={2023: "79,400", 2020: "55,800"},
                                 page=76)
    unit_gap_synths = rows_to_synthetic_chunk_geometry(unit_gap_row, unit_context=None)
    synth_2023 = next(c for c in unit_gap_synths if c["year"] == 2023)
    assert "in 2023: 79,400 (tCOe)" in synth_2023["content"], (
        f"标签自带单位、find_unit_context 又找不到独立括注时，应该从标签里把单位摘出来"
        f"补一份到数值后面，凑齐'标签→数字→单位'的顺序，实际合成文本: {synth_2023['content']}"
    )
    from table_reconstruct import ReconstructedRow as _RR
    unit_gap_tr_row = _RR(label="Scope 2 tCOe", values=["3,400", "3,600"], n_cols=2,
                          source_chunk_id="TestCo6-p090-c00", page=90, confidence="single_label")
    unit_gap_tr_synths = rows_to_synthetic_chunk(unit_gap_tr_row, [2023, 2019], unit_context=None)
    tr_synth_2023 = next(c for c in unit_gap_tr_synths if c["year"] == 2023)
    assert "in 2023: 3,400 (tCOe)" in tr_synth_2023["content"], (
        f"table_reconstruct.py 这一路是同一个坑、同一个修法，实际合成文本: "
        f"{tr_synth_2023['content']}"
    )
    print(f"  通过：行标签本身自带单位（比如'Scope 1 tCOe'）、原始正文又找不到独立单位"
          f"括注时，坐标重建、文字模式重建两条路都会把标签里的单位摘出来、补一份到"
          f"数值后面，凑齐EXTRACT_TMPL教AI的'标签→数字→单位'顺序，不再让本来对齐正确"
          f"的数值因为这个格式细节被误判成证据不干净。")

    print("=== [自检 19] Question里年份提得不够多、AI保守拒答的回归测试（真实数据跑出来的坑，"
          "用诊断脚本debug_alphabet_scope1.py一共做了7组真实API对照实验才锁定：光在英文部分"
          "提一次年份不够，AI还是全判'没找到'；中英文都点名同一年，AI才痛快给出正确答案）===")
    # 真实事故复现，完整时间线写在这里（不是一次就蒙对的，中间两次以为"修好了"其实
    # 都不是真正的根因）：
    # 第一轮：自检18修完标签/单位格式的问题之后，Alphabet Scope 1那5个年份的候选，
    # 用真实数据跑还是全军覆没。写了个一次性的诊断脚本做受控对比：完全照着真实流程
    # 里那次调用复现一遍→还是"没找到"；把同一段文字换成自由问答（不要求严格JSON
    # 格式）→AI自己说"没有任何歧义，就是这个数"；把"Question里有没有显式写上
    # for 2023"这一个变量单独隔离出来测→AI立刻给出正确答案。当时以为病根就是
    # "Question没提年份"，把year_hint这个机制写进代码，19个自检全部跑过，就上线了。
    # 第二轮：真实用户第三次重新跑，Alphabet Scope1依然全部"没找到"。直接去读真实
    # 调用记下来的Question原文（专门加了调试字段来记），确认year_hint确实一字
    # 不差地传给AI了——不是"没传进去"。把成功案例和真实失败案例逐字对比，唯一的
    # 区别是"for 2023"在句子里的位置，怀疑是词序的问题，单独做了两个实验测——结果
    # 真实跑出来两个都还是"没找到"，词序这个猜测被推翻了。
    # 第三轮：把成功案例和失败案例又逐字重新对比一遍，才发现真正剩下的区别是——
    # 成功案例把年份说了两遍（英文里说了一次"for 2023"，中文括注里又额外说了一次
    # "，2023年"），失败案例只在英文里提了一次。单独去掉中文括注里那次重复年份、
    # 别的都不改——真实跑出来马上又变回"没找到"；再按现在真实流程的模板结构（英文
    # 部分带年份、句尾单独加year_hint后缀）加上中文标签也带年份→真实跑出来给出了
    # 正确的JSON答案。cloud_gen用的是temperature=0（也就是没有随机性的确定性
    # 输出），这不是运气问题，是可以稳定复现的行为。
    #
    # 这里用规则去模拟被7组真实API对照实验证实过的AI行为：年份只在英文（year_hint）
    # 里出现一次不够，必须中文标签里也重复同一个年份，两处都对得上才会痛快给出
    # 真实值——不是瞎编的假设，是三组各自只改一个变量的独立对照实验锁定出来的
    # 行为模式。
    def fake_gen_strict_year(system: str, user: str) -> str:
        passage_m = re.search(r"Passage \(from page \d+\):\n(.*?)\n\nQuestion:", user, flags=re.DOTALL)
        passage = passage_m.group(1) if passage_m else ""
        q_year_m = re.search(r"Question: .*?\bfor (\d{4})\b", user)
        if not q_year_m:
            return "NOT_IN_PASSAGE"
        year_asked = q_year_m.group(1)
        # 光有英文"for <year>"还不够——真实API证实过，中文标签里必须也重复同一个
        # 年份（"，<year>年"），AI才会真正采信，只提一次它会保守地拒答。
        if f"，{year_asked}年" not in user:
            return "NOT_IN_PASSAGE"
        v_m = re.search(rf"in {year_asked}:\s*([\d,]+)", passage)
        if not v_m:
            return "NOT_IN_PASSAGE"
        return json.dumps({"value": v_m.group(1), "unit": "tCOe", "year": year_asked, "quote": passage})

    strict_row = GeometricRow(label="Scope 1 tCOe", year_values={2023: "79,400", 2020: "55,800"}, page=76)
    self_mod.reconstruct_table_by_geometry = lambda pdf_path, page_no, keywords: [strict_row]
    self_mod.resolve_pdf_path = lambda company: (
        "StrictYearTest.pdf" if company == "StrictYearTest" else None
    )
    strict_hit_chunk = {
        "chunk_id": "StrictYearTest-p076-c00", "page": 76,
        "content": "Corporate emissions Scope 1 direct emissions data table follows.",
    }
    strict_hit_chunk["vec"] = fake_embed(strict_hit_chunk["content"])
    self_mod.cloud_gen = fake_gen_strict_year
    strict_result = extract_indicator(
        "StrictYearTest", INDICATOR_BY_ID["scope1_emissions"], [strict_hit_chunk], cache,
    )
    self_mod.cloud_gen = fake_gen                              # 恢复
    self_mod.resolve_pdf_path = resolve_pdf_path                # 恢复
    self_mod.reconstruct_table_by_geometry = reconstruct_table_by_geometry   # 恢复
    assert strict_result.disclosed is True, (
        f"候选年份已确认时 Question 应该带上年份，模型（哪怕对没点名年份的候选很保守）"
        f"也应该痛快接受，实际: disclosed={strict_result.disclosed}, "
        f"attempts={strict_result.attempts}"
    )
    assert strict_result.year == "2023" and strict_result.numeric_value == 79400.0, (
        f"应该精确取到 2023 年的 79,400，实际: year={strict_result.year}, "
        f"value={strict_result.value}"
    )
    print(f"  通过：候选年份已经确认（坐标重建/表格重建）的时候，Question里中英文都点名"
          f"那一年（英文用year_hint，中文标签也重复一遍），AI不再因为'年份只提一次不够"
          f"放心'而保守拒答，精确取到2023年的{strict_result.value}——这个修复是拿真实"
          f"API做了一共7组、每组两两只差一个变量的对照实验才锁定病根的，中间两次'以为"
          f"修好了'其实都不是真正的根因，不是拍脑袋猜的。")

    print("=== [自检 20] 单位括注词表漏了一个词、导致单位缺失、进而让AI碰运气选中错误年份的"
          "回归测试（真值比对跑出来的真实坑：Meta总取水量五个年份候选，find_unit_context"
          "找不到'(in megaliters)'这个词，所有候选都没带单位，AI只在第3个候选偶尔猜对一个"
          "能过校验的单位，'试中就停'因此意外采信了2021年而不是2023年的数字——表面看像是"
          "选错了年份，根子其实是单位词表本来就不完整）===")
    # 真实事故：拿真实答案去核对（真实答案文件 vs 真实流程跑出来的结果）时发现，
    # Meta Platforms总取水量取到的是2021年的5,043，不是2023年的5,274。查每一步
    # 的记录发现2023/2022这两个候选都因为unit是空的被第②道机械校验拒绝了，只有
    # 2021这个候选AI自己猜出了一个凑巧能过校验的单位'm³'。查原文，Meta报告水资源
    # 表格表头写的是"Water withdrawal by facility (in megaliters)"——"megaliters"
    # 这个词当时根本不在find_unit_context认识的词表里，返回了None，五个候选因此
    # 全都没带单位，AI只能自己瞎猜，猜中猜不中纯粹看运气。这里验证词表已经补上：
    # 同一句真实的括注现在能被找到，取到的单位是稳定的，不再看运气。
    unit_match = find_unit_context("Water withdrawal by facility (in megaliters)")
    assert unit_match is not None and "megaliters" in unit_match.lower(), (
        f"'(in megaliters)' 这种真实见过的水资源单位括注应该能被 find_unit_context 找到，"
        f"实际: {unit_match!r}"
    )
    unit_match2 = find_unit_context("Corporate facilities Total (in kiloliters) 1,610")
    assert unit_match2 is not None and "kiloliters" in unit_match2.lower(), (
        f"kiloliters 同理应该被找到，实际: {unit_match2!r}"
    )
    print(f"  通过：'(in megaliters)'/'(in kiloliters)'这类真实水资源报告里的单位括注，"
          f"现在能被find_unit_context正确识别出来（之前只认gallons/liters/cubic "
          f"meters等几种写法，漏了megaliters/kiloliters/m³），候选不会再因为单位缺失"
          f"而只能靠AI碰运气瞎猜。")

    print("=== [自检 21] 可再生能源占比这个指标行标签措辞跟预设关键词不匹配、候选在生成阶段"
          "就被拦在门外的回归测试（真值比对跑出来的真实坑：Alphabet/Meta的可再生能源占比"
          "在真实报告里都是100%，但整个流程全都判'未披露'——不是AI判错，是候选生成阶段的"
          "关键词表太窄，这两家公司这一行数据从一开始就没能进入候选池，AI压根没有被问到"
          "这个问题）===")
    # 真实数据：Alphabet原文这一行标签是"Electricity purchased from renewable
    # sources"，Meta原文这一行标签干脆就孤零零一个"Renewable"（单位%是表头单独
    # 一列，不在行标签里）。原来table_row_keywords只有"renewable electricity
    # percentage"/"renewable energy percentage"/"% renewable"/"renewable %"这
    # 四种写法，两家公司的真实标签一个都不沾边。这里验证补充关键词之后，两种真实
    # 标签都能匹配上——敢加这么宽泛的裸词"renewable"，是因为valid_units=
    # ('%','percent','percentage')这道机械校验本来就会在单位对不上的时候正常
    # 拒绝，就算误配到别的行（比如用GW做单位的"可再生能源装机容量"），也不会降低
    # 准确率，只会多给真正对的那一行一次被试到的机会。
    renewable_keywords = INDICATOR_BY_ID["renewable_energy_pct"].table_row_keywords
    for real_label in ("Electricity purchased from renewable sources", "Renewable"):
        low = real_label.lower()
        assert any(kw in low for kw in renewable_keywords), (
            f"真实见过的标签 {real_label!r} 应该能匹配上 renewable_energy_pct 的 "
            f"table_row_keywords，实际关键词表: {renewable_keywords}"
        )
    print(f"  通过：Alphabet 真实标签'Electricity purchased from renewable sources'和 Meta "
          f"真实标签孤零零一个'Renewable'现在都能进入候选池——之前这两种真实见过的写法"
          f"一个都匹配不上预设的四个关键词，候选在生成阶段就被拦住，模型根本没有机会看到"
          f"这两家公司的这一行数据。")

    print("\n全部自检通过 ✅ —— 逻辑正确性已验证，可以放心接真实 API key 和真实数据跑。")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="ESG 尽调分析工作流：指标抽取 + 对比 + 可信度面板 + 风险预警")
    p.add_argument("--companies", nargs="+", default=DEFAULT_COMPANIES)
    p.add_argument("--indicators", nargs="+", choices=list(INDICATOR_BY_ID),
                   help="只跑指定指标（调试用，省 API 调用），默认跑全部 7 项")
    p.add_argument("--ratings-dir", default=str(RATINGS_DIR))
    p.add_argument("--out-dir", default=str(OUT_DIR))
    p.add_argument("--skip-ratings", action="store_true", help="不加载评级数据，跳过漂绿/评级分歧分析")
    p.add_argument("--skip-commitments", action="store_true", help="不抽取承诺话术（更快，但漂绿检测会跳过）")
    p.add_argument("--selftest", action="store_true",
                   help="离线自检：不需要网络/API key，用假数据验证代码逻辑本身是对的")
    p.add_argument("--debug", action="store_true",
                   help="指标标记「未披露」时，把每个候选片段的页码/chunk_id/片段开头/模型原始回复"
                        "打到终端——诊断抽取失败到底是检索没找到对的片段，还是模型判定不匹配")
    p.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                   help=f"并发线程数（默认 {DEFAULT_WORKERS}）。'公司 x 指标'这个维度并发跑，"
                        f"每个指标内部仍是顺序尝试候选片段，不受这个参数影响；调小可以缓解"
                        f"API 限流，调大能跑更快但更容易触发限流重试。")
    args = p.parse_args()

    if args.selftest:
        return run_selftest()

    if not agent_cloud.KEY:
        print("请先设置：export ZHIPU_API_KEY=\"你的key\"（--selftest 模式不需要）", file=sys.stderr)
        return 1

    run(args.companies, Path(args.ratings_dir), Path(args.out_dir),
        args.indicators, args.skip_ratings, args.skip_commitments, args.debug, args.workers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

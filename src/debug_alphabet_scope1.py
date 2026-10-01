#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
这是个一次性的排查脚本，不属于正式流程的一部分，用完就可以删掉——

事情是这样的：pdf_geometry_reconstruct.py 那边已经把 Alphabet 2023 年
Scope 1 排放的真实数字核对清楚了，是 79,400 tCOe（直接对着PDF原文第76页的表格
核对过的："Scope 1² tCOe 81,900 55,800 64,100 91,200 79,400"，这是2019到2023
五年的数字，79,400 就是2023这一年）。标签格式、单位放哪儿这些也照着真实情况
修好了（见 pdf_geometry_reconstruct.py 的自检18）。可是真拿去跑API，这一行
5个年份的候选答案全都被模型判成 NOT_IN_PASSAGE——数据明明是对的，模型咋就不认呢？
光看"NOT_IN_PASSAGE"这几个字啥也看不出来，不知道模型是真没找到还是在犹豫什么。
这个脚本要做的就一件事：让模型这个"黑箱"变得能看懂它到底在想啥。

怎么跑：
    export ZHIPU_API_KEY="你的key"
    python3 src/debug_alphabet_scope1.py

跑完会打出好几组实验结果：
  实验1：原样复现一遍真实流程里那次被拒的调用，先确认这个 NOT_IN_PASSAGE 是不是
        能稳定重现的（要是这次反倒答对了，那就说明模型本身状态不稳定，跟
        prompt写法或格式没关系）。
  实验2：还是同一段文字，但不要求它按严格的JSON格式回答，改成让它用大白话说说
        "这段话里到底有没有一个靠谱的答案，为什么"。这样能看到模型自己说出来的
        犹豫点在哪儿，不用我们瞎猜。
  实验3：格式还是跟实验1一样严格的JSON，但问题里明确写上了2023年——做法是把年份
        直接拼进 label_en 里（变成"Scope 1 GHG emissions for 2023"），而不是走
        模板里单独的 {year_hint} 占位符。这次真跑出来是成功的，模型给出了正确答案。

  ——年份写进问题里这招在实验3里证明有效了，后来也真的把这个修法（自检19，
  在 analyst.py 里叫 year_hint 机制）部署上线了。可用户第三次重新跑，Alphabet
  Scope1 那五个年份候选还是全军覆没，全部 NOT_IN_PASSAGE。直接翻用户机器上
  analysis/outputs/esg_comparison.json 里新加的两个调试字段
  （_debug_year_hint / _debug_question_line）确认：year_hint 这个值本身算对了，
  也确实拼进了发给模型的问题里，"for 2023"这几个字一字不差都在——所以不是
  "年份没传进去"这个问题。

  那就把真实跑的Question原文和实验3成功那次的Question原文放一起逐字比对，
  发现两边措辞顺序不一样：
    实验3（成功）："What is Scope 1 GHG emissions for 2023 (Scope 1 排放...)?"
                    —— "for 2023" 紧挨着英文指标名，中间啥都没插。
    真实流程（失败）："What is Scope 1 GHG emissions (Scope 1 排放...) for 2023?"
                    —— "for 2023" 被挤到句尾去了，中间隔了一整个中文括号说明。

  这是目前唯一还没排除掉的变量：这段话、匹配规则、严格JSON格式全都一字不差，
  "for 2023"也确实都写了——就是它在句子里离"Scope 1 GHG emissions"这个词组的
  距离不一样。

  实验4：完全照着真实流程现在的拼法复现一遍（"for 2023"放在括号说明后面、
        整句最后），看看能不能自己也复现出真实环境里那个 NOT_IN_PASSAGE
        （排除掉"是不是真实环境里还有别的我们没发现的变量"这种可能）。
  实验5：只改一个地方——把"for 2023"挪到紧跟"Scope 1 GHG emissions"后面、
        括号说明前面（跟实验3成功那次的顺序对齐），别的字一个不动。如果这样
        模型就转而给出正确答案了，那就说明真正的病根是"年份和指标名隔得太远"，
        跟"有没有写年份"本身没关系。

  ——结果实验4、5真跑出来还是都失败，仍然是 NOT_IN_PASSAGE。"年份离指标名太远"
  这个猜测被推翻了：挪了位置(实验5)也没用，说明词序根本不是病根。

  接着又逐字比对了实验3（成功）和实验5（失败，词序已经跟实验3对齐了）剩下的
  唯一区别：
    实验3： label = "Scope 1 排放（直接排放），2023年"   —— 中文括号里也带着"2023年"
    实验5： label = "Scope 1 排放（直接排放）"           —— 中文括号里没写年份

  也就是说实验3其实是把年份说了两遍（英文的"for 2023" + 中文的"，2023年"），
  而实验4、5（包括真实流程现在用的 year_hint 机制）都只在英文部分提了一次。
  这是目前唯一剩下、还没被排除的变量了。

  实验6：拿实验3那套已经验证成功、用 .format() 调出来的调用方式（不手抄字符串，
        免得抄错字），只去掉中文括号里的"，2023年"，别的一个字不改，看看是不是
        "年份只提一次不够、非得中英文都提一遍模型才肯认"。

  ——实验6真跑出来又是 NOT_IN_PASSAGE，失败了。这次证实了：光是去掉中文括号里
  那个重复写的"2023年"，别的什么都没动，模型就从"给出正确答案"变回了"拒答"。
  三次独立对照下来（实验3、5、6两两之间都只差一个变量）指向同一个结论：这个
  模型在严格JSON模式下，年份只出现一次它不放心，非得中英文都点名同一个年份
  才会真的采信。cloud_gen 调用用的是 temperature=0（见 agent_cloud.py），
  不是随机采样碰运气——这个结果是能稳定重现的，不是巧合。

  不过目前验证过有效的这个"说两遍年份"的写法（实验3）是把年份直接拼进
  label_en，再整个塞进一个跟真实流程结构不一样的问题句子里——还没验证过
  "如果照着真实流程现在的结构（label_en 不带年份、句尾单独跟一个 {year_hint}
  后缀），只是把中文 label 也顺手带上年份"，这个具体的改法本身到底管不管用。

  实验7：用真实流程现在的模板结构（{label_en} 不含年份、句尾单独挂一个
        {year_hint} 后缀），只改一处——中文 label 也带上年份（"，2023年"）。
        这就是马上要部署到 analyst.py 里的具体改法，实验7就是直接验证这个
        改法本身管不管用。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agent_cloud import cloud_gen  # noqa: E402

PASSAGE = (
    "Scope 1 tCOe in 2023: 79,400 (tCOe) (reconstructed directly from the original "
    "PDF's page layout coordinates on page 76; this value was matched to year 2023 "
    "by its exact column position on the page, not guessed from flattened text)."
)

# 这段跟 analyst.py 里的 EXTRACT_TMPL 一字不差，是故意复制过来的，没有直接 import——
# 万一以后 analyst.py 改了模板，这个诊断脚本要是跟着悄悄变了，那就不是"完全复现"了，
# 排查出来的结果也就不准了。
EXTRACT_TMPL = (
    "You are extracting one specific ESG metric from a passage of a sustainability report.\n\n"
    "Passage (from page {p}):\n{c}\n\n"
    "Question: What is {label_en} ({label})?\n"
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
    "its number — a table row or fragment is fine, it does NOT need to be a full sentence, <=200 chars>\"}}\n"
    "5. If this passage does NOT contain this exact metric with trustworthy evidence, respond with "
    "exactly the token NOT_IN_PASSAGE and nothing else. Do not guess, do not substitute a related number, "
    "do not pick a number just because it appears near the right label."
)

# 这段是跟 analyst.py 里*现在线上正在用*的 EXTRACT_TMPL 一字不差抄过来的（带着独立的
# {year_hint} 句尾后缀，这是自检19之后的新版本，跟上面那份"实验1/2/3用的旧模板"不是
# 同一份）。单独开一个常量放这儿，只给实验7用，这样就不会动到上面那份已经跑通、
# 不该再改的模板。
REAL_EXTRACT_TMPL = (
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
    "its number — a table row or fragment is fine, it does NOT need to be a full sentence, <=200 chars>\"}}\n"
    "5. If this passage does NOT contain this exact metric with trustworthy evidence, respond with "
    "exactly the token NOT_IN_PASSAGE and nothing else. Do not guess, do not substitute a related number, "
    "do not pick a number just because it appears near the right label."
)


def main() -> int:
    if not os.environ.get("ZHIPU_API_KEY"):
        print('请先设置：export ZHIPU_API_KEY="你的key"', file=sys.stderr)
        return 1

    prompt1 = EXTRACT_TMPL.format(
        p=76, c=PASSAGE,
        label_en="Scope 1 GHG emissions", label="Scope 1 排放（直接排放）",
        disambiguation="必须是标注为 'Scope 1' 的数字，不是 'Total emissions'（总排放），"
                        "不是 'Scope 2'，也不是往年基线值。",
    )
    print("=== 实验1：一字不差复现真实 pipeline 里被拒的那次调用 ===")
    resp1 = cloud_gen("You are a careful ESG data analyst who never guesses.", prompt1)
    print("模型原始返回：", repr(resp1))
    print()

    prompt2 = (
        f"Passage (from page 76):\n{PASSAGE}\n\n"
        "Question: What is Alphabet's Scope 1 GHG emissions for 2023?\n\n"
        "Does this passage contain a trustworthy, unambiguous answer to that question? "
        "First explain your reasoning in 2-3 plain sentences — specifically, is there anything about "
        "this passage that makes you uncertain or hesitant, even slightly? "
        "Then on a final separate line write exactly: ANSWER: YES or ANSWER: NO."
    )
    print("=== 实验2：同一段文字，不用严格 JSON 格式，让模型用人话解释理由 ===")
    resp2 = cloud_gen("You are a careful ESG data analyst.", prompt2)
    print(resp2)
    print()

    # 实验1和实验2其实差了两个地方：(a) 一个是严格JSON/NOT_IN_PASSAGE格式，一个是
    # 自由问答；(b) 问题里有没有明写"for 2023"。真实流程里的问题从来不带年份
    # （因为 IndicatorSpec 压根就没存"该问哪一年"这个信息）。这里想把变量(b)单独
    # 挑出来看：格式和匹配规则都保持跟实验1一模一样，只在问题里加上"for 2023"，
    # 看看是不是"没写年份"才是真正的病根。
    prompt3 = EXTRACT_TMPL.format(
        p=76, c=PASSAGE,
        label_en="Scope 1 GHG emissions for 2023", label="Scope 1 排放（直接排放），2023年",
        disambiguation="必须是标注为 'Scope 1' 的数字，不是 'Total emissions'（总排放），"
                        "不是 'Scope 2'，也不是往年基线值。",
    )
    print("=== 实验3：跟实验1一样严格的 JSON 格式，只是 Question 里显式写明 2023 年 ===")
    resp3 = cloud_gen("You are a careful ESG data analyst who never guesses.", prompt3)
    print("模型原始返回：", repr(resp3))
    print()

    disamb = ("必须是标注为 'Scope 1' 的数字，不是 'Total emissions'（总排放），"
              "不是 'Scope 2'，也不是往年基线值。")

    # 真实流程现在的拼法是这样的："Question: What is {label_en} ({label}){year_hint}?"
    # —— year_hint（也就是" for 2023"）加在括号说明*后面*、整句最后头。跟实验1/3
    # 唯一不一样的地方就是这个 year_hint 摆放的位置，别的（这段话、匹配规则、
    # label_en、label本身不含年份）都跟实验1一样。
    prompt4 = (
        "You are extracting one specific ESG metric from a passage of a sustainability report.\n\n"
        f"Passage (from page 76):\n{PASSAGE}\n\n"
        "Question: What is Scope 1 GHG emissions (Scope 1 排放（直接排放）) for 2023?\n"
        f"Matching rule: {disamb}\n\n"
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
        '   {"value": "<number as written, keep commas/decimals/%>", "unit": "<unit>", '
        '"year": "<year if stated, else null>", "quote": "<verbatim excerpt that shows the label next to '
        'its number — a table row or fragment is fine, it does NOT need to be a full sentence, <=200 chars>"}\n'
        "5. If this passage does NOT contain this exact metric with trustworthy evidence, respond with "
        "exactly the token NOT_IN_PASSAGE and nothing else. Do not guess, do not substitute a related number, "
        "do not pick a number just because it appears near the right label."
    )
    print("=== 实验4：完全复现真实 pipeline 现在的拼接方式（\"for 2023\" 在括注后面、句尾）===")
    resp4 = cloud_gen("You are a careful ESG data analyst who never guesses.", prompt4)
    print("模型原始返回：", repr(resp4))
    print()

    # 只改一个变量：把"for 2023"从句尾挪到紧跟"Scope 1 GHG emissions"后面、
    # 中文括号说明前面——跟实验3成功那次的位置对齐。别的字符一个不动。
    prompt5 = prompt4.replace(
        "Question: What is Scope 1 GHG emissions (Scope 1 排放（直接排放）) for 2023?",
        "Question: What is Scope 1 GHG emissions for 2023 (Scope 1 排放（直接排放）)?",
    )
    assert prompt5 != prompt4, "替换没生效，检查字符串是否一致"
    print("=== 实验5：只把 \"for 2023\" 挪到指标名后面、括注前面（其它一字不改）===")
    resp5 = cloud_gen("You are a careful ESG data analyst who never guesses.", prompt5)
    print("模型原始返回：", repr(resp5))
    print()

    # 用跟实验3一模一样、已经验证过靠谱的 .format() 调用方式（不是手打字符串，
    # 免得打错），唯一的改动是去掉中文括号说明里的"，2023年"，年份就只在英文
    # label_en 里提一次。如果这样又变回 NOT_IN_PASSAGE 了，那说明真正的病根是
    # "年份只提一次不够，中英文都得提"。
    prompt6 = EXTRACT_TMPL.format(
        p=76, c=PASSAGE,
        label_en="Scope 1 GHG emissions for 2023", label="Scope 1 排放（直接排放）",
        disambiguation="必须是标注为 'Scope 1' 的数字，不是 'Total emissions'（总排放），"
                        "不是 'Scope 2'，也不是往年基线值。",
    )
    print("=== 实验6：跟实验3一样，只是去掉中文括注里重复的'，2023年'（只在英文提一次年份）===")
    resp6 = cloud_gen("You are a careful ESG data analyst who never guesses.", prompt6)
    print("模型原始返回：", repr(resp6))
    print()

    # 用真实流程现在线上的模板结构（REAL_EXTRACT_TMPL，label_en 不带年份、句尾
    # 单独跟一个 year_hint 后缀），唯一改的地方是让 label（中文括号说明）也带上
    # 年份。这就是马上要真正部署到 analyst.py 里的改法，这里直接验证它本身
    # 管不管用，而不是验证一个结构不一样的替代方案。
    prompt7 = REAL_EXTRACT_TMPL.format(
        p=76, c=PASSAGE,
        label_en="Scope 1 GHG emissions", label="Scope 1 排放（直接排放），2023年",
        year_hint=" for 2023",
        disambiguation="必须是标注为 'Scope 1' 的数字，不是 'Total emissions'（总排放），"
                        "不是 'Scope 2'，也不是往年基线值。",
    )
    print("=== 实验7：真实 pipeline 的模板结构 + 中文 label 也带上年份（即将部署的修法本身）===")
    resp7 = cloud_gen("You are a careful ESG data analyst who never guesses.", prompt7)
    print("模型原始返回：", repr(resp7))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

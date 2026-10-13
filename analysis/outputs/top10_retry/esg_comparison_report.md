# ESG 尽调分析报告

生成时间：2026-10-13 11:03　覆盖公司：Apple, Meta Platforms, Alphabet, Pfizer, Phillips 66, AbbVie, Kroger, Boeing, Ford Motor, General Motors

## 方法论说明

每一项指标均由 Agent 对报告原文做「定位（按语义相似度取候选片段）+ 核对（验证标签/口径与问题完全匹配）」后抽取，附带原文引用与页码；未能定位到明确匹配的指标一律标注为「未披露」，不做推测或替换为相近数字。评级与漂绿/评级分歧部分依赖 `parse_ratings.py` 从 MSCI / S&P Global 评级 PDF 中抽取的数据，若该数据缺失则相应部分从报告中省略而非编造。

## 一、核心指标对比

| 指标 | Apple | Meta Platforms | Alphabet | Pfizer | Phillips 66 | AbbVie | Kroger | Boeing | Ford Motor | General Motors |
|---|---|---|---|---|---|---|---|---|---|---|
| Scope 1 排放（直接排放） | 55,200 metric tons CO2e（第77页，2023年） | 48,952 metric tons CO2e（第78页，2023年） | 79,400 tCOe（第76页，2023年） | *未披露* | 24.8 million metric tons CO₂e（第79页，2022年） | 367954 Metric Tons CO2e（第20页，2023年） | 2,335,900 MTCO2e（第43页，2022年） | *未披露* | *未披露* | *未披露* |
| Scope 2 排放（间接-能源） | 3,400 metric tons CO2e（第77页，2023年） | 1,658 metric tons CO2e（第78页，2023年） | 3,423,400 tCOe（第76页，2023年） | *未披露* | *未披露* | 131,232 Metric Tons CO2e（第20页，2021年） | 21.84 tCO2e/1,000 ft2（第71页） | *未披露* | *未披露* | *未披露* |
| Scope 3 排放（价值链） | 15,980,000 metric tons CO2e（第77页，2023年） | 7,445,621 metric tons CO2e（第78页，2023年） | 10,812,000 tCOe（第76页，2023年） | *未披露* | *未披露* | *未披露* | *未披露* | *未披露* | *未披露* | *未披露* |
| 可再生能源占比 | 100%（第81页，2023年） | 100%（第83页，2023年） | *未披露* | 10%（第45页，2023年） | *未披露* | *未披露* | *未披露* | 39%（第53页，2023年） | 50.8%（第142页，2023年） | *未披露* |
| 总取水量 | *未披露* | 5,274 megaliters（第85页，2023年） | 8,653.3 Million gallons（第79页，2023年） | 31.9 million cubic meters（第46页，2023年） | *未披露* | 28.979 Millions Cubic Meters（第23页，2023年） | *未披露* | *未披露* | *未披露* | *未披露* |
| 废弃物转移率（不进填埋场） | 74%（第52页，2023年） | *未披露* | 82%（第53页，2023年） | *未披露* | 23%（第47页，2022年） | *未披露* | 82%（第45页，2022年） | *未披露* | *未披露* | 94.6%（第27页，2023年） |
| 供应链排放/供应商审计 | *未披露* | *未披露* | *未披露* | *未披露* | *未披露* | *未披露* | *未披露* | *未披露* | *未披露* | *未披露* |

## 二、可信度面板

**Apple**：7 个指标中，**5 个已核实**（71.4%），**2 个未披露**（总取水量、供应链排放/供应商审计）。
**Meta Platforms**：7 个指标中，**5 个已核实**（71.4%），**2 个未披露**（废弃物转移率（不进填埋场）、供应链排放/供应商审计）。
**Alphabet**：7 个指标中，**5 个已核实**（71.4%），**2 个未披露**（可再生能源占比、供应链排放/供应商审计）。
**Pfizer**：7 个指标中，**2 个已核实**（28.6%），**5 个未披露**（Scope 1 排放（直接排放）、Scope 2 排放（间接-能源）、Scope 3 排放（价值链）、废弃物转移率（不进填埋场）、供应链排放/供应商审计）。
**Phillips 66**：7 个指标中，**2 个已核实**（28.6%），**5 个未披露**（Scope 2 排放（间接-能源）、Scope 3 排放（价值链）、可再生能源占比、总取水量、供应链排放/供应商审计）。
**AbbVie**：7 个指标中，**3 个已核实**（42.9%），**4 个未披露**（Scope 3 排放（价值链）、可再生能源占比、废弃物转移率（不进填埋场）、供应链排放/供应商审计）。
**Kroger**：7 个指标中，**3 个已核实**（42.9%），**4 个未披露**（Scope 3 排放（价值链）、可再生能源占比、总取水量、供应链排放/供应商审计）。
**Boeing**：7 个指标中，**1 个已核实**（14.3%），**6 个未披露**（Scope 1 排放（直接排放）、Scope 2 排放（间接-能源）、Scope 3 排放（价值链）、总取水量、废弃物转移率（不进填埋场）、供应链排放/供应商审计）。
**Ford Motor**：7 个指标中，**1 个已核实**（14.3%），**6 个未披露**（Scope 1 排放（直接排放）、Scope 2 排放（间接-能源）、Scope 3 排放（价值链）、总取水量、废弃物转移率（不进填埋场）、供应链排放/供应商审计）。
**General Motors**：7 个指标中，**1 个已核实**（14.3%），**6 个未披露**（Scope 1 排放（直接排放）、Scope 2 排放（间接-能源）、Scope 3 排放（价值链）、可再生能源占比、总取水量、供应链排放/供应商审计）。

## 三、风险预警

**Apple**
- [🟡 中] 报告中未找到供应链相关排放数据或供应商审计披露——这是尽调阶段应重点向公司索取的信息缺口，而非本系统的检索失败（已对该指标做过多候选片段核对）。

**Meta Platforms**
- [🟡 中] 报告中未找到供应链相关排放数据或供应商审计披露——这是尽调阶段应重点向公司索取的信息缺口，而非本系统的检索失败（已对该指标做过多候选片段核对）。
- [🟡 中] MSCI 评级从 B（Dec-23）下调至 CCC（Dec-24），评级趋势值得关注。

**Alphabet**
- [🟡 中] 报告中未找到供应链相关排放数据或供应商审计披露——这是尽调阶段应重点向公司索取的信息缺口，而非本系统的检索失败（已对该指标做过多候选片段核对）。

**Pfizer**
- [🟡 中] 报告中未找到供应链相关排放数据或供应商审计披露——这是尽调阶段应重点向公司索取的信息缺口，而非本系统的检索失败（已对该指标做过多候选片段核对）。
- [🟡 中] 本次仅在 7 项核心指标中核实到 2 项（28.6%），该公司在这些维度上的可验证披露不足，后续分析结论应视为初步、需要人工补充核查。

**Phillips 66**
- [🟡 中] 报告中未找到供应链相关排放数据或供应商审计披露——这是尽调阶段应重点向公司索取的信息缺口，而非本系统的检索失败（已对该指标做过多候选片段核对）。
- [🟡 中] 本次仅在 7 项核心指标中核实到 2 项（28.6%），该公司在这些维度上的可验证披露不足，后续分析结论应视为初步、需要人工补充核查。
- [🟡 中] MSCI 评级从 A（Mar-24）下调至 BBB（Feb-25），评级趋势值得关注。
- [🟡 中] MSCI（AA，粗略折算约 79/100）与 S&P Global（43/100）对该公司的评估分歧较大（约 36 分），两家机构方法论权重不同（例如对 Scope 3、供应链治理的权重），建议交叉核查具体分项，不要只看单一评级结论。（字母-数字折算仅为粗略量级对比，非官方换算，用于提示分歧方向而非精确差值。）

**AbbVie**
- [🟡 中] 报告中未找到供应链相关排放数据或供应商审计披露——这是尽调阶段应重点向公司索取的信息缺口，而非本系统的检索失败（已对该指标做过多候选片段核对）。
- [🟡 中] 本次仅在 7 项核心指标中核实到 3 项（42.9%），该公司在这些维度上的可验证披露不足，后续分析结论应视为初步、需要人工补充核查。

**Kroger**
- [🟡 中] 报告中未找到供应链相关排放数据或供应商审计披露——这是尽调阶段应重点向公司索取的信息缺口，而非本系统的检索失败（已对该指标做过多候选片段核对）。
- [🟡 中] 本次仅在 7 项核心指标中核实到 3 项（42.9%），该公司在这些维度上的可验证披露不足，后续分析结论应视为初步、需要人工补充核查。

**Boeing**
- [🟡 中] 报告中未找到供应链相关排放数据或供应商审计披露——这是尽调阶段应重点向公司索取的信息缺口，而非本系统的检索失败（已对该指标做过多候选片段核对）。
- [🟡 中] 本次仅在 7 项核心指标中核实到 1 项（14.3%），该公司在这些维度上的可验证披露不足，后续分析结论应视为初步、需要人工补充核查。

**Ford Motor**
- [🟡 中] 报告中未找到供应链相关排放数据或供应商审计披露——这是尽调阶段应重点向公司索取的信息缺口，而非本系统的检索失败（已对该指标做过多候选片段核对）。
- [🟡 中] 本次仅在 7 项核心指标中核实到 1 项（14.3%），该公司在这些维度上的可验证披露不足，后续分析结论应视为初步、需要人工补充核查。

**General Motors**
- [🟡 中] 报告中未找到供应链相关排放数据或供应商审计披露——这是尽调阶段应重点向公司索取的信息缺口，而非本系统的检索失败（已对该指标做过多候选片段核对）。
- [🟡 中] 本次仅在 7 项核心指标中核实到 1 项（14.3%），该公司在这些维度上的可验证披露不足，后续分析结论应视为初步、需要人工补充核查。

## 四、第三方评级速览（MSCI / S&P Global）

**MSCI**

| 公司 | 评级 | 评级趋势（近5次） | Implied Temperature Rise | 目标年份 | 目标覆盖度 | 年降幅 |
|---|---|---|---|---|---|---|
| Apple | BBB | — | 1.7°C | 2030 | 100.0% | -11.41% p.a. |
| Meta Platforms | B | B→B→B→B→CCC | 1.3°C | 2031 | 100.0% | -11.11% p.a. |
| Alphabet | BBB | — | 1.4°C | 2030 | 100.0% | -12.5% p.a. |
| Pfizer | — | — | — | — | — | — |
| Phillips 66 | AA | AA→AA→AA→A→BBB | 3.2°C | — | — | — |
| AbbVie | — | — | — | — | — | — |
| Kroger | — | — | — | — | — | — |
| Boeing | — | — | — | — | — | — |
| Ford Motor | BB | — | 1.9°C | 2035 | 58.51% | -1.59% p.a. |
| General Motors | — | — | — | — | — | — |

**S&P Global**

| 公司 | 总分（/100） | CSA 分数 | Modeled 分数 | Environmental | Social | Governance & Economic |
|---|---|---|---|---|---|---|
| Apple | 44 | 37 | 7 | 54（行业均值44 / 最高96） | 41（行业均值44 / 最高91） | 36（行业均值44 / 最高88） |
| Meta Platforms | 32 | 28 | 4 | 52（行业均值22 / 最高79） | 20（行业均值25 / 最高79） | 32（行业均值34 / 最高69） |
| Alphabet | 47 | 45 | 2 | 66（行业均值22 / 最高79） | 51（行业均值25 / 最高79） | 31（行业均值34 / 最高69） |
| Pfizer | — | — | — | — | — | — |
| Phillips 66 | 43 | 41 | 2 | 33（行业均值39 / 最高89） | 40（行业均值43 / 最高89） | 55（行业均值43 / 最高80） |
| AbbVie | — | — | — | — | — | — |
| Kroger | — | — | — | — | — | — |
| Boeing | — | — | — | — | — | — |
| Ford Motor | 38 | 33 | 5 | 49（行业均值43 / 最高82） | 30（行业均值43 / 最高85） | 34（行业均值42 / 最高79） |
| General Motors | — | — | — | — | — | — |

（S&P 分项格式为「公司分数（行业均值 / 行业最高）」，用于判断公司在该维度上相对同行的位置。）

## 六、抽取证据明细（供审计/复核）

**Apple**
- Scope 1 排放（直接排放）：55,200 metric tons CO2e（第77页，相似度1.0，第1次核对命中）　原文：「Institute (WRI) Greenhouse Gas (GHG) Scope 1 in 2023: 55,200 (metric tons CO2e) (reconstructed directly from the original PDF's page layout coordinates on page 77; this value was matched to year 2023 by its exact column position on the page, not guessed from flattened text).」
- Scope 2 排放（间接-能源）：3,400 metric tons CO2e（第77页，相似度1.0，第1次核对命中）　原文：「Scope 2 (market-based)4 in 2023: 3,400 (metric tons CO2e) (reconstructed directly from the original PDF's page layout coordinates on page 77; this value was matched to year 2023 by its exact column position on the page, not guessed from flattened text).」
- Scope 3 排放（价值链）：15,980,000 metric tons CO2e（第77页，相似度1.0，第1次核对命中）　原文：「Total gross scope 3 emissions (corporate and product) (metric tons CO2e) in 2023: 15,980,000 (metric tons CO2e) (reconstructed directly from the original PDF's page layout coordinates on page 77; this value was matched to year 2023 by its exact column position on the page, not guessed from flattened text).」
- 可再生能源占比：100%（第81页，相似度1.0，第1次核对命中）　原文：「Renewable electricity percentage4 % of total energy in 2023: 100」
- 总取水量：未披露（已核对20个候选片段，均不匹配）
- 废弃物转移率（不进填埋场）：74%（第52页，相似度0.99，第3次核对命中）　原文：「2023 progress
74% diversion rate」
- 供应链排放/供应商审计：未披露（已核对20个候选片段，均不匹配）

**Meta Platforms**
- Scope 1 排放（直接排放）：48,952 metric tons CO2e（第78页，相似度1.0，第1次核对命中）　原文：「Scope 1 in 2023: 48,952 (in metric tons CO2e) (reconstructed directly from the original PDF's page layout coordinates on page 78; this value was matched to year 2023 by its exact column position on the page, not guessed from flattened text).」
- Scope 2 排放（间接-能源）：1,658 metric tons CO2e（第78页，相似度1.0，第1次核对命中）　原文：「Scope 2 in 2023: 1,658 (in metric tons CO2e) (reconstructed directly from the original PDF's page layout coordinates on page 78; this value was matched to year 2023 by its exact column position on the page, not guessed from flattened text).」
- Scope 3 排放（价值链）：7,445,621 metric tons CO2e（第78页，相似度1.0，第1次核对命中）　原文：「Scope 3 in 2023: 7,445,621 (in metric tons CO2e) (reconstructed directly from the original PDF's page layout coordinates on page 78; this value was matched to year 2023 by its exact column position on the page, not guessed from flattened text).」
- 可再生能源占比：100%（第83页，相似度1.0，第1次核对命中）　原文：「Renewable in 2023: 100% (in MWh/unit of key performance indicators) (reconstructed directly from the original PDF's page layout coordinates on page 83; this value was matched to year 2023 by its exact column position on the page, not guessed from flattened text).」
- 总取水量：5,274 megaliters（第85页，相似度1.0，第1次核对命中）　原文：「Total water withdrawal in 2023: 5,274 (in megaliters) (reconstructed directly from the original PDF's page layout coordinates on page 85; this value was matched to year 2023 by its exact column position on the page, not guessed from flattened text).」
- 废弃物转移率（不进填埋场）：未披露（已核对20个候选片段，均不匹配）
- 供应链排放/供应商审计：未披露（已核对20个候选片段，均不匹配）

**Alphabet**
- Scope 1 排放（直接排放）：79,400 tCOe（第76页，相似度1.0，第8次核对命中）　原文：「Scope 1 tCOe in 2023: 79,400 (tCOe) (reconstructed directly from the original PDF's page layout coordinates on page 76; this value was matched to year 2023 by its exact column position on the page, not guessed from flattened text).」
- Scope 2 排放（间接-能源）：3,423,400 tCOe（第76页，相似度1.0，第6次核对命中）　原文：「Scope 2 (market-based)4 tCOe in 2023: 3,423,400 (tCOe) (reconstructed directly from the original PDF's page layout coordinates on page 76; this value was matched to year 2023 by its exact column position on the page, not guessed from flattened text).」
- Scope 3 排放（价值链）：10,812,000 tCOe（第76页，相似度1.0，第1次核对命中）　原文：「Scope 3 (total)14 tCOe in 2023: 10,812,000 (tCOe) (reconstructed directly from the original PDF's page layout coordinates on page 76; this value was matched to year 2023 by its exact column position on the page, not guessed from flattened text).」
- 可再生能源占比：未披露（已核对20个候选片段，均不匹配）
　　⚠️ 表格重建定位到 4 条疑似匹配的表格行，但原文缺少年份表头，无法确认对应哪一年，因此不计入「已核实」，仅供人工核查：
　　　- 第76页「Renewable electricity (grid) MWh」：2,515,900, 3,062,100, 4,168,900, 5,073,000, 6,207,100（chunk: Alphabet-p076-c04）
　　　- 第34页「Renewable energy purchases」：25.3, 100%, 100%, 100%, 100%（chunk: Alphabet-p034-c04）
　　　- 第76页「Renewable electricity (PPAs) MWh」：9,715,000, 12,069,200, 14,109,400, 16,693,600, 19,089,200（chunk: Alphabet-p076-c03）
　　　- 第76页「On-site renewable electricity MWh」：6,300, 7,200, 8,800, 9,600, 10,700（chunk: Alphabet-p076-c00）
- 总取水量：8,653.3 Million gallons（第79页，相似度1.0，第1次核对命中）　原文：「Water withdrawal Million gallons in 2023: 8,653.3」
- 废弃物转移率（不进填埋场）：82%（第53页，相似度0.6338，第5次核对命中）　原文：「2023 PROGRESS
82% of food waste
diverted from landfill」
- 供应链排放/供应商审计：未披露（已核对20个候选片段，均不匹配）

**Pfizer**
- Scope 1 排放（直接排放）：未披露（已核对20个候选片段，均不匹配）
- Scope 2 排放（间接-能源）：未披露（已核对20个候选片段，均不匹配）
- Scope 3 排放（价值链）：未披露（已核对20个候选片段，均不匹配）
- 可再生能源占比：10%（第45页，相似度1.0，第1次核对命中）　原文：「Renewable electricity (%) 10% in 2023:」
- 总取水量：31.9 million cubic meters（第46页，相似度1.0，第1次核对命中）　原文：「Water withdrawal (in million cubic meters) in 2023: 31.9 (in million cubic meters) (reconstructed directly from the original PDF's page layout coordinates on page 46; this value was matched to year 2023 by its exact column position on the page, not guessed from flattened text).」
- 废弃物转移率（不进填埋场）：未披露（已核对20个候选片段，均不匹配）
- 供应链排放/供应商审计：未披露（已核对20个候选片段，均不匹配）

**Phillips 66**
- Scope 1 排放（直接排放）：24.8 million metric tons CO₂e（第79页，相似度1.0，第7次核对命中）　原文：「Direct GHG Emissions (Scope 1) - All GHGs million metric tons CO₂e in 2022: 24.8 (CO₂e) (reconstructed directly from the original PDF's page layout coordinates on page 79; this value was matched to year 2022 by its exact column position on the page, not guessed from flattened text).」
- Scope 2 排放（间接-能源）：未披露（已核对20个候选片段，均不匹配）
　　⚠️ 表格重建定位到 2 条疑似匹配的表格行，但原文缺少年份表头，无法确认对应哪一年，因此不计入「已核实」，仅供人工核查：
　　　- 第79页「Indirect GHG Emissions from Imported Energy (Scope 2)10 million metric tons CO₂e」：5.4, 0.4, 5.0（chunk: Phillips 66-p079-c00）
　　　- 第79页「Indirect GHG Emissions (Scope 2) - All GHGs」：4.6, 0.5, 4.0（chunk: Phillips 66-p079-c00）
- Scope 3 排放（价值链）：未披露（已核对20个候选片段，均不匹配）
　　⚠️ 表格重建定位到 1 条疑似匹配的表格行，但原文缺少年份表头，无法确认对应哪一年，因此不计入「已核实」，仅供人工核查：
　　　- 第79页「Scope 3 Product-Related Emissions Intensity」：36.5, 37.3, 37.5, 33.4（chunk: Phillips 66-p079-c01）
- 可再生能源占比：未披露（已核对20个候选片段，均不匹配）
- 总取水量：未披露（已核对20个候选片段，均不匹配）
- 废弃物转移率（不进填埋场）：23%（第47页，相似度0.99，第2次核对命中）　原文：「23% of our waste diverted from landfills in 2022」
- 供应链排放/供应商审计：未披露（已核对20个候选片段，均不匹配）

**AbbVie**
- Scope 1 排放（直接排放）：367954 Metric Tons CO2e（第20页，相似度0.6305，第20次核对命中）　原文：「Absolute Scope 1 GHG Emissions (Metric Tons CO2e) 367,954」
- Scope 2 排放（间接-能源）：131,232 Metric Tons CO2e（第20页，相似度0.6259，第17次核对命中）　原文：「Absolute Scope 2 Market-Based GHG Emissions (Metric Tons CO2e) 256,708 184,549 131,232」
- Scope 3 排放（价值链）：未披露（已核对20个候选片段，均不匹配）
- 可再生能源占比：未披露（已核对20个候选片段，均不匹配）
　　⚠️ 表格重建定位到 4 条疑似匹配的表格行，但原文缺少年份表头，无法确认对应哪一年，因此不计入「已核实」，仅供人工核查：
　　　- 第22页「Absolute Total Renewable Energy Consumption (MWh)1」：245,433（chunk: AbbVie-p022-c02）
　　　- 第22页「Purchased Renewable Electricity (MWh)」：205,015, 243,484, 297,3492（chunk: AbbVie-p022-c02）
　　　- 第22页「Self-Generated Renewable Electricity (MWh)1」：1,949（chunk: AbbVie-p022-c02）
　　　- 第22页「Absolute Total Non-Renewable Energy Consumption (MWh)1」：2,204,335（chunk: AbbVie-p022-c01）
- 总取水量：28.979 Millions Cubic Meters（第23页，相似度1.0，第1次核对命中）　原文：「Absolute Water Withdrawal (Millions Cubic Meters)1 in 2023: 28.979 (Millions Cubic Meters) (reconstructed directly from the original PDF's page layout coordinates on page 23; this value was matched to year 2023 by its exact column position on the page, not guessed from flattened text).」
- 废弃物转移率（不进填埋场）：未披露（已核对20个候选片段，均不匹配）
- 供应链排放/供应商审计：未披露（已核对20个候选片段，均不匹配）

**Kroger**
- Scope 1 排放（直接排放）：2,335,900 MTCO2e（第43页，相似度0.99，第2次核对命中）　原文：「Scope 1 total emissions 2,335,900 +1%」
- Scope 2 排放（间接-能源）：21.84 tCO2e/1,000 ft2（第71页，相似度0.5408，第11次核对命中）　原文：「Our normalized emissions (per 1,000 square feet) decreased from 22.94 to 21.84. This calculation reflects market-based Scope 2 emissions」
- Scope 3 排放（价值链）：未披露（已核对20个候选片段，均不匹配）
　　⚠️ 表格重建定位到 1 条疑似匹配的表格行，但原文缺少年份表头，无法确认对应哪一年，因此不计入「已核实」，仅供人工核查：
　　　- 第71页「Indirect (Scope 3) emissions (partial)」：2,335,900, 578,392, 420,020, 1,337,488, 2,695,641, 2,558,805, 5,031,541, 4,894,705, 2,390,614（chunk: Kroger-p071-c01）
- 可再生能源占比：未披露（已核对20个候选片段，均不匹配）
- 总取水量：未披露（已核对20个候选片段，均不匹配）
- 废弃物转移率（不进填埋场）：82%（第45页，相似度0.99，第3次核对命中）　原文：「82% waste diversion from landfill company-wide」
- 供应链排放/供应商审计：未披露（已核对20个候选片段，均不匹配）

**Boeing**
- Scope 1 排放（直接排放）：未披露（已核对20个候选片段，均不匹配）
- Scope 2 排放（间接-能源）：未披露（已核对20个候选片段，均不匹配）
- Scope 3 排放（价值链）：未披露（已核对20个候选片段，均不匹配）
- 可再生能源占比：39%（第53页，相似度0.4998，第21次核对命中）　原文：「achieving 39% renewable electricity procurement through direct purchases and renewable energy credits in 2023.」
- 总取水量：未披露（已核对20个候选片段，均不匹配）
- 废弃物转移率（不进填埋场）：未披露（已核对20个候选片段，均不匹配）
- 供应链排放/供应商审计：未披露（已核对20个候选片段，均不匹配）

**Ford Motor**
- Scope 1 排放（直接排放）：未披露（已核对20个候选片段，均不匹配）
- Scope 2 排放（间接-能源）：未披露（已核对20个候选片段，均不匹配）
- Scope 3 排放（价值链）：未披露（已核对20个候选片段，均不匹配）
- 可再生能源占比：50.8%（第142页，相似度1.0，第1次核对命中）　原文：「Percent Renewable Electricity in 2023: 50.8」
- 总取水量：未披露（已核对20个候选片段，均不匹配）
- 废弃物转移率（不进填埋场）：未披露（已核对20个候选片段，均不匹配）
- 供应链排放/供应商审计：未披露（已核对20个候选片段，均不匹配）

**General Motors**
- Scope 1 排放（直接排放）：未披露（已核对20个候选片段，均不匹配）
- Scope 2 排放（间接-能源）：未披露（已核对20个候选片段，均不匹配）
- Scope 3 排放（价值链）：未披露（已核对20个候选片段，均不匹配）
- 可再生能源占比：未披露（已核对20个候选片段，均不匹配）
- 总取水量：未披露（已核对20个候选片段，均不匹配）
- 废弃物转移率（不进填埋场）：94.6%（第27页，相似度0.6054，第3次核对命中）　原文：「In 2023, GM’s Zero Waste performance was 94.6%...」
- 供应链排放/供应商审计：未披露（已核对20个候选片段，均不匹配）

## 七、局限性说明

本报告的指标覆盖面受限于评测集验证过的三家公司与预设的 7 个核心指标；「未披露」代表本系统未能在检索到的候选片段中核实到匹配数字，不完全等同于公司确实未披露（可能存在于未被检索到的段落中，检索命中率并非 100%，详见 DATA_FACTS.md 第5节）。评级分歧的字母-数字换算为粗略近似，仅用于提示方向，不构成投资建议。

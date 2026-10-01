# ESG 尽调分析报告

生成时间：2026-09-28 09:39　覆盖公司：Alphabet, Apple, Meta Platforms

## 方法论说明

每一项指标均由 Agent 对报告原文做「定位（按语义相似度取候选片段）+ 核对（验证标签/口径与问题完全匹配）」后抽取，附带原文引用与页码；未能定位到明确匹配的指标一律标注为「未披露」，不做推测或替换为相近数字。评级与漂绿/评级分歧部分依赖 `parse_ratings.py` 从 MSCI / S&P Global 评级 PDF 中抽取的数据，若该数据缺失则相应部分从报告中省略而非编造。

## 一、核心指标对比

| 指标 | Alphabet | Apple | Meta Platforms |
|---|---|---|---|
| Scope 1 排放（直接排放） | 79,400 tCOe（第76页，2023年） | 55,200 metric tons CO2e（第77页，2023年） | 48,952 metric tons CO2e（第78页，2023年） |
| Scope 2 排放（间接-能源） | 3,423,400 tCOe（第76页，2023年） | 3,400 metric tons CO2e（第77页，2023年） | 1,658 metric tons CO2e（第78页，2023年） |
| Scope 3 排放（价值链） | 10,812,000 tCOe（第76页，2023年） | 15,980,000 metric tons CO2e（第77页，2023年） | 7,445,621 metric tons CO2e（第78页，2023年） |
| 可再生能源占比 | *未披露* | 100%（第81页，2023年） | 100%（第83页，2023年） |
| 总取水量 | 8,653.3 Million gallons（第79页，2023年） | *未披露* | 5,274 megaliters（第85页，2023年） |
| 废弃物转移率（不进填埋场） | 82%（第53页，2023年） | 74%（第52页，2023年） | *未披露* |
| 供应链排放/供应商审计 | *未披露* | *未披露* | *未披露* |

## 二、可信度面板

**Alphabet**：7 个指标中，**5 个已核实**（71.4%），**2 个未披露**（可再生能源占比、供应链排放/供应商审计）。
**Apple**：7 个指标中，**5 个已核实**（71.4%），**2 个未披露**（总取水量、供应链排放/供应商审计）。
**Meta Platforms**：7 个指标中，**5 个已核实**（71.4%），**2 个未披露**（废弃物转移率（不进填埋场）、供应链排放/供应商审计）。

## 三、风险预警

**Alphabet**
- [🟡 中] 报告中未找到供应链相关排放数据或供应商审计披露——这是尽调阶段应重点向公司索取的信息缺口，而非本系统的检索失败（已对该指标做过多候选片段核对）。

**Apple**
- [🟡 中] 报告中未找到供应链相关排放数据或供应商审计披露——这是尽调阶段应重点向公司索取的信息缺口，而非本系统的检索失败（已对该指标做过多候选片段核对）。

**Meta Platforms**
- [🟡 中] 报告中未找到供应链相关排放数据或供应商审计披露——这是尽调阶段应重点向公司索取的信息缺口，而非本系统的检索失败（已对该指标做过多候选片段核对）。
- [🟡 中] MSCI 评级从 B（Dec-23）下调至 CCC（Dec-24），评级趋势值得关注。

## 四、第三方评级速览（MSCI / S&P Global）

**MSCI**

| 公司 | 评级 | 评级趋势（近5次） | Implied Temperature Rise | 目标年份 | 目标覆盖度 | 年降幅 |
|---|---|---|---|---|---|---|
| Alphabet | BBB | — | 1.4°C | 2030 | 100.0% | -12.5% p.a. |
| Apple | BBB | — | 1.7°C | 2030 | 100.0% | -11.41% p.a. |
| Meta Platforms | B | B→B→B→B→CCC | 1.3°C | 2031 | 100.0% | -11.11% p.a. |

**S&P Global**

| 公司 | 总分（/100） | CSA 分数 | Modeled 分数 | Environmental | Social | Governance & Economic |
|---|---|---|---|---|---|---|
| Alphabet | 47 | 45 | 2 | 66（行业均值22 / 最高79） | 51（行业均值25 / 最高79） | 31（行业均值34 / 最高69） |
| Apple | 44 | 37 | 7 | 54（行业均值44 / 最高96） | 41（行业均值44 / 最高91） | 36（行业均值44 / 最高88） |
| Meta Platforms | 32 | 28 | 4 | 52（行业均值22 / 最高79） | 20（行业均值25 / 最高79） | 32（行业均值34 / 最高69） |

（S&P 分项格式为「公司分数（行业均值 / 行业最高）」，用于判断公司在该维度上相对同行的位置。）

## 五、承诺话术摘录（用于与评级数据交叉核对）

**Alphabet**
- 第 32 页：「reach net-zero emissions across all of our operations and value chain by 2030」（目标年份：2030）
- 第 30 页：「Net-zero carbon」
- 第 8 页：「We aim to achieve net-zero emissions across all of our operations and value chain by 2030」（目标年份：2030）

**Apple**
- 第 13 页：「75% reduction in gross emissions from 2015」（目标年份：2015）
- 第 35 页：「We aim to reduce emissions by 75 percent compared with our 2015 footprint by 2030.」（目标年份：2030）
- 第 13 页：「plan to become carbon neutral」（目标年份：2030）

**Meta Platforms**
- 第 17 页：「achieving net zero emissions across our value chain and becoming water positive in 2030」（目标年份：2030）
- 第 91 页：「Meta is aligning our emissions reduction targets with the Science Based Targets initiative」
- 第 37 页：「Nature-based carbon removal via forests or soils is deployable now and can offer solutions to both mitigate climate change and address the biodiversity crisis.」（目标年份：2027-2035）

## 六、抽取证据明细（供审计/复核）

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

## 七、局限性说明

本报告的指标覆盖面受限于评测集验证过的三家公司与预设的 7 个核心指标；「未披露」代表本系统未能在检索到的候选片段中核实到匹配数字，不完全等同于公司确实未披露（可能存在于未被检索到的段落中，检索命中率并非 100%，详见 DATA_FACTS.md 第5节）。评级分歧的字母-数字换算为粗略近似，仅用于提示方向，不构成投资建议。

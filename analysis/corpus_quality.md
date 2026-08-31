# 语料质量体检报告

本报告由 `src/build_corpus.py` 自动生成，共检查 50 家公司，其中 **42 家可用**、8 家存在解析问题。

## 一、修复效果

原 `Data.ipynb` 使用 `re.sub(r"(?<=\n)\d{1,2}", "", text)` 删除页码，导致 ESG 数据表中所有数字的前 1-2 位被一并删除（例：`3,423,400` → `,423,400`）。

本次重建改为只删除「独占一行的纯数字」，下表 `残缺数字` 列应全部为 0。

## 二、逐公司明细

| 行业 | 公司 | 页数 | chunks | 每页字符 | 数字完好 | 数字残缺 | 状态 |
|---|---|---:|---:|---:|---:|---:|---|
| Aerospace & Defense | Boeing | 120 | 384 | 2660 | 319 | 0 | ✓ 可用 |
| Aerospace & Defense | Lockheed Martin | 54 | 155 | 2379 | 313 | 0 | ✓ 可用 |
| Energy | Chevron | 61 | 191 | 2636 | 9 | 0 | ✓ 可用 |
| Energy | Exxon Mobil | 12 | 46 | 3074 | 7 | 0 | ✗ TOO_FEW_CONTENT |
| Energy | Marathon Petroleum | 58 | 297 | 4502 | 472 | 0 | ✓ 可用 |
| Energy | Phillips 66 | 88 | 250 | 2268 | 176 | 0 | ✓ 可用 |
| Energy | Valero Energy | 80 | 500 | 5517 | 266 | 0 | ✓ 可用 |
| Financials | Allstate | 12 | 36 | 2348 | 35 | 0 | ✗ TOO_FEW_CONTENT |
| Financials | Bank of America | 124 | 474 | 3205 | 502 | 0 | ✓ 可用 |
| Financials | Citigroup | 102 | 419 | 3530 | 266 | 0 | ✓ 可用 |
| Financials | Goldman Sachs Group | 102 | 315 | 2474 | 164 | 0 | ✓ 可用 |
| Financials | JPMorgan Chase | 88 | 407 | 3996 | 136 | 0 | ✓ 可用 |
| Financials | MetLife | 160 | 434 | 2161 | 242 | 0 | ✓ 可用 |
| Financials | Morgan Stanley | 93 | 329 | 2912 | 234 | 0 | ✓ 可用 |
| Food&Drug Stores | Kroger | 71 | 295 | 3532 | 156 | 0 | ✓ 可用 |
| Food&Drug Stores | Walgreens Boots Alliance | 129 | 485 | 3153 | 196 | 0 | ✓ 可用 |
| Food,Beverages&Tobacco | Archer Daniels Midland | 59 | 148 | 1971 | 126 | 0 | ✓ 可用 |
| Food,Beverages&Tobacco | PepsiCo | 67 | 204 | 2532 | 44 | 0 | ✓ 可用 |
| Food,Beverages&Tobacco | Tyson Foods | 10 | 22 | 1576 | 90 | 0 | ✗ TOO_FEW_CONTENT |
| Health Care | AbbVie | 90 | 266 | 2438 | 162 | 0 | ✓ 可用 |
| Health Care | CVS Health | 93 | 285 | 2487 | 143 | 0 | ✓ 可用 |
| Health Care | Cardinal Health | 7 | 16 | 1877 | 21 | 0 | ✗ TOO_FEW_CONTENT |
| Health Care | Cigna | 98 | 322 | 2773 | 119 | 0 | ✓ 可用 |
| Health Care | Johnson & Johnson | 111 | 528 | 4092 | 345 | 1 | ✓ 可用 |
| Health Care | McKesson | 61 | 168 | 2193 | 142 | 0 | ✓ 可用 |
| Health Care | Pfizer | 84 | 281 | 2766 | 12 | 0 | ✓ 可用 |
| Health Care | UnitedHealth Group | 93 | 285 | 2487 | 143 | 0 | ✓ 可用 |
| Household Products | Procter & Gamble | 83 | 154 | 1296 | 34 | 0 | ✓ 可用 |
| Industrials | Caterpillar | 67 | 192 | 2274 | 49 | 0 | ✓ 可用 |
| Industrials | General Electric | 5 | 32 | 5931 | 43 | 0 | ✗ TOO_FEW_CONTENT |
| Media | Walt Disney | 80 | 295 | 3134 | 233 | 0 | ✓ 可用 |
| Motor Vehicles&Parts | Ford Motor | 209 | 880 | 3621 | 292 | 0 | ✓ 可用 |
| Motor Vehicles&Parts | General Motors | 84 | 286 | 2810 | 61 | 0 | ✓ 可用 |
| Motor Vehicles&Parts | Tesla | 38 | 44 | 610 | 20 | 0 | ✗ TOO_FEW_CONTENT |
| Retailing | Amazon | 98 | 502 | 4541 | 221 | 0 | ✓ 可用 |
| Retailing | Costco Wholesale | 109 | 334 | 2520 | 92 | 0 | ✓ 可用 |
| Retailing | Home Depot | 112 | 321 | 2360 | 196 | 0 | ✓ 可用 |
| Retailing | Walmart | 43 | 109 | 1996 | 16 | 0 | ✓ 可用 |
| Technology | Alphabet | 85 | 404 | 4098 | 282 | 0 | ✓ 可用 |
| Technology | Apple | 113 | 426 | 3196 | 400 | 0 | ✓ 可用 |
| Technology | Cisco Systems | 67 | 202 | 2432 | 32 | 0 | ✓ 可用 |
| Technology | Dell Technologies | 116 | 420 | 3063 | 159 | 0 | ✓ 可用 |
| Technology | Meta Platforms | 94 | 174 | 1344 | 474 | 0 | ✓ 可用 |
| Technology | Microsoft | 88 | 307 | 2924 | 92 | 0 | ✓ 可用 |
| Technology | Tencent Holdings | 116 | 562 | 4241 | 190 | 0 | ✓ 可用 |
| Telecommunications | AT&T | 50 | 139 | 2260 | 74 | 0 | ✓ 可用 |
| Telecommunications | Comcast Corporation | 0 | 0 | 0 | 0 | 0 | ✗ NO_REPORT |
| Telecommunications | Verizon Communications | 71 | 252 | 2987 | 69 | 0 | ✓ 可用 |
| Transportation | FedEx | 44 | 158 | 3030 | 312 | 0 | ✓ 可用 |
| Transportation | United Parcel Service | 6 | 10 | 880 | 8 | 0 | ✗ TOO_FEW_CONTENT |

## 三、不可用公司说明

- **Exxon Mobil**（Energy）：全文内容过少，可能只是摘要小册子而非完整报告。PDF 6.9 MB，仅解析出 36883 字符 / 12 页。
- **Allstate**（Financials）：全文内容过少，可能只是摘要小册子而非完整报告。PDF 1.1 MB，仅解析出 28170 字符 / 12 页。
- **Tyson Foods**（Food,Beverages&Tobacco）：全文内容过少，可能只是摘要小册子而非完整报告。PDF 0.1 MB，仅解析出 15755 字符 / 10 页。
- **Cardinal Health**（Health Care）：全文内容过少，可能只是摘要小册子而非完整报告。PDF 3.9 MB，仅解析出 13138 字符 / 7 页。
- **General Electric**（Industrials）：全文内容过少，可能只是摘要小册子而非完整报告。PDF 1.5 MB，仅解析出 29653 字符 / 5 页。
- **Tesla**（Motor Vehicles&Parts）：全文内容过少，可能只是摘要小册子而非完整报告。PDF 26.6 MB，仅解析出 23177 字符 / 38 页。
- **Comcast Corporation**（Telecommunications）：目录中未找到企业主报告 PDF。PDF 0.0 MB，仅解析出 0 字符 / 0 页。
- **United Parcel Service**（Transportation）：全文内容过少，可能只是摘要小册子而非完整报告。PDF 3.5 MB，仅解析出 5279 字符 / 6 页。

## 四、对评测集的影响

标注 benchmark 时应只从「可用」公司中选题。不可用公司若纳入评测，其拒答会被误判为模型问题，实际是语料层缺陷。

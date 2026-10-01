# Precision / Recall / F1 评估报告

ground truth 来源：`analysis/ground_truth.json`（人工直接核对原始 PDF 原文确认，跟 pipeline 输出完全独立生成）。pipeline 输出来源：`analysis/outputs/esg_comparison.json`。

## 混淆矩阵

| | pipeline 说披露 | pipeline 说未披露 |
|---|---|---|
| **ground truth 确实披露** | TP = 14 | FN = 5 |
| **ground truth 确实未披露** | FP = 1 | TN = 1 |

- **Precision（取到的里面有多少是对的）= 93.3%**
- **Recall（该取到的里面取到了多少）= 73.7%**
- **F1 = 0.824**

## 逐条明细

| 公司 | 指标 | 判定 | ground truth | pipeline 结果 | 原因 |
|---|---|---|---|---|---|
| Alphabet | scope1_emissions | TP | 79,400（2023） | 79,400（2023） | 数值/年份都对得上（79400.0 vs ground truth 79400.0，2023年） |
| Alphabet | scope2_emissions | TP | 3,423,400（2023） | 3,423,400（2023） | 数值/年份都对得上（3423400.0 vs ground truth 3423400.0，2023年） |
| Alphabet | scope3_emissions | TP | 10,812,000（2023） | 10,812,000（2023） | 数值/年份都对得上（10812000.0 vs ground truth 10812000.0，2023年） |
| Alphabet | renewable_energy_pct | FN | 100（2023） | 未披露 | ground truth 确实有披露，但 pipeline 保守地标了未披露，错失了本来能取到的数字 |
| Alphabet | water_withdrawal | TP | 8,653.3（2023） | 8,653.3（2023） | 数值/年份都对得上（8653.3 vs ground truth 8653.3，2023年） |
| Alphabet | waste_diversion_pct | FP | 78（2023） | 82%（2023） | 数值对不上：pipeline 取到 82.0，ground truth 是 78.0（很可能抓到了别的年份/口径/子类目） |
| Alphabet | supply_chain_emissions | FN | flexible（2023） | 未披露 | ground truth 确实有披露，但 pipeline 保守地标了未披露，错失了本来能取到的数字 |
| Apple | scope1_emissions | TP | 55,200（2023） | 55,200（2023） | 数值/年份都对得上（55200.0 vs ground truth 55200.0，2023年） |
| Apple | scope2_emissions | TP | 3,400（2023） | 3,400（2023） | 数值/年份都对得上（3400.0 vs ground truth 3400.0，2023年） |
| Apple | scope3_emissions | TP | 15,980,000（2023） | 15,980,000（2023） | 数值/年份都对得上（15980000.0 vs ground truth 15980000.0，2023年） |
| Apple | renewable_energy_pct | TP | 100（2023） | 100（2023） | 数值/年份都对得上（100.0 vs ground truth 100.0，2023年） |
| Apple | water_withdrawal | FN | 1,610（2023） | 未披露 | ground truth 确实有披露，但 pipeline 保守地标了未披露，错失了本来能取到的数字 |
| Apple | waste_diversion_pct | TP | 74（2023） | 74%（2023） | 数值/年份都对得上（74.0 vs ground truth 74.0，2023年） |
| Apple | supply_chain_emissions | FN | flexible（2023） | 未披露 | ground truth 确实有披露，但 pipeline 保守地标了未披露，错失了本来能取到的数字 |
| Meta Platforms | scope1_emissions | TP | 48,952（2023） | 48,952（2023） | 数值/年份都对得上（48952.0 vs ground truth 48952.0，2023年） |
| Meta Platforms | scope2_emissions | TP | 1,658（2023） | 1,658（2023） | 数值/年份都对得上（1658.0 vs ground truth 1658.0，2023年） |
| Meta Platforms | scope3_emissions | TP | 7,445,621（2023） | 7,445,621（2023） | 数值/年份都对得上（7445621.0 vs ground truth 7445621.0，2023年） |
| Meta Platforms | renewable_energy_pct | TP | 100（2023） | 100%（2023） | 数值/年份都对得上（100.0 vs ground truth 100.0，2023年） |
| Meta Platforms | water_withdrawal | TP | 5,274（2023） | 5,274（2023） | 数值/年份都对得上（5274.0 vs ground truth 5274.0，2023年） |
| Meta Platforms | waste_diversion_pct | TN | 未披露 | 未披露 | ground truth 确实未披露，pipeline 也如实标未披露 |
| Meta Platforms | supply_chain_emissions | FN | flexible（2023） | 未披露 | ground truth 确实有披露，但 pipeline 保守地标了未披露，错失了本来能取到的数字 |

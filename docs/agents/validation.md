# 交叉校验与可信度

改 `validators.py`、`confidence.py`、来源锚定（`anchoring.py` / `extraction.enrich_provenance`）、字段状态或起诉状就绪度时读本文件。

## 交叉校验（②：确定性代码，非 LLM）

实现于 `backend/app/services/validators.py`：

- **金额勾稽**：`total_amount == paid_amount + unpaid_amount`（浮点按分容差）
- **台数一致**：`elevator_qty_by_approval == elevator_qty_by_contract == elevator_qty_by_acceptance`
- **信用代码**：18 位、字符集、校验位
- **日期**：合法性、`acceptance_latest_date` 取各验收报告最晚合格日
- **名称**：不做分公司核实。原告按规则拼接（见 [complaint-rules.md](complaint-rules.md)「原告」）；被告是分公司时只起诉分公司，不列总公司（律师确认）
- **台数**：有验收报告台数时以其为准、不暂停；只有审批表与合同一致而报告不同时标待核实。无验收报告台数时三源不一致仍暂停问律师

校验产出：每个字段的 `status` 结论（正常/冲突/缺失）+ 供展示的高亮说明文本。
通过但需律师留意的检查（如台数不一致、已按规则取验收报告口径）带 `needs_review`，校验面板标琥珀色，不算冲突。金额在说明中一律写到分（千分位 + 两位小数）。
`prompt-a-validate.md` **降级**为"仅生成给律师看的自然语言高亮说明"，不再承担一致性判断。

---

## 可信度评分（③：分层）

**A. 确定性硬信号（是/否，直接决定 `FieldStatus`）**
- 来源锚定：`value` 能否在原文逐页命中（`extraction.enrich_provenance`）。否 → src 追加「（原文未找到，待核实）」；命中的文件与模型标注的不同 → src 改为实际命中的文件，与页码一致；值在多页都能命中时优先模型写的那一页，那一页没命中则把 src 里的「第N页」改成实际命中页，出处文字与「原件第 N 页」按钮一致（#120）。抽取后与 OCR 补读后各锚定一次，标记每次重算
- 结构化出处（#60，试点）：锚定同时写 `source_file`（实际命中的文件名，未命中为 null）和 `verified`（是否命中），规则用 `extraction.field_source_file` 读取，不解析 src 文字；src 仍是给律师看的说明。目前按结构化出处判断的字段见 `extraction.STRUCTURED_SOURCE_KEYS`（签约日期：战略合同把关、要不要读合同签章页）。签约日期只命中战略合同 → 清空（缺失）；原文哪里都没命中 → 保留、标待核实，不按模型写的文件名清空。旧会话 / 刚抽出还没锚定的字段没有 `source_file`，退回从 src 取文件名
- 格式校验：信用代码/日期/金额可解析
- 勾稽/一致性：金额、台数（见上）

**B. 软估计（加权成 0–100 confidence，仅排序/默认展开）**

| 信号 | 权重 | 取值 |
|---|---|---|
| 来源通道 | 40 | 文本=1.0 / OCR=0.6 / 多模态=0.7 |
| 锚定质量 | 30 | 精确命中=1.0 / 模糊=0.5 / 无=0 |
| 自一致性 | 20 | 两次或两模型一致=1.0 / 否=0 |
| 模型自评 | 10 | 模型输出的 confidence（弱信号） |

`confidence = Σ(权重 × 信号值)`，实现于 `backend/app/services/confidence.py`（纯确定性、可单测）。

**score → 状态映射**：

```
勾稽冲突 / 台数不一致        → ⚠️ 冲突（最高优先级）
value=null                    → ❌ 缺失
锚定失败（原文未找到）        → 🔍 待核实
来源含 OCR/扫描 或 conf<60   → 🔍 待核实
其余                          → ✅ 正常
```

审核页用 confidence 排序、决定默认展开；数字放 hover，不喧宾夺主。
案件级另给"起诉状就绪度"，比单一总分更贴合律师心智：按起诉状模板的实际填空统计（措辞类占位符不计），有值且不冲突的占比（`complaint_renderer.fill_status`），与预览页「N 处待补充」口径一致。只有 100% 才显示绿色，还有待补充时显示琥珀色。

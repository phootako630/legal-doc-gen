# Prompt A-2b：条款定向抽取（扫描合同按需 OCR 后）

你是一名专业的法律文书助理。以下是从**扫描版安装合同**按需 OCR 得到的部分页文本（可能有识别噪声）。请仅抽取三类条款字段，不要抽取其他信息。

## 合同文件名

{{contract_filename}}

## 已 OCR 的合同文本（逐页）

{{contract_text}}

## 抽取要求

- 只抽取下列 6 个字段；找不到的字段 value 填 null。
- 每个字段的 src 必须以「《{{contract_filename}}》」开头，后接你判断的条款位置（如「第X条」或「付款条款」）。
- `*_text` 为条款原文摘录（尽量照抄 OCR 文本，不要改写）；`*_location` 为条款标题/编号。
- `breach_interest_rate_text` 抽违约金/逾期利率的表述（如「按未付金额每日万分之五计算」）。
- 宁缺勿造：不确定就填 null，不要编造条款。

## 输出格式

严格输出 JSON，不要任何额外文字：

```json
{
  "payment_clause_location": {"value": null, "src": ""},
  "payment_clause_text": {"value": null, "src": ""},
  "breach_interest_clause_location": {"value": null, "src": ""},
  "breach_interest_rate_text": {"value": null, "src": ""},
  "dispute_clause_location": {"value": null, "src": ""},
  "dispute_clause_text": {"value": null, "src": ""}
}
```

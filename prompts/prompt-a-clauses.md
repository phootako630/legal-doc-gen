# Prompt A-2b：条款定向抽取（扫描合同按需 OCR 后）

你是一名专业的法律文书助理。以下是从**扫描版安装合同**按需 OCR 得到的部分页文本（可能有识别噪声）。请抽取三类条款字段，以及三个以合同为准的基本信息字段，不要抽取其他信息。

## 合同文件名

{{contract_filename}}

## 已 OCR 的合同文本（逐页）

{{contract_text}}

## 抽取要求

- 只抽取下列 12 个字段；找不到的字段 value 填 null。
- 付款条款取**专用条款**里约定付款节点与比例的那一章（如「合同价款支付」：进度款/验收款各付合同总价的百分之几），不要取通用条款里的付款申请程序；目录页只列章名，不是条款正文。
- 争议条款取「争议」章中关于向哪里起诉/仲裁的约定。若条款提到「工程所在地」「合同签订地」「合同履行地」，`dispute_clause_location` 同时写出合同中约定该地点的条款（如协议书写安装地址的那一款），例如「第二十章第1.1条及第一条第2款」。
- `payment_clause_summary`：把付款条款归纳成一句话，写明各付款节点与比例，例如「电梯安装完成后，30个工作日内支付合同总价的60%；电梯通过当地政府部门验收合格、完整移交并办理完工程结算手续后，30个工作日内支付至合同总价的100%」。数字必须与原文一致。
- `retention_ratio`：合同约定的质保金（保修金）比例，如「5%」；合同明确没有质保金填「无」；找不到填 null。
- `contract_sign_date`：合同盖章页（协议书末尾签署处）的签署日期，写成「2024年2月26日」；不要取封面的「合同订立时间」。双方日期不同取较晚的，都没有填 null。
- `contract_title`：合同封面上的合同全称（不含书名号）。
- `elevator_qty_by_contract`：合同约定的安装台数（协议书「承包范围」或计价清单的合计台数），只填数字。
- `project_site`：协议书「安装地址 / 工程地点」的完整地址，保留省、市、区（县），去掉 OCR 插入的空格。
- 每个字段的 src 必须以「《{{contract_filename}}》」开头，后接你判断的条款位置（如「第X条」或「付款条款」）。
- `*_text` 为条款原文摘录（尽量照抄 OCR 文本，不要改写）；`*_location` 为条款章节/编号，会直接写进起诉状「依据合同<location>约定」，请写成「第二十八章」「第二十章第1.1条」这类形式。
- `breach_interest_rate_text` 只抽**甲方（发包方 / 买方）逾期付款**的利率或违约金标准，写成能直接放进「按照____计至实际付清之日止」的短语，如「每日万分之五的标准」「年利率6%的标准」；乙方工期、施工等方面的违约金不算，没有就填 null。
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
  "dispute_clause_text": {"value": null, "src": ""},
  "contract_title": {"value": null, "src": ""},
  "elevator_qty_by_contract": {"value": null, "src": ""},
  "project_site": {"value": null, "src": ""},
  "payment_clause_summary": {"value": null, "src": ""},
  "retention_ratio": {"value": null, "src": ""},
  "contract_sign_date": {"value": null, "src": ""}
}
```

# 案件 eval

跑或改案件 eval（`backend/scripts/eval_cases.py`、`case_eval/`）、补虚构案卷、更新 baseline，或落实律师答复 / 改抽取规则后回归时读本文件。

## 打分与回放

- 用律师已写好诉状的真实案卷跑系统（与网页同一条链路，不经浏览器），按 `expected.json` 的标准字段逐项打分：正确 / 错误 / 缺失 / 待定（待律师确认，不计分）；按字段比，不逐句比诉状（律师措辞本就不统一）
- 标准字段可选填 `source`（预期来源文件）与 `review: "待核实"`：来源按值回原文重新锚定后实际命中的文件判断（不信模型自述）；审核页显示「正常」却值错 / 应标待核实 / 回原文未命中的记为静默错误。两项单独计数、不影响准确率，前后两次运行对比也列出（`case_eval/scoring.py`）
- OCR / LLM 返回按请求内容哈希录制到案件的 `cache/`：改规则后 `--strict` 只回放、零费用；换模型或改提示词后 `--mode live` 重建
- 每次运行写 `_runs/<时间>/report.md`，并与上次逐字段对比；落实律师答复、改抽取规则后跑一遍，看有没有改坏别的案件
- 真实案卷与标准字段放服务器 `/srv/eval-cases`（见 `deploy/README.md`「案件 eval」），不进仓库；新案卷核对完（`case-review`）就补一份 `expected.json` 加进去
- 错误 / 缺失的字段另做归因（#108，`case_eval/attribution.py`）：在扫描件 OCR 文本里找标准值，分抽取错误 / 其他页有标准值 / 疑似 OCR 错误 / 可能未读到 / 非 OCR 字段 / 规则推导，报告分类计数，用来判断该投入改 OCR 还是改抽取
- 两套案卷各有用途：**虚构案卷集** `backend/eval_fixtures/`（进仓库、当事人金额地址全虚构，缓存已录制）在 CI 的 eval job 里严格回放，与 `baseline.json` 比较，任一字段变差（正确 → 错误 / 缺失、缺失 → 错误、来源变错、新静默错误）或缓存未命中即失败（`case_eval/baseline.py`，`--baseline` / `--update-baseline`）；**真实案卷集**放服务器、手动跑，看真实材料上的准确率。报告单列「错误字段」（看起来正常的错值，比缺失危险）
- 新规则落地时，先往虚构案卷集补一个能体现该规则的案件（`eval_fixtures/_build/build_fixtures.py` 生成材料、写 `expected.json`、`--mode live` 录缓存、确认后 `--update-baseline`），见 `backend/eval_fixtures/README.md`

# 案件 eval

跑或改案件 eval（`backend/scripts/eval_cases.py`、`case_eval/`）、补虚构案卷、更新 baseline，或落实律师答复 / 改抽取规则后回归时读本文件。

## 打分与回放

- 用律师已写好诉状的真实案卷跑系统（与网页同一条链路，不经浏览器），按 `expected.json` 的标准字段逐项打分：正确 / 错误 / 缺失 / 待定（待律师确认，不计分）；按字段比，不逐句比诉状（律师措辞本就不统一）
- 标准字段可选填 `source`（预期来源文件）与 `review: "待核实"`：来源按值回原文重新锚定后实际命中的文件判断（不信模型自述）；审核页显示「正常」却值错 / 应标待核实 / 回原文未命中的记为静默错误。两项单独计数、不影响准确率，前后两次运行对比也列出（`case_eval/scoring.py`）
- LLM 裁判（#90，分步接入中）：规则拼接的整句（管辖句、违约条款引用句、付款节点引用、移交句、利率基准等）可写 `"match": "judge"`，由裁判判断与律师写法是否法律意义上一致；只有白名单字段（`case_eval/judge_rubrics.py` 的 `JUDGE_CRITERIA`，每个都有默认判断标准，可用 `criteria` 覆盖）能交给裁判；其余字段（主体、编号、金额、比例、台数、日期、电话、条款原文与位置、地址等）配成 judge 也退回逐字比对，报告「配置警告」一节单列。裁判（`case_eval/judge.py`）与抽取用不同模型（默认 DashScope 通义千问，`EVAL_JUDGE_MODEL` 覆盖，只用国内服务），只输出「一致 / 不一致 / 无法判断」+ 理由；逐字相同、一边没有这句由代码直接判，不问模型；调用录在案件的 `cache/judge/`，严格回放未命中或调用出错记「未裁判」，不影响其他字段。结论在报告「LLM 裁判」一节单列，校准前（第 3 步）不计入准确率
- 链路追踪（Langfuse，自托管，`--langfuse`，`case_eval/tracing.py`）：每个案件一条 trace，每次 LLM / OCR / 裁判调用（含命中缓存的回放，`metadata.cached`）各一条记录，逐字段结论与准确率写成 score。挂在 `ReplayCache` 的三个包装上，不开 `--langfuse` 时是空实现、不连网；线上系统不导入。trace 含案卷内容：只许发自托管实例，发往 Langfuse Cloud 默认拒绝（`--langfuse-allow-cloud` 只用于虚构案卷）；OCR 只记页面图片哈希。部署见 `deploy/langfuse/README.md`
- 裁判校准（#90 第 3 步，`scripts/judge_calibrate.py` + `case_eval/calibration.py`）：一组「律师句 / 系统句」配对（真实配对 + 在真实句子上改一处造的反例 / 换说法的正例），先 `sheet` 生成标注表（打乱顺序、不含裁判结论，免得先入为主）由人填一致 / 不一致 / 无法判断，再 `run` 跑裁判（录制在 `--cache`）并对照人工结论出报告：一致率、Cohen's kappa（扣掉碰巧对上的部分）、混淆矩阵，重点看「人工不一致、裁判一致」（放过实质差异）。代码直接判的配对单独统计，不算模型水平。真实配对、标注表和报告只放服务器 / 会话临时目录
- OCR / LLM 返回按请求内容哈希录制到案件的 `cache/`：改规则后 `--strict` 只回放、零费用；换模型或改提示词后 `--mode live` 重建
- 每次运行写 `_runs/<时间>/report.md`，并与上次逐字段对比；落实律师答复、改抽取规则后跑一遍，看有没有改坏别的案件
- 真实案卷与标准字段放服务器 `/srv/eval-cases`（见 `deploy/README.md`「案件 eval」），不进仓库；新案卷核对完（`case-review`）就补一份 `expected.json` 加进去
- 错误 / 缺失的字段另做归因（#108，`case_eval/attribution.py`）：在扫描件 OCR 文本里找标准值，分抽取错误 / 其他页有标准值 / 疑似 OCR 错误 / 可能未读到 / 非 OCR 字段 / 规则推导，报告分类计数，用来判断该投入改 OCR 还是改抽取
- 两套案卷各有用途：**虚构案卷集** `backend/eval_fixtures/`（进仓库、当事人金额地址全虚构，缓存已录制）在 CI 的 eval job 里严格回放，与 `baseline.json` 比较，任一字段变差（正确 → 错误 / 缺失、缺失 → 错误、来源变错、新静默错误）或缓存未命中即失败（`case_eval/baseline.py`，`--baseline` / `--update-baseline`）；LLM 裁判结论也存进基线，变化与裁判缓存未命中只提示（报告「LLM 裁判提示」，CI 里是黄色 warning），不算失败；**真实案卷集**放服务器、手动跑，看真实材料上的准确率。报告单列「错误字段」（看起来正常的错值，比缺失危险）
- 新规则落地时，先往虚构案卷集补一个能体现该规则的案件（`eval_fixtures/_build/build_fixtures.py` 生成材料、写 `expected.json`、`--mode live` 录缓存、确认后 `--update-baseline`），见 `backend/eval_fixtures/README.md`

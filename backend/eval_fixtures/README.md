# 虚构案卷集（eval fixtures）

CI 里跑的案件 eval 案卷集（#82）。**所有当事人、金额、地址、编号都是虚构的**，可以进仓库；真实案卷仍只放服务器 `/srv/eval-cases`，不进仓库。

| 案件 | 覆盖的业务路径 |
|---|---|
| 虚构安装案A | 扫描合同按需 OCR 找付款 / 违约 / 争议条款与签章页；质保金触发 `retention` 断点；3 份文字版验收报告取最晚批准日期 |
| 虚构买卖案B | 买卖合同争议条款转引《战略合作协议》，协议约定仲裁 → 仲裁申请书；合同未约定甲方逾期付款违约金 → 逾期付款利息（LPR） |
| 虚构安装案C | 扫描合同的设备清单 4 台含 1 台 VGE 家用电梯、验收报告 3 份 → 不暂停；原告所在地法院（按原告信息表住址推断） |
| 虚构安装案D | 一次性违约金（合同总价 5%）；合同签订地管辖、县级市法院 |

## 目录

```
eval_fixtures/
  branch_info.json        虚构的原告信息表（eval_cases.py 发现案卷根目录有它就用它，不读线上那份）
  baseline.json           基线：每个案件每个字段的结论（只有结论，没有取值）
  _build/build_fixtures.py  生成各案件 materials/ 下的 PDF
  <案件>/materials/       上传给系统的材料（扫描件是渲染成图片的 PDF，没有文字层）
  <案件>/expected.json    标准字段（写法见 scripts/eval_cases.py 文件头）
  <案件>/cache/           录制的 OCR / LLM 返回
```

## 常用命令（在 backend/ 下）

```bash
# CI 同款：严格回放 + 基线门槛（不联网、不需要密钥，几秒钟）
python scripts/eval_cases.py eval_fixtures --strict --baseline eval_fixtures/baseline.json

# 改了提示词 / 换了模型 / 改了材料 → 重录某个案件的缓存（需要 DEEPSEEK_API_KEY、DASHSCOPE_API_KEY）
rm -rf eval_fixtures/虚构安装案A/cache
python scripts/eval_cases.py eval_fixtures --case 虚构安装案A --mode live

# 结论变好了（或新增了案件），确认无误后更新基线
python scripts/eval_cases.py eval_fixtures --strict --baseline eval_fixtures/baseline.json --update-baseline
```

门槛规则（`scripts/case_eval/baseline.py`）：任一字段比基线变差即失败（正确 → 错误 / 缺失，缺失 → 错误，来源由对变错，新出现静默错误）；案件运行出错、严格回放缓存未命中、基线里没有的案件也失败。变好不失败，只提示更新基线。

## 缓存怎么来的

`cache/` 用 `--mode live` 对上面这些虚构材料录制：LLM 为 `deepseek-flash`，OCR 为 `qwen3.8-flash`（DashScope），2026-10-25 录制。缓存按请求内容哈希命名：提示词、材料文字或扫描页图片任何一处变了，对应请求就会未命中，CI 会提示重录。重录后要核对报告里的结论，并在 PR 里说明为什么要重录。

## 加新案件

新规则落地时，先在 `_build/build_fixtures.py` 里补一个能体现该规则的虚构案件（或在已有案件上改材料），写好 `expected.json`（标准答案按律师规则写，不是照抄系统输出），录制缓存，确认结论后 `--update-baseline`。

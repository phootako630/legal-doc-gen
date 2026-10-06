---
name: lawyer-questionnaire
description: 把需要律师确认的问题（诉状写法、字段取值规则、原告信息表核对等）生成为 Word 确认单（.docx），律师在每题回复框里填字母或写说明后发回；也用于读取律师填好的回复。用户说「做成问卷 / 确认单」「问问律师」时使用。
---

# 律师确认单

律师在中国大陆，不看 JSON、不看英文，部分网页打不开，所以确认单一律做成 **Word 文件**。已发过的有：《起诉状规则确认单》（19 题）、《补充确认单》（8 题）、《确认单（三）》（16 题），格式统一。

## 步骤

以下 `$SKILL` 指本目录 `.claude/skills/lawyer-questionnaire`，`$SCRATCH` 指 scratchpad。

1. **写题目 JSON**（放 scratchpad），结构照 `$SKILL/scripts/example.json`：
   - `meta`：`title`（如「起诉状规则确认单（四）」）、`subtitle`、`intro`（说明用哪个案件核对的、共几题，需要样本也在这里提）。
   - `GROUPS`：分组，`id` / `title`（标题里写题数）/ `note`（可选）。
   - `Q`：题目，`n` 题号连续、`g` 所属分组、`title` 问句、`ctx` 背景要点（列表）、`opts` 选项（可省略，省略时回复框是「做法：」）、`first: true` 标「建议先答」。

2. **生成 Word**（首次需装 docx 包，装在 scratchpad，不进仓库）：
   ```bash
   npm install --prefix "$SCRATCH/qdeps" docx@9 --silent --no-audit --no-fund
   NODE_PATH="$SCRATCH/qdeps/node_modules" node $SKILL/scripts/make-docx.js "$SCRATCH/q.json" "$SCRATCH/起诉状规则确认单（四）.docx"
   ```

3. **检查排版**：转 PDF 再渲染成图片，用 Read 看首页和末页：
   ```bash
   export LC_ALL=C.UTF-8
   cd "$SCRATCH" && soffice --headless --convert-to pdf "起诉状规则确认单（四）.docx" >/dev/null
   python3 -I -c "import fitz,sys; d=fitz.open(sys.argv[1]); [d[i].get_pixmap(matrix=fitz.Matrix(1.2,1.2)).save(f'q-p{i+1}.png') for i in (0, len(d)-1)]" "起诉状规则确认单（四）.pdf"
   ```
   没有 `soffice` 时：`apt-get install -y -q libreoffice-writer`。

4. **交付**：用 SendUserFile（`display: attach`）发给用户，回复里按分组列出题目标题，并标出「建议先答」的题。

## 写题原则

- **一题只问一件事**，问句写成律师能直接选的形式；选项最后一个通常是「其他（请说明）」。
- **背景写具体**：引用本案的条款号、金额、诉状原句，让律师不翻材料也能答。只写必要信息，不写当事人电话、身份证号等。
- **只问律师能定的**：代码能确定的（校验位、勾稽）直接给结论，不当成问题问；需要用户自己定的（如是否接企查查 API）单独告诉用户，不放进确认单。
- **标「建议先答」**：影响大多数案件、阻塞开发的题（通常 3–5 题）。
- **题量**：一份控制在 20 题以内；上一份的遗留问题放进新一份，不另发零散问题。

## 读取律师回复

律师发回填好的 .docx 后：

```bash
export LC_ALL=C.UTF-8
soffice --headless --convert-to 'txt:Text (encoded):UTF8' --outdir "$SCRATCH/reply" <回复.docx>
```

逐题整理成「题号 → 选择 → 说明」，再落实：
- 规则写进 `backend/app/services/derived_fields.py` 等代码；
- 同步 `CLAUDE.md`「律师确认的填写规则」；
- 补测试。

答得不清楚的追问，放进下一份确认单。

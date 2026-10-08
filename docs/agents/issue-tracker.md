# Issue tracker：GitHub

本项目的需求规格与任务记录在 `phootako630/legal-doc-gen` 的 GitHub Issues，使用 `gh` CLI。标题、正文和讨论默认简体中文；技术标识保留原文。公开内容使用合成或脱敏信息，真实案件材料保留在本地。

## 常用操作

命令显式指定 `--repo phootako630/legal-doc-gen`。

- 创建：`gh issue create --repo phootako630/legal-doc-gen --title "标题" --body-file <正文文件>`。多行正文写入 UTF-8 文件，再传给 CLI。
- 读取：`gh issue view <编号> --repo phootako630/legal-doc-gen --json number,title,body,labels,comments`。
- 列表：`gh issue list --repo phootako630/legal-doc-gen --state open --json number,title,body,labels,comments`；按需增加 `--label` 或调整 `--state`。
- 评论：`gh issue comment <编号> --repo phootako630/legal-doc-gen --body-file <正文文件>`。
- 标签：`gh issue edit <编号> --repo phootako630/legal-doc-gen --add-label <标签>` / `--remove-label <标签>`；角色映射见 `docs/agents/triage-labels.md`。
- 关闭：`gh issue close <编号> --repo phootako630/legal-doc-gen`；需要解释时先添加评论。
- 子任务：`gh issue create --repo phootako630/legal-doc-gen --parent <父编号> ...`，或 `gh issue edit <父编号> --repo phootako630/legal-doc-gen --add-sub-issue <子编号>`。不可用时在子任务正文顶部写 `Part of #<父编号>`，父任务用任务列表关联。

## Skill 中的 tracker 操作

- “发布到 issue tracker”：创建 GitHub issue。
- “读取相关 ticket”：按编号读取 GitHub issue。
- 关闭工作：采用项目对应的 issue / PR 工作流；创建 PR 时将其附到当前 Codex chat。

## Pull requests as a triage surface

**PRs as a request surface: no.** `/triage` 默认处理 issues。后续需要把外部 PR 纳入请求队列时，可将此值改为 `yes`，并使用 `gh pr view`、`gh pr diff`、`gh pr edit` 等对应命令。

GitHub issues 与 PR 共用编号空间；引用编号有歧义时先用 `gh pr view` 判断，再读取 issue。

## Wayfinding operations

- Map：单个 issue，标签 `wayfinder:map`，正文记录 Notes / Decisions-so-far / Fog。
- Child ticket：map 的子 issue，标签 `wayfinder:<type>`，type 为 `research` / `prototype` / `grilling` / `task`。执行 wayfinder 前检查并创建缺失的 wayfinder 标签。
- Blocking：优先使用 GitHub 原生 issue dependencies。读取 blocker 的数据库 id：`gh api repos/phootako630/legal-doc-gen/issues/<编号> --jq .id`；创建依赖：`gh api --method POST repos/phootako630/legal-doc-gen/issues/<child>/dependencies/blocked_by -F issue_id=<blocker数据库id>`。此 id 是数据库 id，不是 issue 编号或 node_id。不可用时在正文顶部写 `Blocked by: #<编号>, #<编号>`。
- Frontier：按 map 顺序检查开放子任务，排除仍有开放 blocker 或已有 assignee 的任务；原生依赖的开放 blocker 数见 `issue_dependencies_summary.blocked_by`，备用正文引用则逐项检查。
- Claim：`gh issue edit <编号> --repo phootako630/legal-doc-gen --add-assignee @me`。
- Resolve：评论记录结论后关闭 ticket，再把结论摘要和链接追加到 map 的 Decisions-so-far。

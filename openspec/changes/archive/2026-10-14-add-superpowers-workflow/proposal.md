# Proposal

## Why

项目已经用 OpenSpec 管住了"改什么、改成什么样"，但**没有任何东西约束"怎么改"**：测试可以先不写、bug 可以靠猜着改、声称"做完了"不需要给证据。审计记录里"全仓库 0 个测试、无 lint/CI"就是这种状态的直接后果。

上游 [obra/superpowers](https://github.com/obra/superpowers)（MIT，v7.0.0，commit `bb92a77`）是一套把编码代理约束成软件工程流程的技能库：头脑风暴 → 写计划 → 测试先行 → 系统化调试 → 代码评审 → 完成前验证 → 分支收尾。引入它能把"怎么做"这半边补上。

一个必须说清的前提：superpowers 不只是一堆 Markdown 技能，它是完整插件（还有 `hooks/`、`scripts/`、`index.js`，并给 Claude Code / Codex / Cursor / OpenCode 等各做了适配器）。**它的"强制力"主要来自 `hooks/`**。上游只做了一个 `SessionStart` hook（把 `using-superpowers` 注入上下文），也就是"让代理知道自己该先查技能"；至于"查不查、守不守规格门禁"，上游并不拦。

因此本次在第一版只 vendoring `skills/` 的基础上**追加引入强制力**：

1. 原样收录上游 `hooks/`（`.dsh/hooks/upstream/`），使来源可追溯、给 Claude Code / Cursor 使用者直接用。
2. 用 `.dsh/hooks/project-gate.cjs` 把本项目需要的闸门补成三层，并注册到 CodeBuddy / WorkBuddy 的 `settings.json`：
   - `SessionStart`：注入 `using-superpowers` 全文 + 本项目门禁，会话一开始就没有"忘了先查技能"的空间；
   - `UserPromptSubmit`：**每一轮提问**都重新注入门禁（技能自检、规格门禁、第三方内容只读）；
   - `PreToolUse`：写文件前**真正 deny** 掉对 `.dsh/skills/` 与 `.dsh/hooks/upstream/` 的就地修改——这是"软约束"变成"硬拦截"的关键一步。

这一步把"自觉遵守"补成"绕不过去"，同时不引入插件运行时依赖（见 design 的决策 6）。

另外这次引入需要**推翻一个既有决定**：之前把 `.dsh/` 整体设为 gitignore（当作本机工具配置）。既然技能集要团队共享，就必须把它纳入版本库。

## What Changes

- 新增能力规格 `engineering-workflow`：定义技能集的入库要求与可追溯性、许可证随附、技能文件格式兼容性、与 OpenSpec 的职责分界、升级方式，以及"不得用技能绕过规格门禁"。
- 把上游 superpowers 的 **15 个技能 / 74 个文件** vendoring 进 `.dsh/skills/`（与既有的 6 个 `openspec-*` 技能并列，共 21 个技能目录）。
- 把上游 `hooks/` 子树（4 个文件）原样收录到 `.dsh/hooks/upstream/`，并在来源记录中登记：它给 Claude Code / Cursor 用，在本项目的 DSH 路径上不生效，属于可追溯的存档。
- 新增项目自有的硬拦截实现 `.dsh/hooks/project-gate.cjs`（Node，无第三方依赖）与注册脚本 `.dsh/hooks/install-hooks.cjs`：注册 `SessionStart` / `UserPromptSubmit` / `PreToolUse` 三个 hook，实现"会话注入门禁 + 每轮提醒 + 写第三方内容前 deny"。
- 随技能集入库 **MIT 许可证全文**（`.dsh/LICENSE-superpowers`）与**来源/版本记录**（`.dsh/README.md`：上游仓库、版本 v7.0.0、commit、引入日期、升级步骤、OpenCode 接入方式、两套技能的职责分界）。
- 调整 `.gitignore`：不再整体忽略 `.dsh/`，改为只忽略其中可能出现的运行态目录（`sessions/`、`cache/`、`logs/`）。
- 在 `README.md`、`docs/README.md` 与 `.dsh/README.md` 中说明"OpenSpec 管需求与规格、superpowers 管实现与评审"的分工，以及硬拦截的三层机制、安装/卸载方式与失效排查。
- **BREAKING**：无。不改变任何运行时行为、接口或部署方式；变化只发生在开发工作流与仓库内容上。

## Capabilities

### New Capabilities

- `engineering-workflow`: 代理技能集如何入库与追溯、许可证如何随附、技能文件必须满足的格式、两套技能集的职责分界、升级与验证方式，以及规格门禁不可被绕过。

### Modified Capabilities

无。`repository-hygiene` 与 `documentation-governance` 的既有要求（根目录只放长期资产、忽略规则要锚定、文档要有单一来源）都仍然成立，本次只是让 `.dsh/skills/` 从"被忽略的本机内容"变成"入库的长期资产"，属于该要求的合规实现，不需要修改要求文本。

## Impact

- **新增文件**：`.dsh/skills/` 下 15 个技能目录（74 个文件）、`.dsh/hooks/upstream/`（4 个上游 hook 文件）、`.dsh/hooks/project-gate.cjs`、`.dsh/hooks/install-hooks.cjs`、`.dsh/LICENSE-superpowers`、`.dsh/README.md`。
- **修改文件**：`.gitignore`（`.dsh/` 不再整体忽略）、`README.md`、`docs/README.md`、`openspec/changes/add-superpowers-workflow/`。
- **仓库外改动**：执行一次 `node .dsh/hooks/install-hooks.cjs` 会把 hook 注册进用户级 `~/.workbuddy/settings.json`（含备份，可 `--uninstall` 回滚）；该文件不属于仓库，也不含本项目密钥。
- **不受影响**：`app.py` 及所有 Python 代码、`web/`、`openspec/` 的规格内容、依赖清单、部署方式。技能集与 hook 是开发期资产，不进入运行时。
- **用户可见变化**：克隆仓库后即获得同一套代理工作流；注册 hook 并重启宿主后，会话开始与每轮提问都会收到门禁提醒，对 `.dsh/skills/` 与 `.dsh/hooks/upstream/` 的写入会被直接拒绝。
- **依赖新增**：无。hook 用 Node 标准库实现，不引入 npm 依赖（Node 在开发机上已因前端 vendoring 而具备）；不引入 Python 依赖。

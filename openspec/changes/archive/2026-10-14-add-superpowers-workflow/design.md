# Design

## Context

动机见 `proposal.md` —— Why。这里只记录方案取舍所需的事实。

### 实测得到的上游事实

| 项 | 值 |
|---|---|
| 正式仓库 | `obra/superpowers`（描述：an agentic skills framework & software development methodology） |
| 版本 | v7.0.0，commit `bb92a77741419a4ab5f06e711a283343f1ada0c3`（2026-10-10） |
| 许可证 | MIT |
| 规模 | 29.8 万 stars、2.6 万 forks，仍在维护 |

- 曾搜到的 `obra/superpowers-skills` **已归档**（`archived: true`，最后推送 2025-10-14），是旧的社区技能库；它的技能目录是**两级**布局（`skills/<分类>/<技能>/SKILL.md`）。正式仓库是一级扁平布局，正好匹配本项目 `.dsh/skills/<技能>/SKILL.md`。**因此必须用正式仓库**。
- 正式仓库除 `skills/` 外还包含 `hooks/`、`scripts/`、`index.js`、`package.json`、`tests/` 以及给 Claude Code / Codex / Cursor / Devin / OpenCode 等十来个代理的适配目录。**"强制力"来自 hooks**。
- 上游 `SKILL.md` 的 frontmatter 是 `name` + `description`，与本项目既有的 `openspec-*` 技能格式一致 —— 已实测：安装后 DSH 的技能目录**立即重载**并出现全部 15 个技能，无需适配。

### 本机网络约束（决定了升级命令怎么写）

- `github.com` 可直连（curl 200，约 2.7 s）。
- `raw.githubusercontent.com` 在本机**只解析到 IPv6**（`2606:50c0:8000::154`），无路由，直连失败；`git clone` 亦因 DNS 解析失败而不可用；`codeload.github.com` 直连超时。
- 可用镜像：`gh-proxy.com`（tarball 实测成功，680 KB）、`ghfast.top`、`ghproxy.net`、jsDelivr 的 `gh` 通道。其中 tarball 只有 `gh-proxy.com` 成功。

因此升级步骤写的是 **经镜像取 tarball 再解压覆盖**，而不是 `git clone`。

## Goals / Non-Goals

**Goals:**

- 让"怎么改代码"这半边也有成文且可追溯的约束，并且随仓库分发。
- 记录清楚来源、版本、许可证与引入范围，使升级可复现。
- 明确 OpenSpec 与 superpowers 的职责分界，避免两套流程互相打架或互相漏掉。

**Non-Goals:**

- **不引入上游的插件运行时**（`scripts/`、`index.js`、`package.json` 与 Claude Code / Codex / Cursor / Devin / OpenCode 等适配目录）。原因：本项目的主代理是 DSH，它按 `.dsh/skills/<名>/SKILL.md` 加载技能，不消费 Claude Code / OpenCode 的插件协议；把插件运行时搬进来既不能生效，又会引入 Node.js 构建与依赖。
- **不把上游 `hooks/` 当作本项目的强制力来源**（见决策 6）。上游只实现了 `SessionStart` 一个事件，且其 `run-hook.cmd` 依赖标准位置安装的 Git for Windows（本机的 bash 在 `~/.workbuddy/binaries/PortableGit/` 下，不在它探测的四个位置里）。`hooks/` 原样收录到 `.dsh/hooks/upstream/` 只为**可追溯**与给 Claude Code / Cursor 使用者取用；本项目实际生效的闸门由自有的 `project-gate.cjs` 提供。
- 不改写任何第三方技能内容。项目自有约定一律写在项目文档里。
- 不为 superpowers 建 CI 或强制校验脚本（本项目目前没有 CI，那属于 `establish-engineering-baseline` 的范畴）。
- 不修改 `opencode.json`，也不把它纳入版本库——该文件含有真实密钥。OpenCode 的完整能力接入方式只在文档中说明。

## Decisions

### 决策 1：用正式仓库 `obra/superpowers`，不用已归档的 `superpowers-skills`

两者都叫 superpowers，但前者在维护（v7.0.0、仍在推送）、布局是一级扁平、且带多代理适配；后者已归档且是两级布局。选前者还有一个直接好处：**布局与 DSH 期望的完全一致，零适配**。

### 决策 2：只 vendoring `skills/` 子树

取舍已在规格中要求写进来源记录：

- **获得**：15 个技能的完整方法论与纪律（`SKILL.md` 及其 `references/`、`prompts/`、`scripts/`、`templates/` 等随附文件，共 74 个文件）。
- **失去**：`hooks/` 带来的自动强制（例如在特定时机强制触发某技能）。因此对 DSH 而言这是"自觉遵守的纪律"而非"拦得住的闸门"。**该缺口由决策 6 补齐**：不搬上游 bash hook，而是按宿主真实支持的 hook 契约自建三层闸门。
- **补救**：OpenCode 使用者可在 `opencode.json` 里加官方插件规范拿到完整能力。文档中已给出命令，并提示该文件含密钥、**不要**为了共享而把它连同密钥提交。

选这条而不是"把整个插件搬进来"，是因为 DSH 不消费插件协议；搬进来不生效反而会让仓库背上 Node.js 工具链。

### 决策 3：按一级扁平布局放进 `.dsh/skills/`，与 `openspec-*` 并列

既有 6 个 `openspec-*` 技能就是 `.dsh/skills/<名>/SKILL.md`，上游也是一级扁平，直接复制即可。已实测 DSH 能同时识别两套（21 个技能目录）。

### 决策 4：`.dsh/` 从"整体忽略"改为"忽略运行态、放行技能"

之前把 `.dsh/` 整体忽略是按"本机工具配置"处理的；现在技能集要共享，那个判断不再成立。改为只忽略 `sessions/`、`cache/`、`logs/` 这类运行态目录，并保留一句注释说明：**日后若出现别的运行态内容，按具体路径补规则**，而不是重新整体忽略（否则会再次把技能集挡在版本库外）。

### 决策 5：把分界写成规格，而不是只写在 README 里

"不得用实现类技能绕过规格门禁"是一条会被反复触碰的约束。只写在 README 里容易被忽略，写进能力规格后就有明确归属，且违反时可对照 `documentation-governance` 判定。

### 决策 6：硬拦截用自有的 Node hook 重建，而不是搬上游的 bash hook

**问题**：决策 2 的取舍是"只有纪律、没有闸门"。用户明确要求闸门（这正是 superpowers 的特性之一）。

**事实**（实测，见 `docs` 与本机环境）：

| 事实 | 影响 |
|---|---|
| CodeBuddy / WorkBuddy 的 hooks 配置在 `settings.json` 的 `hooks` 字段，事件名与 Claude Code 同构 | 可以注册 |
| `SessionStart` / `UserPromptSubmit` 的 stdout 或 `hookSpecificOutput.additionalContext` 会被注入上下文 | 可以注入门禁 |
| `PreToolUse` 支持 `permissionDecision: "deny"` | **可以真正阻断工具调用** |
| Windows 上 hook 强制用 Git Bash 执行；本机 bash 位于 `~/.workbuddy/binaries/PortableGit/`，不在上游 `run-hook.cmd` 探测的四个位置 | 上游 bash hook 在本机**不生效** |
| 上游只注册了 `SessionStart` | 上游方案即使跑起来也管不住"写第三方文件" |

**选择**：用 Node 写一个 hook 脚本（`project-gate.cjs`），由安装脚本把宿主绝对路径的 `node` 写进配置。理由：

- Node 在本机与项目里都已具备（`web/` 前端与构建链需要它），不新增工具链；
- 跨平台、无 PATH 依赖（上游 bash 方案最大的坑就是这个）、JSON 解析不需要 `jq`；
- 一个脚本按 `hook_event_name` 分派三个事件，比三份 bash 脚本更好维护。

**三层闸门的设计**：

| 事件 | 行为 | 强度 |
|---|---|---|
| `SessionStart` | 注入 `using-superpowers` 全文 + 项目门禁（三条硬规则） | 强（每次会话必到） |
| `UserPromptSubmit` | 注入精简门禁提醒（技能自检 / 规格门禁 / 第三方只读） | 强（每轮必到） |
| `PreToolUse` | 命中 `.dsh/skills/`、`.dsh/hooks/upstream/`、`LICENSE-superpowers` 的 Write/Edit 类调用 → `permissionDecision: "deny"` | **硬（工具真的执行不了）** |

**失败姿态**：一律 fail-open——hook 解析失败、Node 缺失、脚本异常都不阻断用户操作，只是不注入、不拦截。闸门是为了减少犯错，不是为了在基础环境异常时把人锁死。

**范围收敛**：脚本内置"只在本仓库生效"的判断（比对 `cwd` / `CODEBUDDY_PROJECT_DIR` 与脚本所在仓库），因此可以安全地注册到**用户级**配置，不会污染其它项目。

### 决策 7：注册走脚本，且必须幂等、可卸载、带备份

`settings.json` 是用户的全局配置，手工编辑容易出错也无法回滚。因此提供 `install-hooks.cjs`：

- 注册前先移除本 hook 的旧条目（重复执行不会累积重复注入）；
- 写回前把原文件备份为 `settings.json.bak-<时间戳>`；
- `--uninstall` 精确移除本 hook 条目、不动其它配置；
- `--project` 可改为写项目级配置（`.workbuddy/settings.json`），但**不要与用户级同时装**，否则同一事件会并行跑两次、注入两份重复文本。

配置文件是宿主启动时快照的，**改完必须重启宿主才生效**——这一点必须在文档里写明，否则会被误判为"hook 没生效"。

## Risks / Trade-offs

- **[只有纪律没有强制]** → 已按决策 6 补齐三层闸门；来源记录必须写清"上游 `hooks/` 是存档、本项目闸门由 `project-gate.cjs` 提供"，不让人把两者混为一谈。
- **[拿不到强制力]** → 已解决：`project-gate.cjs` 把 `SessionStart` / `UserPromptSubmit` / `PreToolUse` 三个事件都接上，其中 `PreToolUse` 是真正的 deny（见决策 6）。需要完整上游能力的 Claude Code / Cursor 使用者仍可另装上游插件。
- **[hook 注册后不生效，被误判为"没装"]** → 文档明确写出三件前置事实：必须执行安装脚本、必须重启宿主、宿主命令字符串里的 `node` 与脚本路径是安装时的绝对路径（换机器要重跑）。
- **[每轮注入增加上下文开销]** → `UserPromptSubmit` 只注入 4 行精简提醒，完整门禁与技能全文只在 `SessionStart` 注入一次。
- **[用户级注册影响其它项目]** → 脚本内置"只在本仓库生效"判断；文档同时给出 `--project` 与 `--uninstall` 两条路径。
- **[hook 自身出错反而挡住开发]** → 全部路径 fail-open，且 `--uninstall` 一步可摘除。
- **[上游格式变化导致技能不被识别]** → 规格要求升级后校验 frontmatter 的 `name`/`description`，并在结构变化时拒绝该次升级。
- **[第三方内容被就地改写，升级时丢失]** → 规格明确禁止修改 vendored 内容，项目约定必须写在项目自有文档。
- **[仓库体积增加]** → 74 个文件约 400 KB 的纯文本，相对仓库现有内容可忽略。
- **[多人各自升级导致版本漂移]** → 来源记录里写了版本与 commit，且升级要求同步更新该记录并在提交信息里写明范围。

## Migration Plan

1. 复制上游 `skills/` 到 `.dsh/skills/`，随附 MIT 许可证。
2. 写 `.dsh/README.md`：来源、版本、commit、引入范围、升级步骤、OpenCode 接入、两套技能的职责分界。
3. 调整 `.gitignore`，让技能入库、运行态仍被忽略。
4. 在 `README.md` 与 `docs/README.md` 补充工作流分工说明。
5. 复制上游 `hooks/` 到 `.dsh/hooks/upstream/`（存档），写 `project-gate.cjs`（三层闸门）与 `install-hooks.cjs`（幂等注册）。
6. 执行 `node .dsh/hooks/install-hooks.cjs` 注册到用户级配置（写入前自动备份），并重启宿主。
7. 校验：技能数量与名称、每个技能有 `SKILL.md`、frontmatter 可解析、许可证随附、三类 hook 事件的输入输出实测、`git status` 中技能与 hook 文件待提交而运行态未出现。
8. 在 `.dsh/README.md` 补硬拦截章节：三层机制、安装/卸载、生效前提（安装 + 重启）、失效排查。

回滚：`node .dsh/hooks/install-hooks.cjs --uninstall` 摘掉闸门（立即回到"只有纪律"的状态）；删除 `.dsh/skills/` 下非 `openspec-*` 的技能与随附文件、`.dsh/hooks/`，并把 `.gitignore` 的 `.dsh/` 规则改回整体忽略即可。全过程不影响任何运行时行为。

## Open Questions

- 是否要为 superpowers 的"完成前验证"配一个可执行的校验入口（例如一个把 `python -m unittest` 与 `openspec validate --all --strict` 串起来的脚本）？这属于 `establish-engineering-baseline`（已在审计记录中登记）的范畴，本变更不阻塞它。

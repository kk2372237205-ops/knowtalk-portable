# KnowTalk 工程治理门禁（Engineering Gate）

本文档说明 KnowTalk 项目如何用 **OpenSpec + superpowers** 管控改动，以及**哪些强制力真实生效、哪些只是存档**——对应 `openspec/specs/engineering-workflow/spec.md` 第 118–234 行的要求。

## 1. 职责分工（写在仓库里、可追溯）
- **OpenSpec（改什么）**：能力规范 `openspec/specs/<cap>/spec.md` + 变更提案 `openspec/changes/<name>/`。任何**改变对外可观察行为**的改动，必须先开 OpenSpec 变更，评审过再写码，完成后 `openspec archive` 合并回主规范。
- **superpowers（怎么改）**：8 项实现纪律——brainstorming → using-git-worktrees/分支隔离 → writing-plans → subagent-driven-development → test-driven-development（测试先行）→ systematic-debugging（求根因）→ code-review → verification-before-completion（有证据才算完成）。技能在 `.dsh/skills/superpowers/skills/*`。

两者分界见 `.dsh/skills/README.md`；技能正文见 `.dsh/skills/openspec/SKILL.md` 与各 superpowers 技能。

## 2. 真实生效的强制力（Effective）
| 机制 | 作用 | 文件 |
|---|---|---|
| **git pre-commit hook** | 每次提交前跑 `openspec validate --all`，spec 或活动变更不合法则**阻断提交** | `scripts/git-hooks/pre-commit`（由 `scripts/setup-governance.sh` 安装到 `.git/hooks/`） |
| **会话内自觉纪律** | Agent 在每次任务开始时加载 `using-superpowers` + OpenSpec 技能，写码前自检门禁 | Agent 运行时 |
| **归档校验** | 变更完成后 `openspec archive` 将 delta 合并进主规范，变更移入 `openspec/changes/archive/` | OpenSpec CLI |

git hook 满足规范「门禁 hook 必须可安装、可卸载、失败时放行」：安装脚本幂等（重复注册只留一份）、写入前备份原 hook、提供精确卸载；hook 自身异常时 `exit 0`（放行），但**校验失败**（spec 真有问题）一律阻断。

## 3. 仅存档、未生效的强制力（Honest limitation）
`engineering-workflow` 规范第 141–169 行描述的「宿主 PreToolUse hook 实时拦截 Write/Bash 对 `.dsh/skills/` 的写入」，**当前宿主 WorkBuddy 不提供该机制**，因此：
- 对 `.dsh/skills/` 的写入**仅由约定保护**：不得就地修改 vendored/上游技能文件；项目自有约定写在 `docs/` 或 `openspec/`（见规范第 102–117 行）。
- 门禁在**提交时刻**（pre-commit）与**Agent 自觉**两层生效，而非每次工具调用实时拦截。
- 对 `.dsh/skills/` 的只读操作（`ls`/`grep`/`cat`/`git status`、备份到仓库外）一律允许。

## 4. 安装 / 卸载（幂等）
```bash
# 安装（Git Bash）
bash scripts/setup-governance.sh
# 或 PowerShell
pwsh scripts/setup-governance.ps1 -Action install

# 卸载
bash scripts/setup-governance.sh uninstall
```
安装前会自动备份已有的 `.git/hooks/pre-commit`；卸载时移除托管 hook 并恢复原备份。

## 5. 如何获得完整上游强制力
若需规范描述的「实时 PreToolUse 拦截」，请在支持 PreToolUse hook 的宿主（如 Claude Code + superpowers 原生 hooks）中运行，并按其文档安装；注意那些宿主配置文件可能含本地路径/密钥，提交前需审查，勿连带入库。

## 6. 当前已归档 / 进行中的 OpenSpec 变更
- 已归档：`2026-10-31-add-kiosk-holographic-cabin`（全息舱五项能力 + 四次修复回顾性补规，含 5 条刻意留空的 P1 缺口：商品库 / 关键词问答库 / 设备下发 / 阵列麦+AEC / 自启看门狗）。
- 进行中：无（新任务须先 `openspec change` 建提案）。

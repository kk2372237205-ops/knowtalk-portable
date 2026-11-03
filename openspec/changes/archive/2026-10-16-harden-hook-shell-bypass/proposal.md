# Proposal

## Why

硬拦截已经落地并通过验收（变更 `2026-10-14-add-superpowers-workflow`），但它只覆盖**写文件类工具**（`Write` / `Edit` / `MultiEdit` / `NotebookEdit`）。重启后的真机验收实测到一个缺口：

```
Bash: rm -rf .dsh/skills   → 放行
```

也就是说，shell 与脚本仍能绕过闸门改写上游 vendored 内容。`engineering-workflow` 里"上游技能内容不得被就地修改"这条要求，目前**只在文件工具路径上成立**。

这不是理论风险。代理用 `sed -i`、`tee`、输出重定向或一段内联 Python 完成同一件事，比走 `Edit` 更省事，而且完全不会触发检查。于是只有两种选择：要么补齐覆盖，要么把"第三方内容只读"降格为"尽量遵守"。本项目选择补齐。

## What Changes

- 扩展 `PreToolUse` 的 matcher：从写文件类工具扩展到包含 shell 命令类工具（`Bash`、`PowerShell`）。
- `.dsh/hooks/project-gate.cjs` 新增命令串判定，**仅当命令以受保护路径为写入目标时**返回 deny：
  - 覆盖的写入形态：删除/移动/覆盖受保护路径、原位置编辑（`sed -i`、`perl -i`）、`tee`、`dd of=`、输出重定向、解释器内联脚本（`bash -c` / `python -c`）里对受保护路径的写调用。
  - 读操作（`ls`、`grep`、`cat`、只读脚本）一律放行——闸门要拦的是改写，不是查看。
  - `cp`/`rsync` 只在**目标**落在受保护路径时拦；把上游内容备份到仓库外的操作放行。
  - 明确不覆盖：命令串里不可见的间接写入（执行一个脚本，脚本内部再写文件）。规格如实写明这条边界，不承诺"绝对改不动"。
- 新增 `tests/js/hook-gate-shell.test.js`：以真实 stdin JSON 调用 hook 脚本，逐条断言 deny / 放行 / fail-open。
- `install-hooks.cjs` 的 matcher 同步更新，并重跑安装使配置生效（**需重启宿主**）。
- 文档同步：`.dsh/README.md` 与 `README.md` 写明 shell 覆盖范围与已知边界。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `engineering-workflow`：既有的"规格门禁必须以 hook 硬性拦截落地"要求，其"阻断对第三方内容的写入"场景目前只约束写文件类工具。本次将该场景扩展到 shell 命令类调用，并补一条"阻断判定必须精确、不得误伤只读操作"的要求——因为命令串判定是启发式的，必须把"宁可漏报也不误伤"写成可验收的约束。

## Impact

- **修改文件**：`.dsh/hooks/project-gate.cjs`、`.dsh/hooks/install-hooks.cjs`、`.dsh/README.md`、`README.md`、`openspec/` 下本变更目录。
- **新增文件**：`tests/js/hook-gate-shell.test.js`。
- **仓库外改动**：重跑 `node .dsh/hooks/install-hooks.cjs` 会更新用户级 `~/.workbuddy/settings.json` 中本项目的 `PreToolUse` matcher（写前备份，可 `--uninstall` 回滚）。该文件不属于仓库，也不含本项目密钥。
- **不受影响**：`app.py` 与全部 Python 代码、`web/`、RAG 服务、依赖清单、部署方式。闸门只在开发期生效，不进入运行时。
- **用户可见变化**：重启宿主后，用 shell 删除或改写 `.dsh/skills/`、`.dsh/hooks/upstream/` 内容的调用会被拒绝并给出原因；`ls`、`grep`、`cat` 等只读调用不受影响。
- **风险**：命令串解析是启发式判断，存在误报可能。缓解方式是把判定收窄到"明确的写命令 **且** 受保护路径同时出现"，对读操作与备份操作显式放行，并保持所有异常路径 fail-open。误报的代价（一次被拒的调用，附原因与替代做法）低于漏报的代价（上游内容被静默改写）。
- **BREAKING**：无。

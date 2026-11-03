# Tasks

## 1. 命令串判定

- [x] 1.1 `project-gate.cjs` 增加 shell 工具识别：`PreToolUse` 的判定从只认写文件类工具扩展为同时认 `Bash`、`PowerShell` 等执行命令类工具（工具名大小写不敏感）。命令串从 `tool_input` 的 `command` / `cmd` / `script` 取值。验证：`tests/js/hook-gate-shell.test.js` 断言工具名为 `Bash` 且命令串含越界写入时返回 `permissionDecision: "deny"`；工具名不在名单内时返回 `{}`
- [x] 1.2 实现 `commandWritesProtected(command)` 判定：先从命令串中找出受保护路径的出现（`.dsh/skills`、`.dsh/hooks/upstream`、`.dsh/LICENSE-superpowers`，两种分隔符、大小写不敏感），再逐个判断该出现是否落在**写入目标**位置。判定的写入形态 MUST 覆盖：受保护路径直接被删除/移动（`rm`、`del`、`Remove-Item`、`mv`、`Move-Item`、`rmdir`）、就地编辑（`sed -i`、`perl -i`）、`tee` 写出、`dd of=`、输出重定向（`>`、`>>`，但 `2>&1` 不算）、以及解释器内联脚本（`bash -c`、`python -c`、`node -e`）内含写调用。验证：`tests/js/hook-gate-shell.test.js` 对每种形态各有用例，断言 deny（**实现期补充**：另覆盖 `git rm`/`git mv` 子命令、`$(…)` 与反引号命令替换、`find -delete`）
- [x] 1.3 判定必须精确：只读命令（`ls`、`cat`、`grep`、`rg`、`head`、`tail`、`find`、`stat`、`wc`、`Get-ChildItem`、`Get-Content`、`Select-String`、`Test-Path`、`git status/log/diff/show`）一律放行；`cp`/`rsync`/`xcopy`/`robocopy` 只在**目标**落在受保护路径时拦，把上游内容备份到仓库外 MUST 放行。验证：用例覆盖「只读放行」「备份到 /tmp 放行」「cp 目标为受保护路径则拦」三类，逐条断言放行或 deny
- [x] 1.4 失败姿态保持 fail-open：命令串缺失、非字符串、为空，或判定过程抛异常时一律放行。验证：用例覆盖 `command` 缺失、`command` 为对象、命令串为空三种输入，断言返回 `{}` 且进程退出码为 0
- [x] 1.5 拒绝原因 MUST 指向"禁止就地修改第三方内容"的规格要求，并给出替代做法（写进 `README.md`/`docs/`/`openspec/`，或把项目自有内容写到新文件）。验证：用例断言 `permissionDecisionReason` 同时含规格要求关键词与替代做法

## 2. 注册与生效

- [x] 2.1 `install-hooks.cjs` 的 `PreToolUse` matcher 从 `Write|Edit|MultiEdit|NotebookEdit` 扩展为同时包含 shell 工具名。验证：`install-hooks.cjs --dry-run` 输出的 matcher 含 `Bash` 与 `PowerShell`
- [x] 2.2 重新执行安装使配置生效，并确认幂等。验证：连续执行两次 `node .dsh/hooks/install-hooks.cjs`，宿主配置中本项目条目仍只有一份；`--dry-run` 不写入任何文件（实测：3 条 hook 条目、matcher 已含 shell 工具、其它顶层键全部保留、JSON 合法）

## 3. 测试与文档

- [x] 3.1 新增 `tests/js/hook-gate-shell.test.js`：以真实 stdin JSON 调用 hook 脚本（`node --test`，与既有 JS 测试同一约定），逐条断言 deny / 放行 / fail-open。验证：`node --test tests/js/hook-gate-shell.test.js` 全绿（实测 63 passed）
- [x] 3.2 文档同步：`.dsh/README.md` 与 `README.md` 写明 shell 覆盖范围与**已知边界**——命令串里不可见的间接写入（脚本内部再写文件）不覆盖，不承诺"绝对改不动"。验证：两份文档中均能找到"已知边界"或等义表述，并与本变更的规格场景一致
- [x] 3.3 全量回归：新增的 JS 用例与既有测试一起跑通。验证：`node --test tests/js/*.test.js` 无失败；`openspec validate harden-hook-shell-bypass --type change --strict` 通过

# Tasks

## 1. 技能集入库与追溯

- [x] 1.1 经镜像获取上游 `obra/superpowers` 的 v7.0.0 源码（`raw.githubusercontent.com` 在本机只解析到 IPv6，`git clone` 因 DNS 失败，实测只有 `gh-proxy.com` 的 tarball 可用），解压后确认版本与 commit。验证：记录 commit 与取证方式，确认解压目录名为 `superpowers-main`
- [x] 1.2 把上游 `skills/` 子树复制进 `.dsh/skills/`，与既有的 6 个 `openspec-*` 技能并列。验证：技能目录总数为 21，其中非 `openspec-*` 的为 15，且每个都含 `SKILL.md`
- [x] 1.3 把上游 MIT 许可证全文随附为 `.dsh/LICENSE-superpowers`。验证：文件以 `MIT License` 开头，内容与上游一致
- [x] 1.4 编写 `.dsh/README.md`：上游仓库、版本 v7.0.0、commit、引入日期、引入范围（只 `skills/`）、未引入的部分及后果、升级步骤、OpenCode 官方插件接入方式与"该文件含密钥不要提交"的提示、两套技能的职责分界。验证：文档中可查到上述每一项，且升级命令按本机网络约束写成镜像取 tarball 的方式

## 2. 忽略规则与仓库卫生

- [x] 2.1 修改 `.gitignore`：不再整体忽略 `.dsh/`，改为只忽略运行态目录（`sessions/`、`cache/`、`logs/`），并写明"日后出现别的运行态内容按具体路径补规则，不要重新整体忽略"。验证：`git check-ignore` 对技能文件不命中、对 `sessions/` 下的路径命中
- [x] 2.2 确认仓库中不出现任何密钥类文件。验证：`git status` 与 `git ls-files` 中不含 `opencode.json`、`.env`、`.env.rag`

## 3. 文档与职责分界

- [x] 3.1 在 `README.md` 增加"开发工作流"一节：OpenSpec 管需求与规格（改什么），superpowers 管实现与评审（怎么改），并给出典型顺序。验证：README 中能查到两套技能的分工与顺序，且不重复描述 `.dsh/README.md` 的升级步骤（只引用）
- [x] 3.2 在 `docs/README.md` 的文档索引中补上 `.dsh/README.md` 作为开发工作流说明的入口。验证：索引中出现该条目，链接检查通过

## 4. 验证

- [x] 4.1 校验技能清单与格式：逐个技能目录检查存在 `SKILL.md`、frontmatter 可解析出 `name` 与 `description`、`name` 等于目录名。验证：输出校验结果，异常数为 0
- [x] 4.2 验证技能集被实际识别：确认会话的可用技能目录中出现全部 15 个 superpowers 技能。验证：记录技能名清单并逐个比对
- [x] 4.3 运行 `openspec validate --all --strict`，确认变更与全部规格通过严格校验。验证：命令退出码为 0
- [x] 4.4 复核仓库状态：`git status` 中新增项全部是技能文件、许可证与文档，无运行态内容、无密钥文件。验证：逐项说明归属

## 5. 硬性拦截（hooks）

- [x] 5.1 把上游 `hooks/` 子树原样收录到 `.dsh/hooks/upstream/`（`hooks.json`、`run-hook.cmd`、`session-start`、`hooks-cursor.json`）。验证：文件数与上游一致、内容逐字节未改
- [x] 5.2 写 `.dsh/hooks/project-gate.cjs`：`SessionStart` 注入 `using-superpowers` 全文 + 项目门禁；`UserPromptSubmit` 每轮注入精简提醒；`PreToolUse` 对 `.dsh/skills/`、`.dsh/hooks/upstream/`、`LICENSE-superpowers` 的写调用返回 `permissionDecision: "deny"`。验证：三类事件逐一喂 JSON 实测，输出符合宿主 hooks 契约，非受保护路径返回空决定
- [x] 5.3 写 `.dsh/hooks/install-hooks.cjs`：幂等注册（先摘旧条目）、写前备份、`--uninstall`、`--dry-run`、`--project`。验证：dry-run 输出的 JSON 结构符合宿主配置要求
- [x] 5.4 注册到用户级 `~/.workbuddy/settings.json`，确认其它已有配置键未被动过。验证：写入前后比对，新增的只有 `hooks` 键；备份文件存在

## 6. 文档与收尾

- [x] 6.1 更新 `.dsh/README.md`：引入范围（skills + hooks 存档）、三层门禁机制、安装/卸载、生效前提（安装 + 重启）、失效排查
- [x] 6.2 更新 `README.md` 的"开发工作流"一节：说明开箱是纪律、闸门需一次性安装，并给出开关命令
- [x] 6.3 更新 `docs/README.md` 文档索引
- [x] 6.4 运行 `openspec validate --all --strict`，确认变更与全部规格通过严格校验
- [x] 6.5 复核仓库状态并归档本变更

## Workflow follow-up

- 用户确认技能清单、门禁行为与文档后归档本变更：`openspec archive add-superpowers-workflow`。
- 归档后确认 `openspec/specs/engineering-workflow/spec.md` 存在且 Purpose 不是 `TBD` 占位。
- 提醒使用者：hook 配置不会热加载，注册后需要重启宿主才生效。

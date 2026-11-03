# engineering-workflow Specification

## Purpose
定义项目代理技能集的入库与追溯要求、许可证随附规则、技能文件格式约束、两套技能集（OpenSpec 与 superpowers）的职责分界，以及规格门禁不得被绕过，使"怎么改代码"和"改什么"一样有据可依。

## Requirements

### Requirement: 共用的代理技能集必须入库并随仓库分发

项目共用的代理技能 MUST 存放在仓库内的 `.dsh/skills/` 下并纳入版本控制，MUST NOT 只存在于某台机器的本机配置中。克隆仓库的人 MUST 无需额外安装步骤即可获得同一套技能。

#### Scenario: 克隆后即可用

- **WHEN** 在全新机器上克隆本仓库并用支持该技能目录的代理打开
- **THEN** `.dsh/skills/` 下的技能 MUST 全部出现在该会话的可用技能列表中
- **AND** MUST NOT 需要额外下载或安装

#### Scenario: 技能目录内的运行态内容不入库

- **WHEN** `.dsh/` 下出现会话、缓存或日志等运行态内容
- **THEN** 这些内容 MUST 被忽略规则排除
- **AND** 只有技能文件、许可证与说明文档进入版本库

### Requirement: 引入的第三方技能集必须可追溯

每个从上游引入的第三方技能集 MUST 在仓库内登记：上游仓库地址、版本或提交标识、引入日期、引入范围（哪些子树被复制）、以及升级步骤。仅凭仓库内容 MUST 能判断当前装的是上游哪个版本。

#### Scenario: 查阅来源记录

- **WHEN** 打开技能的来源说明文档
- **THEN** 其中 MUST 给出版本号与提交标识
- **AND** MUST 说明本次只引入了上游的哪个子树
- **AND** MUST 给出可复现的升级命令

#### Scenario: 升级后的版本记录

- **WHEN** 把技能集升级到上游的新版本
- **THEN** 来源记录中的版本与提交标识 MUST 同步更新
- **AND** 升级范围 MUST 在提交信息中写明

### Requirement: 第三方许可证必须随技能集一同入库

引入的第三方技能集 MUST 以其原始许可证条款随附许可证全文与版权声明，文件 MUST 位于技能集所在目录内且 MUST NOT 被忽略规则排除。

#### Scenario: 检查许可证随附情况

- **WHEN** 检查 `.dsh/` 目录
- **THEN** MUST 存在该技能集上游的许可证全文
- **AND** 其内容 MUST 与上游一致，MUST NOT 被删改

#### Scenario: 许可证与来源的对应

- **WHEN** 来源记录声明某许可证
- **THEN** 随附的许可证全文 MUST 与该声明一致

### Requirement: 技能文件必须满足加载器的格式要求

每个技能 MUST 位于 `<技能集目录>/skills/<技能名>/SKILL.md`，`SKILL.md` MUST 以 YAML frontmatter 开头并包含 `name` 与 `description` 两个字段；`name` MUST 与所在目录名一致。升级或新增技能后 MUST 校验这些条件。

#### Scenario: 校验技能清单

- **WHEN** 升级技能集后逐目录检查
- **THEN** 每个技能目录 MUST 含有 `SKILL.md`
- **AND** 每个 `SKILL.md` MUST 能解析出 `name` 与 `description`
- **AND** `name` 取值 MUST 等于目录名

#### Scenario: 升级改变了 frontmatter 结构

- **WHEN** 上游把 frontmatter 改成加载器不认的结构
- **THEN** 该次升级 MUST 被拒绝并记录原因，MUST NOT 直接覆盖现有技能

### Requirement: 技能集之间有明确且成文的职责分界

项目 MUST 以成文方式规定各技能集的职责：OpenSpec 技能集负责需求、规格、设计与任务清单（即"改什么"），superpowers 技能集负责实现与评审纪律（即"怎么改"）。该分界 MUST 写在仓库内可被发现的文档中，且 MUST NOT 与技能自身的描述冲突。

#### Scenario: 新人查找该走哪套流程

- **WHEN** 贡献者想知道某类改动应该走哪套技能
- **THEN** 在仓库文档中 MUST 能查到 OpenSpec 与 superpowers 的分工说明
- **AND** 说明 MUST 覆盖"要改可观察行为"与"只写实现"两种情形

#### Scenario: 两套技能对同一动作都有说法

- **WHEN** 某项工作同时落在一套技能的"计划"阶段与另一套技能的"任务"阶段
- **THEN** 分界说明 MUST 指出哪一套产出什么，避免同一件事被写两遍

### Requirement: 技能集不得绕过规格门禁

引入实现类技能集 MUST NOT 改变既有的规格门禁：改变对外可观察行为的改动 MUST 仍然先通过 OpenSpec 变更提出并更新受影响的能力规格。技能集只能在已经批准的变更范围内约束实现方式。

#### Scenario: 用实现技能直接改行为

- **WHEN** 有人仅凭实现类技能直接修改了接口行为而未创建 OpenSpec 变更
- **THEN** 该改动 MUST 被视为违反 `documentation-governance` 的要求

#### Scenario: 在已批准的变更内实施

- **WHEN** 某个 OpenSpec 变更已经批准，实施者用测试先行、系统化调试等技能完成编码
- **THEN** 这些技能的使用 MUST NOT 需要额外的规格审批
- **AND** 变更完成后仍 MUST 更新受影响的能力规格

### Requirement: 上游技能内容不得被就地修改

vendoring 进来的第三方技能文件 MUST 保持与上游一致；项目自身的约定 MUST 写在项目自有文档中，MUST NOT 直接改写第三方技能内容。这样升级时才能整体覆盖而不丢信息。

#### Scenario: 想给某个技能加项目特有规则

- **WHEN** 需要为某个技能补充项目专属约定
- **THEN** 该约定 MUST 写入项目自有文档（如 `README.md`、`docs/`、`openspec/`）
- **AND** MUST NOT 修改 `.dsh/skills/` 下的第三方技能文件

#### Scenario: 升级时整体覆盖

- **WHEN** 按来源记录中的步骤升级技能集并整体覆盖技能目录
- **THEN** 项目自有的约定 MUST 不受影响
- **AND** 覆盖后 MUST NOT 丢失任何项目信息

### Requirement: 引入范围与强制力来源必须被说明

来源记录 MUST 明确说明本次从上游引入了哪些子树（技能、hooks、脚本、插件运行时），以及**每部分在本项目里是否真的生效**：哪些只是存档可追溯、哪些由项目自有实现替代。MUST NOT 让使用者误以为"装了技能就等于有闸门"，也 MUST NOT 让强制力的来源无从查证。

#### Scenario: 查阅引入范围

- **WHEN** 查阅来源记录
- **THEN** MUST 逐项列出本次引入的上游子树
- **AND** MUST 说明未引入的上游部分
- **AND** MUST 说明每一部分在本项目中是否被消费、由谁消费

#### Scenario: 区分存档与生效的强制力

- **WHEN** 来源记录描述了项目内的强制机制
- **THEN** MUST 区分"上游 hooks 存档"与"项目自有 hook 实现"
- **AND** MUST 给出生效的强制机制所在的文件路径

#### Scenario: 指明获取完整能力的途径

- **WHEN** 使用者希望获得包含自动强制在内的完整上游能力
- **THEN** 来源记录 MUST 给出一条可执行的替代安装方式
- **AND** MUST 提示该方式涉及的配置文件是否已被忽略、是否会连带提交敏感信息

### Requirement: 规格门禁必须以 hook 硬性拦截落地

项目 MUST 通过宿主支持的 hook 机制实现可执行的门禁，使"先查技能"与"改行为先走 OpenSpec 变更"不只是写在文档里的约定。门禁 MUST 覆盖会话开始与每一次用户提问，并 MUST 在写入受保护的第三方内容之前阻断该工具调用——无论写入经由**写文件类工具**还是**执行 shell 命令的工具**。

#### Scenario: 会话开始注入门禁

- **WHEN** 宿主触发会话开始事件
- **THEN** hook MUST 注入 `using-superpowers` 技能内容与本项目的硬规则
- **AND** 注入内容 MUST 说明"改变对外可观察行为必须先有 OpenSpec 变更"

#### Scenario: 每轮提问重新注入门禁

- **WHEN** 用户提交一次新的提问
- **THEN** hook MUST 注入精简的门禁提醒（技能自检、规格门禁、第三方内容只读）
- **AND** 该提醒 MUST NOT 重复注入会话级的技能全文

#### Scenario: 阻断对第三方内容的写入

- **WHEN** 一次写文件类工具调用以 `.dsh/skills/` 或 `.dsh/hooks/upstream/` 下的路径为目标
- **THEN** hook MUST 返回拒绝决定并附上原因
- **AND** 该工具调用 MUST NOT 被执行
- **AND** 原因 MUST 指向禁止就地修改第三方内容的规格要求

#### Scenario: 阻断经由 shell 命令的写入

- **WHEN** 一次执行 shell 命令的工具调用（如 `Bash`、`PowerShell`）的**命令串**以受保护路径为写入目标，形态包括删除或移动该路径、就地编辑（`sed -i`、`perl -i`）、`tee` 写出、`dd of=`、输出重定向（`>`、`>>`）、以及解释器内联脚本（`bash -c`、`python -c`、`node -e`）中对受保护路径的写调用
- **THEN** hook MUST 返回拒绝决定并附上原因
- **AND** 该工具调用 MUST NOT 被执行
- **AND** 原因 MUST 指向禁止就地修改第三方内容的规格要求

#### Scenario: 不误伤项目自有文件的写入

- **WHEN** 写文件类工具调用的目标不在受保护路径内
- **THEN** hook MUST 放行
- **AND** MUST NOT 产生任何阻断

### Requirement: 门禁 hook 必须可安装、可卸载且失败时放行

门禁 hook 的注册 MUST 通过脚本完成，MUST 幂等（重复注册不产生重复注入）、MUST 在写入宿主配置前备份原文件、MUST 提供精确卸载。hook 自身异常时 MUST 放行而不是阻断用户的正常工作。

#### Scenario: 重复注册

- **WHEN** 对同一宿主重复执行安装
- **THEN** 配置中 MUST 只保留一份本项目的 hook 条目
- **AND** 其它已有 hooks MUST 保持不变

#### Scenario: 卸载

- **WHEN** 执行卸载
- **THEN** 本项目的 hook 条目 MUST 被移除
- **AND** 宿主配置中的其它内容 MUST 保持不变

#### Scenario: hook 执行失败

- **WHEN** hook 脚本遇到解析错误、缺少运行时或内部异常
- **THEN** MUST NOT 阻断用户的工具调用或提问
- **AND** 宿主 MUST 能继续正常工作

### Requirement: 门禁必须限定作用范围

注册到用户级配置的 hook MUST 自行判断当前是否位于本仓库，MUST NOT 对其它项目注入本项目门禁或阻断其文件写入。

#### Scenario: 在其它项目中工作

- **WHEN** 宿主在非本仓库的目录中触发同一 hook
- **THEN** hook MUST 不注入任何本项目内容
- **AND** MUST NOT 拒绝任何工具调用

### Requirement: 阻断判定必须精确且不得误伤只读操作

命令串判定是启发式的，因此 MUST 把"宁可漏报也不误伤"写成可验收的约束：判定 MUST 只在"明确的写命令"与"受保护路径"同时出现时生效，**只读操作 MUST 一律放行**。

#### Scenario: 只读命令放行

- **WHEN** shell 命令串只读取受保护路径，例如 `ls .dsh/skills`、`grep -r x .dsh/skills`、`cat .dsh/skills/x/SKILL.md`、`Get-ChildItem .dsh/skills`、`git status .dsh`
- **THEN** hook MUST 放行该调用
- **AND** MUST NOT 返回拒绝决定

#### Scenario: 把上游内容备份到仓库外

- **WHEN** shell 命令串以受保护路径为**来源**、以仓库外的路径为**目标**，例如 `cp -r .dsh/skills /tmp/upstream-backup`
- **THEN** hook MUST 放行该调用

#### Scenario: 命令串中不可见的间接写入

- **WHEN** shell 命令串只是执行一个脚本，而该脚本内部再去写受保护路径
- **THEN** hook MUST NOT 被要求阻断该调用
- **AND** 这条边界 MUST 在来源记录与 README 中如实写明，MUST NOT 承诺"绝对改不动"

#### Scenario: 判定异常时放行

- **WHEN** 命令串取值不是字符串、为空、或判定过程抛出异常
- **THEN** hook MUST 放行且 MUST NOT 阻断该调用

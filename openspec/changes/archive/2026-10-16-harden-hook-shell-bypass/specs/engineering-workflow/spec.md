# Spec Delta

## MODIFIED Requirements

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

## ADDED Requirements

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

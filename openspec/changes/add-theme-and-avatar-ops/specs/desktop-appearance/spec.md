# Spec Delta

## ADDED Requirements

### Requirement: 界面主题必须可在四套色板之间切换

桌面端 MUST 支持蓝白风、科技风、商务风、米白四套主题，
当前主题 MUST 持久化在 `data/settings/theme.json`，启动时据此套用。
首次启动（无配置文件）MUST 使用蓝白风。

主题相关颜色 MUST 通过语义键取值（背景 / 卡片 / 描边 / 正文 / 强调 / 危险 / 舞台 / 悬停…），
业务代码 MUST NOT 直接写死十六进制色值（视频舞台、全屏黑底、tooltip 这类与主题无关的除外）。

#### Scenario: 切换主题

- **WHEN** 用户在应用设置里选中「科技风」并点「应用并重启界面」
- **THEN** MUST 写入 `theme.json`
- **AND** MUST 重启界面进程使新配色生效
- **AND** MUST NOT 停止主服务（避免重新加载数字人模型）

#### Scenario: 主题文件缺失或损坏

- **GIVEN** `theme.json` 不存在或内容不合法
- **WHEN** 启动桌面端
- **THEN** MUST 回退到蓝白风并正常启动，不得因读主题失败而崩溃

### Requirement: 应用设置必须统一管理对外 API 密钥

右上角「设置」MUST 打开独立的应用设置窗口，包含「API 密钥」「外观主题」「关于」三个分栏。

密钥 MUST 以密文显示，且回写已被掩码的值时 MUST 视为「保持原值」而不是把掩码当新密钥。

#### Scenario: 保存对话模型密钥

- **WHEN** 用户在「API 密钥」里填入 LLM Key、接口地址与模型名并保存
- **THEN** MUST 通过 `/api/settings` 的 `llm` 分组写入
- **AND** 保存结果 MUST 在窗口内给出成功/失败提示

#### Scenario: 测试连通性

- **WHEN** 用户点「测试连通性」
- **THEN** MUST 调用 `/api/settings/llm/test`，并把结果文案回显在按钮旁

### Requirement: 布尔与枚举选项不得使用原生勾选控件

界面中的开关类选项 MUST 使用自绘开关（`ToggleSwitch`），
互斥选项 MUST 使用分段选择器（`SegmentedControl`），MUST NOT 使用
`tk.Checkbutton` / `tk.Radiobutton`。

#### Scenario: 语音引擎三选一

- **WHEN** 用户打开「形象与设置 → 语音与音频」
- **THEN** 在线音色 / XTTS 克隆 / Fish 克隆 MUST 以分段选择器呈现，选中项实底强调色
- **AND** MUST NOT 出现圆形单选点

### Requirement: 参考音色必须以卡片形式给出少量经典选项

参考音色选择器 MUST 只展示经典音色与少量克隆音色，MUST NOT 把系统返回的全部语音
平铺进下拉框。每个音色 MUST 显示中文名与一句特征描述。

用户仍 MUST 能填写自定义复刻音色 ID（`S_` 开头）。

#### Scenario: 选择音色

- **WHEN** 用户点选「云健 · 沉稳解说」卡片
- **THEN** `tts.ref_file` MUST 被置为该音色的语音名（如 `zh-CN-YunjianNeural`）
- **AND** 该卡片 MUST 显示选中态

### Requirement: 形象必须支持重命名与删除

形象库 MUST 提供重命名与删除操作。

重命名 MUST 同步目录名与 `avator_info.json` 中的 `avatar_id`，
且 MUST NOT 允许重名或越界名称。删除 MUST 二次确认，且 MUST NOT 允许删除
`data/avatars` 之外的任何路径。

#### Scenario: 重命名形象

- **WHEN** 用户选中某形象并输入新名称确认
- **THEN** 素材目录 MUST 被改名，`avator_info.json` 的 `avatar_id` MUST 同步
- **AND** 列表 MUST 刷新为新名称

#### Scenario: 删除形象

- **GIVEN** 用户未确认删除
- **WHEN** 点了「删除」但取消确认弹窗
- **THEN** 不得删除任何文件

### Requirement: 动作制作必须有桌面端入口

形象库 MUST 提供「制作新动作」，可选择预设、命名并提交生成作业，
并 MUST 显示作业进度。

未配置 MiniMax 密钥时 MUST 提示去应用设置配置，MUST NOT 直接发起请求。

#### Scenario: 未配置密钥时打开动作制作

- **WHEN** 用户未配置 MiniMax Key 就打开「制作新动作」
- **THEN** 界面 MUST 提示前往「设置 → API 密钥」配置
- **AND** 点「开始生成」MUST NOT 产生费用

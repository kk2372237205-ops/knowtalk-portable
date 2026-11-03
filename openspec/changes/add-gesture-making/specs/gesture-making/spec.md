# Spec Delta

## ADDED Requirements

### Requirement: 动作面板必须提供制作入口与预设

数字人管理的「动作」面板 MUST 提供「制作新动作」入口，包含：预设动作下拉、动作名称输入、提示词输入、时长与分辨率选择、生成按钮。

预设 MUST 为 16 套，按「基础 / 强调与对比 / 互动与收尾」三组展示，每套含标识、中文名、用途说明与完整中文提示词；选择预设 MUST 自动填入名称与提示词，且两者 MUST 都可改。

#### Scenario: 打开制作入口

- **WHEN** 在动作面板点「制作新动作」
- **THEN** 下拉 MUST 列出全部预设（中文名 + 用途说明）
- **AND** 名称与提示词输入框 MUST 可见

#### Scenario: 选预设自动带出名称与提示词

- **WHEN** 选中预设「掌心向上展示」
- **THEN** 名称输入框 MUST 变为「掌心向上展示」
- **AND** 提示词框 MUST 填入该预设的完整提示词，且允许继续编辑

#### Scenario: 自定义提示词

- **WHEN** 使用者清空名称并粘贴自己的提示词后点生成
- **THEN** 系统 MUST 接受自定义提示词，不得强制回填预设内容

### Requirement: 生成必须走形象首帧图生视频

后端 MUST 取该形象 `full_imgs/` 的第一帧作为首帧图，调用 MiniMax 图生视频接口（异步任务 + 轮询），MUST 使用设置里的密钥、基址与模型名。

密钥 MUST 来自设置或环境变量，MUST NOT 出现在源码、前端或日志里。未配置密钥时 MUST 直接报错说明，MUST NOT 发起请求。

#### Scenario: 未配置密钥

- **WHEN** 设置里没有 MiniMax API Key 就点生成
- **THEN** 接口 MUST 返回可读的错误说明
- **AND** MUST NOT 向 MiniMax 发出任何请求

#### Scenario: 正常提交生成

- **WHEN** 已配好密钥并提交一次生成
- **THEN** 后端 MUST 用形象首帧与提示词建出任务，并返回作业标识
- **AND** 作业状态 MUST 可查询

#### Scenario: 上游失败

- **WHEN** MiniMax 返回失败或超时
- **THEN** 作业 MUST 进入失败态并带上游给的原因
- **AND** MUST NOT 留下半套素材目录

### Requirement: 生成结果必须落成标准手势素材

成片 MUST 经 `generate_avatar(img_size=256, pads=[0,10,0,0], pad_scale=0.35)` 产出 `full_imgs/`、`face_imgs/`、`coords.pkl` 三件套，MUST 写入 `gesture.json`，其 `label` MUST 为动作名称。

画面尺寸 MUST 归一化到该形象默认素材的尺寸（偏窄补边、偏宽裁边，人脸框同步平移），使运行时不再触发尺寸归一化。

#### Scenario: 生成完成后出现在列表

- **WHEN** 作业完成
- **THEN** 该形象的动作列表 MUST 出现这个动作（带帧数与预览）
- **AND** `full_imgs/` 首帧尺寸 MUST 与默认素材一致

#### Scenario: 名称写入元数据

- **WHEN** 动作名为「掌心向上展示」
- **THEN** `gesture.json` 的 `label` MUST 是「掌心向上展示」
- **AND** 列表与画布 MUST 显示这个名字而不是目录名

### Requirement: 同一形象同时只允许一个制作作业

同一形象在已有作业进行中时，新的提交 MUST 被拒绝并提示已有作业在跑，MUST NOT 并发出片。

作业信息 MUST 至少包含：阶段（排队/出片/下载/切帧/完成/失败）、阶段说明、动作标识、失败原因。

#### Scenario: 重复点击

- **WHEN** 一个作业还在跑时再次点生成
- **THEN** 系统 MUST 拒绝第二次提交并说明原因
- **AND** 进行中的作业 MUST 不受影响

#### Scenario: 失败后可重试

- **WHEN** 上一个作业失败
- **THEN** 再次提交 MUST 能正常开始新作业

### Requirement: 生成的动作必须能被画布直接选中

画布的手势节点 MUST 列出该形象下全部可用动作（含新生成的），MUST 带循环预览，MUST 不需要重启服务。

#### Scenario: 画布选到新动作

- **WHEN** 新动作生成完成后打开画布并选中该形象
- **THEN** 手势节点的候选里 MUST 出现这个动作
- **AND** 选中后节点 MUST 显示它的循环预览

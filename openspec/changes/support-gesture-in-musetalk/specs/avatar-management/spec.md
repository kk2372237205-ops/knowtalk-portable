# Spec Delta

## ADDED Requirements

### Requirement: MuseTalk 形象播放动作

模型为 `musetalk` 时，讲稿中的 `[GESTURE:<id>]` MUST 在对应段落开始朗读时**真正切换画面**：
该段期间输出的画帧 MUST 来自该手势的 `full_imgs/`，口型 MUST 继续跟随语音更新
（手势帧作为底图参与口型合成，人脸参考来自形象自身——见下一条），
MUST NOT 只在静音补帧时出现。

手势播放一遍后，MUST 自动回到该形象的基础素材继续朗读。

该手势缺少 MuseTalk 侧资产（`musetalk/latents.pt`、`musetalk/mask_coords.pkl`、
`musetalk/mask/` 中至少一张图）时，MUST 按「该手势不可用」处理：该段**照常朗读**、
动作跳过并回落到基础素材，且 MUST 在响应 `data.warnings` 中说明原因，
MUST NOT 因为缺资产拒绝整篇讲稿。

#### Scenario: 有 MuseTalk 资产的动作正常播放

- **WHEN** 模型为 `musetalk`，讲稿某段标注了 `[GESTURE:count]`，且该手势含完整 MuseTalk 资产
- **THEN** 该段开始朗读时画面切换为该手势的帧序列
- **AND** 该段期间口型仍随语音变化（不是静止帧）
- **AND** 该段结束后画面回到基础素材

#### Scenario: 缺 MuseTalk 资产时降级但不打断

- **WHEN** 模型为 `musetalk`，讲稿标注的手势只有 wav2lip 三件套、没有 MuseTalk 资产
- **THEN** 响应 `code` 为 0，该段内容照常朗读
- **AND** 不播放该动作，画面保持在基础素材
- **AND** `data.warnings` 中含该手势不可用的说明

#### Scenario: wav2lip 路径行为不变

- **WHEN** 模型为 `wav2lip`，讲稿标注了具备三件套的手势
- **THEN** 播放行为与改动前完全一致（画面、时序、回落时机均不变）

### Requirement: 动作期间的人脸恒等于形象

动作素材由生成模型产出，即使同一个人、同一构图，脸型也会漂移——实测同一形象下
六套动作的人脸框与形象相差不到 5%，但框内脸型明显偏胖（下颌更宽、两颊更满）。
MuseTalk 输出的人脸形状跟随参考 latent，因此系统 MUST 让**动作期间的人脸完全由形象
自身的资产驱动**：人脸参考 latent、遮罩、遮罩框与人脸框 MUST 一律取自形象基础素材，
长度恒为 1，与动作帧数无关；动作素材只贡献整帧画面（身体与姿态）。

播放时按帧索引取用这套资产 MUST 对任意索引都返回同一份（按长度 1 取模），
使动作全程的脸型不随动作帧变化。

补齐（或重建）动作资产时 MUST NOT 用动作帧自己的脸编码 latent 或生成遮罩。

#### Scenario: 动作期间脸型与形象一致

- **WHEN** 模型为 `musetalk`，某段正在播放动作
- **THEN** 该段输出的每一帧人脸 MUST 由形象的参考 latent 生成
- **AND** 动作素材的帧只作为底图提供身体与姿态
- **AND** 动作开始、进行中、结束时的脸型 MUST 保持一致，MUST NOT 出现胖瘦变化

#### Scenario: 任意帧索引取到同一份人脸资产

- **WHEN** 播放动作时用不同的帧索引（0、1、37、88…）取参考 latent 与遮罩
- **THEN** 四次取用 MUST 得到同一份 latent、同一张遮罩、同一个人脸框

#### Scenario: 资产只有一份且来自形象

- **WHEN** 一个 89 帧的动作补齐了 MuseTalk 资产
- **THEN** `musetalk/latents.pt` MUST 只有 1 项，且与该形象 `latents.pt` 的第 0 项相同
- **AND** `musetalk/mask/` MUST 只有 1 张图，`musetalk/coords.pkl` 与 `musetalk/mask_coords.pkl` MUST 各 1 项
- **AND** 该人脸框 MUST 与形象基础素材的人脸框相同

### Requirement: 手势的 MuseTalk 资产可补齐

系统 MUST 能为已存在的手势补齐 MuseTalk 侧资产（即上一条所述的形象人脸资产副本），
补齐过程 MUST NOT 修改该手势既有的 `full_imgs/`、`face_imgs/`、`coords.pkl` 与 `gesture.json`。

补齐 MUST NOT 对手势逐帧重新做人脸关键点检测，也 MUST NOT 逐帧跑 VAE 编码
（动作素材要求头部零位移，一份人脸资产即可覆盖整段）。

资产目录 MUST 写入 `musetalk/asset.json` 记录其模式为共享（`shared`）；缺少该文件或模式
不为共享的**旧版逐帧资产 MUST 被判定为未就绪**，并由补齐流程自动重建为共享资产。

补齐成功后，手势列举结果的该项 MUST 标明其 MuseTalk 资产已就绪；未补齐时 MUST 标明未就绪。
对已补齐的手势再次执行补齐 MUST 不重复计算（直接返回已就绪）。

#### Scenario: 补齐后动作可用

- **WHEN** 某手势只有 wav2lip 三件套，执行补齐
- **THEN** 该手势目录下出现 `musetalk/latents.pt`、`musetalk/mask_coords.pkl`、`musetalk/mask/` 与 `musetalk/asset.json`
- **AND** 列举该形象手势时，该项标明 MuseTalk 资产已就绪
- **AND** 之后播放该动作时画面确实切换（不再降级）

#### Scenario: 重复补齐不重复计算

- **WHEN** 对已就绪的手势再次执行补齐
- **THEN** 响应立即返回已就绪，MUST NOT 重新编码全部帧

#### Scenario: 旧版逐帧资产自动迁移

- **WHEN** 某手势的 `musetalk/` 下是旧版逐帧资产（没有 `asset.json`）
- **THEN** 该手势 MUST 被判定为未就绪
- **AND** 执行补齐后 MUST 被重建为共享资产（遮罩目录只剩 1 张图）
- **AND** 该手势重新变为就绪

#### Scenario: 补齐不破坏 wav2lip 素材

- **WHEN** 对某手势执行补齐
- **THEN** 该手势的 `full_imgs/`、`face_imgs/`、`coords.pkl`、`gesture.json` 内容 MUST 保持不变

> 动作「按素材原始时长播放」属编排语义，规格增量落在未归档变更 `add-gesture-module` 的
> `gesture-orchestration`（Requirement: 手势按素材原始时长播放），此处不重复声明。


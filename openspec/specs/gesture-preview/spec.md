# gesture-preview Specification

## Purpose
把动作素材合成到多个背景上录成视频，用于肉眼验收抠像效果。抠像参数一动就是几百帧，
单帧 PNG 判断不了「播起来什么样」，必须有连续播放的验收手段；而验收工具若另写一套
合成逻辑，结论就与真机脱节，所以本能力强制复用线上同一条合成路径。

## Requirements

### Requirement: 预览录制必须复用真实合成路径

预览录制 SHALL 走与真机完全相同的合成实现，避免「验收工具和线上不是一套逻辑」
导致验收结论失真。

录制工具 SHALL 通过 `utils.avatar_background.AvatarBackground.compose` 产出每一帧，
SHALL NOT 另写一套仅供预览的合成实现。

#### Scenario: 录出来的画面与真机一致

- **WHEN** 用户录制某动作在某背景下的预览
- **THEN** 每一帧 MUST 由 `AvatarBackground.compose` 产出（同样的 alpha 查表、
  同样的前景去污染、同样的定点混合）
- **AND** 换成任意已配置的背景（纯色或 `data/backgrounds/` 下的图片）时
  MUST 得到与真机播放一致的画面

#### Scenario: 缺抠像素材时明确报错

- **WHEN** 某动作还没有 matte 素材，或背景图路径不存在
- **THEN** 工具 MUST 直接报出是哪些背景没启用，而不是静默录出一段没换背景的视频

### Requirement: 三种录制模式覆盖不同验收角度

抠像问题分三类，对应的看法也不同：单帧横向对比看边缘、全屏看整体观感、
连播看帧间稳定性。工具 SHALL 三种都提供。

#### Scenario: 并排对比同一帧在多个背景下的差异

- **WHEN** 用户不带 `--solo` / `--reel` 运行
- **THEN** 每个动作 MUST 产出一条视频，同一帧并排铺在多个背景上
- **AND** MUST 包含一列未合成的原图作参照（否则看不出「换了背景后有没有变形」）
- **AND** 每格 MUST 带可见的背景名标签（带底衬，浅色底上也读得清）

#### Scenario: 全屏单背景贴近真机观感

- **WHEN** 用户带 `--solo` 运行
- **THEN** MUST 按素材原生尺寸（538×1080）逐背景产出一条视频，不做缩放拼板

#### Scenario: 串烧看循环播放

- **WHEN** 用户带 `--reel` 运行
- **THEN** MUST 把所有动作顺序连播成一条视频，动作切换处不带停顿标记

### Requirement: 产物可直接查看

录制产物 SHALL 做到「不用额外工具就能看」——视频可解码，另有静态中间帧兜底。

#### Scenario: 不开播放器也能扫一眼

- **WHEN** 一条视频录制完成
- **THEN** MUST 同时写出一张同名 `_mid.png`（该动作中间帧的同一块拼板）
- **AND** 输出目录 MUST 为 `data/tmp/preview/`（本就不入库）

#### Scenario: 视频可被标准解码器打开

- **WHEN** 录制结束
- **THEN** 产物 MUST 是能按 24fps 正常解码的 mp4（与动作素材 `gesture.json` 的
  fps 一致），帧数与素材帧数相同

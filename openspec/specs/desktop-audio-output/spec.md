# desktop-audio-output Specification

## Purpose
规范桌面端对 WebRTC 音频的还原与播放行为：按帧元数据（而非数组形状）确定声道数，
避免「画面在动却完全没有声音」；同时提供输出设备选择与一键自检，覆盖真机
多屏/HDMI 场景下系统默认输出指向显示器而非音箱的问题。

## Requirements

### Requirement: 桌面端必须按帧元数据还原 WebRTC 音频

桌面端从 WebRTC 音频轨收到的音频帧 MUST 依据帧自带的元数据
（`layout.channels` 声道数、`format.is_planar` 布局类型）还原成
`(samples, channels)` 形状后交给 sounddevice 播放。

MUST NOT 依据数组形状推断声道数——av 的 `AudioFrame.to_ndarray()` 对
**packed** 格式（`s16`）返回交织的一行 `(1, samples*channels)`，其中
`(1, 1920)` 既可能是"单声道 1920 样本"也可能是"立体声 960 样本交织"，
形状本身不携带声道信息，据此推断会把立体声误当单声道播放。

服务端 `HumanPlayer.push_audio` 推送的是单声道 16kHz 的 `(1, 320)` 帧，
而客户端经 Opus 解码后收到的是 **s16 / stereo / 48kHz packed** 的
`(1, 1920)` 帧——两者形状含义不同，客户端 MUST NOT 沿用服务端的形状假设。

float 格式（`fltp`/`flt`）的样本幅度在 `[-1.0, 1.0]`，MUST 按峰值缩放到
int16 幅度后再播放；MUST NOT 直接 `astype(int16)`（会把 `0.3` 截断成 `0`，
导致静音）。

音频播放过程中的任何异常 MUST NOT 被静默忽略，MUST 记录到日志以便排查
"无声"类问题。

#### Scenario: 立体声 packed 帧

- **WHEN** 桌面端收到形状为 `(1, 1920)` 的 int16 音频帧（`layout.channels == 2`）
- **THEN** 系统 MUST 以 `channels=2`、形状 `(960, 2)` 播放
- **AND** MUST NOT 把 `1920` 当作声道数
- **AND** MUST NOT 把它转置成 `(1920, 1)` 当单声道播放

#### Scenario: 单声道 packed 帧

- **WHEN** 桌面端收到形状为 `(1, 320)` 的 int16 音频帧（`layout.channels == 1`）
- **THEN** 系统 MUST 以 `channels=1`、形状 `(320, 1)` 播放

#### Scenario: planar 帧

- **WHEN** 桌面端收到 planar 格式的帧（`format.is_planar` 为真）
- **THEN** 系统 MUST 把 `(channels, samples)` 转置成 `(samples, channels)` 后播放

#### Scenario: float 幅度不被截断

- **WHEN** 桌面端收到幅度为 `0.3` 的 float 样本
- **THEN** 播放用的 int16 数据 MUST 保留可听的幅度
- **AND** MUST NOT 全部变成 `0`（静音）

#### Scenario: 播放异常可见

- **WHEN** sounddevice 打开设备或写入失败
- **THEN** 异常 MUST 被记录到日志
- **AND** MUST NOT 被静默忽略导致"无声无痕"

### Requirement: 桌面端必须支持选择音频输出设备并自检

桌面端 MUST 支持通过设置指定音频输出设备，留空时使用系统默认设备。
MUST 提供可用的输出设备列表（只含具备输出通道的设备），并提供一键测试播放，
用于在真机上区分"本机扬声器通路有问题"与"数字人没有产生语音"。

音频输出设备 MUST 可持久化到运行期设置，并在桌面端启动时生效。

#### Scenario: 列出输出设备

- **WHEN** 用户打开设置页的音频输出区域
- **THEN** 系统 MUST 列出本机具备输出通道的音频设备
- **AND** MUST NOT 把只有输入通道的设备（如麦克风）列入输出列表

#### Scenario: 指定输出设备

- **WHEN** 用户选择了某个音频输出设备并保存
- **THEN** 桌面端后续创建输出流时 MUST 使用该设备
- **AND** 该选择 MUST 在重启桌面端后仍然生效

#### Scenario: 测试播放

- **WHEN** 用户点击「测试播放」
- **THEN** 系统 MUST 用当前选定的设备播放一段非静音的短提示音

#### Scenario: 连接建立后的提示音

- **WHEN** 数字人会话建立成功且设置中启用了启动提示音
- **THEN** 桌面端 MUST 播放一次测试提示音

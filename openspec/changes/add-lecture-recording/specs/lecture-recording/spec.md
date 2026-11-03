# Spec Delta

## ADDED Requirements

### Requirement: 展示页必须提供录制入口

`/knowtalk-chat.html` 的数字人画面区域 MUST 常驻一个录制入口，位置在画面右上角，MUST NOT 遮挡字幕条与连接重试按钮。

入口 MUST 有两态：空闲为「录制」，录制中为红点 + 已录时长 + 「停止」。录制中 MUST 每秒刷新时长。

#### Scenario: 入口常驻

- **WHEN** 打开任意一节课的展示页
- **THEN** 数字人画面区域内 MUST 出现「录制」按钮
- **AND** 该按钮 MUST NOT 与字幕条、连接重试按钮重叠

#### Scenario: 录制中显示计时

- **WHEN** 使用者点击「录制」并等待 5 秒
- **THEN** 按钮 MUST 变为红点 + 「停止」
- **AND** 时长 MUST 显示为 `00:05`

### Requirement: 录制必须合成人像与课件

录制画面 MUST 为 16:9 的合成帧：人像占左侧，当前幻灯片占右侧，底部叠加当前字幕文本。幻灯片 MUST 取画面里正在显示的那一页，MUST NOT 取播报队列里的未来页。

合成分辨率 MUST 不小于 1280×720，帧率 MUST 不低于 20fps。取不到幻灯片时 MUST 用空白纸底继续录，MUST NOT 中断录制。

#### Scenario: 幻灯片跟随

- **WHEN** 录制中数字人讲到第 3 页
- **THEN** 合成画面右侧 MUST 是第 3 页的图
- **WHEN** 播放推进到第 4 页
- **THEN** 合成画面右侧 MUST 在 1 秒内换成第 4 页

#### Scenario: 迟到开会

- **WHEN** 页面还没显示任何幻灯片时点击录制
- **THEN** 录制 MUST 正常开始，右侧为空白纸底
- **AND** 第一页出现后 MUST 自动补上

### Requirement: 录制必须带声音

录制产物 MUST 包含展示页正在播放的 TTS 音频轨道，且 MUST NOT 影响扬声器继续出声。

音频取不到时 MUST 退化为无声录制，并在结束提示里说明「本次未录到声音」。

#### Scenario: 有声录制

- **WHEN** 数字人正在播报并开始录制
- **THEN** 产物的音频轨道 MUST 与扬声器听到的内容一致
- **AND** 录制期间扬声器 MUST 仍正常出声

### Requirement: 录制产物必须可下载

停止录制 MUST 触发浏览器下载一个 `.webm` 文件，文件名 MUST 含课程名与 `YYYYMMDD-HHmmss` 时间戳。下载 MUST 由页面自身触发，MUST NOT 依赖额外弹窗确认。

录制 MUST 全程在浏览器内完成：MUST NOT 新增服务端录制接口，MUST NOT 往服务端写视频文件。

#### Scenario: 停止即下载

- **WHEN** 录制 20 秒后点击「停止」
- **THEN** 浏览器 MUST 下载一个大小非零的 `.webm`
- **AND** 文件名 MUST 匹配 `<课程名>_<8位日期>-<6位时间>.webm`
- **AND** 按钮 MUST 回到「录制」态，计时 MUST 归零

#### Scenario: 会话中途断开

- **WHEN** 录制中数字人连接断开
- **THEN** 录制 MUST 自动停止并照常下载已完成的部分
- **AND** MUST 提示录制已因断线结束

### Requirement: 录制不得干扰播放

录制 MUST NOT 暂停、重连或改变播放进度；MUST NOT 影响提问面板与幻灯片跟随。断开连接 MUST NOT 让页面报错或卡住。

浏览器不支持录制（无 `MediaRecorder` 或 `captureStream`）时，入口 MUST 呈现为禁用态并说明原因，MUST NOT 抛出未捕获异常。

#### Scenario: 录制期间照常提问

- **WHEN** 录制中向数字人提问并收到回答
- **THEN** 问答 MUST 正常进行，录制 MUST 不中断

#### Scenario: 浏览器不支持

- **WHEN** 在不支持 `MediaRecorder` 的浏览器打开展示页
- **THEN** 录制入口 MUST 为禁用态并给出原因
- **AND** 页面其它功能 MUST 正常

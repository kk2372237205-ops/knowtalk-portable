# Spec Delta

## ADDED Requirements

### Requirement: 复刻音色必须自动路由到正确的合成集群

`tts/doubao.py` 在选择 `cluster` 时 MUST 按下列优先级决定：

1. 若环境变量 `DOUBAO_CLUSTER` 非空，MUST 直接使用该值；
2. 否则若 `voice_type` 以 `S_` 开头（声音复刻产出的 speaker_id），
   MUST 使用 `volcano_icl`；
3. 否则 MUST 使用 `volcano_tts`。

使用复刻集群时，请求体 MUST 携带 `request.split_sentence = 1`。

#### Scenario: 用户把参考音色换成复刻音色

- **WHEN** 用户在设置里把 `tts.ref_file` 从 `zh-CN-YunyangNeural` 改成 `S_lKZjXSAt1`
- **THEN** 发往 `wss://openspeech.bytedance.com/api/v1/tts/ws_binary` 的
  `app.cluster` MUST 为 `volcano_icl`
- **AND** 请求体 MUST 含 `split_sentence: 1`

#### Scenario: 用户改回预设音色

- **WHEN** `tts.ref_file` 为 `zh_female_shuangkuaisisi_moon_bigtts`
- **THEN** `app.cluster` MUST 为 `volcano_tts`
- **AND** 请求体 MUST NOT 含 `split_sentence`

#### Scenario: 显式配置优先级最高

- **WHEN** `DOUBAO_CLUSTER=volcano_mega` 且 `tts.ref_file` 为 `S_abc12345`
- **THEN** `app.cluster` MUST 为 `volcano_mega`

### Requirement: 豆包引擎必须在 websockets 14 及以上版本可用

`tts/doubao.py` MUST 在建立连接时兼容 `extra_headers` 与 `additional_headers`
两种参数名，依据运行时 `websockets.connect` 的签名自动选择，MUST NOT 因库升级
而抛 `TypeError`。

#### Scenario: websockets 16.x 环境

- **WHEN** 运行环境为 websockets 16.1.1，用户选择 `doubao` 引擎并触发合成
- **THEN** 连接建立过程 MUST NOT 抛 `TypeError: connect() got an unexpected keyword argument 'extra_headers'`

### Requirement: 界面提供的 TTS 引擎必须与后端一致

桌面端「高级设置」的引擎与备用引擎下拉 MUST 取自 `utils.settings_store.TTS_ENGINES`，
MUST NOT 另写一份硬编码清单。

#### Scenario: 用户要选豆包引擎

- **WHEN** 用户展开「高级设置」
- **THEN** 引擎下拉 MUST 包含 `doubao`
- **AND** 备用引擎下拉 MUST 包含空串与 `TTS_ENGINES` 的全部取值

### Requirement: 参考音色必须支持填入无中文标签的音色 ID

「参考音色 / 参考音频」控件 MUST 允许自由输入，且取值转换 MUST 保留不在下拉
列表中的输入内容。

对 `  ·  `（两空格 + 中点 + 两空格）分隔的装饰性历史值，MUST 仍然返回空串，
以免把展示文案写回 `tts.ref_file`。

#### Scenario: 手填云端音色 ID

- **WHEN** 用户在参考音色里输入 `S_lKZjXSAt1` 并保存
- **THEN** 下发的 `tts.ref_file` MUST 为 `S_lKZjXSAt1`
- **AND** 重新打开设置页时 MUST 仍显示该值

#### Scenario: 历史脏值不被写回

- **WHEN** 输入框内容为 `云健  ·  普通话 ·  男声` 且不在音色映射中
- **THEN** 下发的 `tts.ref_file` MUST 为空串

### Requirement: 必须提供可复现的音色注册脚本

`scripts/register_voice.py` MUST 支持：

- `--audio` 注册新音色（自动生成 `S_` 前缀的 speaker_id）
- `--status S_xxx` 只查询训练状态
- `--speaker-id S_xxx` 对已有音色重新训练
- 上传前 MUST 校验时长并在偏离 10–30 秒时给出提示
- 非 wav / 多声道 / 非 24kHz / 超过 3MB 的输入 MUST 自动转为 24kHz 单声道
  PCM16 的临时文件，MUST NOT 修改用户原文件
- 缺少 `DOUBAO_APPID` / `DOUBAO_TOKEN` 时 MUST 以非零码退出并说明去哪个控制台获取

#### Scenario: 音频时长不足

- **WHEN** 对一段 7.8 秒的音频执行注册
- **THEN** 脚本 MUST 提示「略短于建议的 10s，相似度可能不足」
- **AND** MUST 继续执行，不中断

#### Scenario: 缺少凭证

- **WHEN** 未设置 `DOUBAO_APPID` 就运行脚本
- **THEN** MUST 打印获取位置说明并以非零码退出

### Requirement: 在线引擎失效必须可被察觉

`tts/edge.py` 在服务端返回 403 时 MUST 抛出 `TTSSynthesisError`，
MUST NOT 静默返回空音频。

#### Scenario: 旧版客户端被拒

- **WHEN** 当前 `edge-tts` 版本被微软以 403 拒绝
- **THEN** 合成 MUST 抛 `TTSSynthesisError`，并带上「改用离线引擎或指定备用引擎」的提示

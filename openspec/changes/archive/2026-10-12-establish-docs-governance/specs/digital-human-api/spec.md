# Spec Delta

## Purpose

定义 KnowTalk 主服务（aiohttp，默认 `8010` 端口）对外 HTTP 接口的行为契约，包括统一响应格式、会话寻址、文本与音频驱动、自定义动作状态、打断语义、录制、状态查询、声音克隆、RAG 代理与跨域配置。

## ADDED Requirements

### Requirement: 统一 JSON 响应格式与业务错误

除 WebRTC 信令响应、RAG 代理转发与文件下载外，所有接口 MUST 返回 JSON 对象。成功时 MUST 为 `{"code": 0, "msg": "ok"}`，并在有返回值时附带 `data` 字段；无返回值时 MUST NOT 输出 `data` 键。业务失败时 MUST 返回 `{"code": -1, "msg": "<错误描述>"}`。业务错误 MUST 以 HTTP 状态码 200 返回，MUST NOT 使用 4xx/5xx（RAG 代理转发与文件下载接口除外）。

#### Scenario: 成功响应

- **WHEN** 客户端调用 `POST /human` 且请求被接受
- **THEN** 响应体为 `{"code": 0, "msg": "ok"}`，不含 `data` 键
- **AND** HTTP 状态码为 200

#### Scenario: 业务失败响应

- **WHEN** 客户端请求引用了不存在的会话
- **THEN** 响应体为 `{"code": -1, "msg": "session not found"}`
- **AND** HTTP 状态码为 200

### Requirement: 会话寻址与未知会话错误

所有会话级接口 MUST 用 `sessionid` 字符串定位会话：`application/json` 接口从请求体读取该字段，`multipart/form-data` 接口从表单字段读取。`sessionid` 缺失时 MUST 按空字符串处理，并因找不到会话而返回 `{"code": -1, "msg": "session not found"}`。会话被空闲回收后，同一 `sessionid` MUST 同样表现为不存在。

#### Scenario: 缺少 sessionid

- **WHEN** 客户端调用 `POST /interrupt_talk` 且请求体不含 `sessionid`
- **THEN** 服务按空字符串查找会话并返回 `{"code": -1, "msg": "session not found"}`

#### Scenario: 会话已空闲回收

- **WHEN** 某会话超过空闲超时被回收，客户端再用原 `sessionid` 调用 `POST /is_speaking`
- **THEN** 服务返回 `{"code": -1, "msg": "session not found"}`

### Requirement: WebRTC 会话建立接口

`POST /offer` MUST 接受 JSON 请求体，其中 `sdp` 与 `type` 为必填。每个请求 MUST 创建一个独立的数字人会话并返回新的 `sessionid`；请求体中携带的 `sessionid` MUST NOT 用于复用已有连接。成功响应 MUST NOT 使用统一 JSON 信封，而是直接返回 `{"sdp": ..., "type": "answer", "sessionid": ...}`。

#### Scenario: 建立连接

- **WHEN** 客户端提交包含 `sdp` 与 `type=offer` 的 JSON
- **THEN** 响应为包含 `sdp`、`type=answer` 与新 `sessionid` 的 JSON 对象
- **AND** 响应体不含 `code`/`msg` 字段

### Requirement: `/offer` 的可选会话参数

`POST /offer` MUST 接受可选字段 `avatar`（本次连接使用的数字人 ID，缺省取启动参数值）、`refaudio` 与 `reftext`（音色参考音频与参考文本）、`custom_config`（动作编排配置的 JSON 字符串）。`refaudio` 存在时 MUST 覆盖该会话的参考音频与参考文本；`custom_config` 存在时 MUST 被解析为动作编排配置。这些参数 MUST 只作用于本次新建的会话，MUST NOT 影响其它会话。

#### Scenario: 指定形象与音色

- **WHEN** 提交 `avatar` 为 `my_wav2lip_avatar`、`refaudio` 为某音频路径、`reftext` 为对应文本
- **THEN** 新会话使用指定的形象与参考音频/文本
- **AND** 其它已存在的会话配置不受影响

#### Scenario: 指定动作编排

- **WHEN** `custom_config` 为合法的动作编排 JSON 字符串
- **THEN** 新会话使用该动作编排配置

#### Scenario: 动作编排配置非法

- **WHEN** `custom_config` 不是合法 JSON
- **THEN** 请求以错误结束，MUST NOT 返回 SDP 应答

### Requirement: 文本驱动的 echo 模式

`POST /human` 在 `type=echo` 时 MUST 将请求体的 `text` 直接交由 TTS 朗读，MUST NOT 调用 LLM。请求体中的 `tts` 对象（如 `voice`、`emotion`）存在时 MUST 透传给 TTS。接口 MUST 在受理后立即返回成功响应，MUST NOT 等待朗读完成。`type` 或 `text` 缺失时 MUST 返回业务错误。

#### Scenario: 朗读指定文本

- **WHEN** 客户端提交 `{"sessionid": "<sid>", "type": "echo", "text": "欢迎来到课堂"}`
- **THEN** 该会话朗读"欢迎来到课堂"，且不产生 LLM 请求
- **AND** 响应为 `{"code": 0, "msg": "ok"}`

#### Scenario: 缺少 type 字段

- **WHEN** 客户端提交 `{"sessionid": "<sid>", "text": "你好"}`
- **THEN** 服务返回 `code` 为 `-1` 的业务错误响应

### Requirement: 文本驱动的 chat 模式

`POST /human` 在 `type=chat` 时 MUST 把 `text` 作为用户提问交给对话 LLM，并将流式生成的回答分段送入 TTS 朗读。该模式 MUST 以环境变量 `DEEPSEEK_API_KEY` 非空为前置条件；未配置时请求仍 MUST 返回成功响应，但 MUST NOT 产生任何朗读内容。`type` 字段为其它值时 MUST 返回成功响应且 MUST NOT 产生朗读内容。

#### Scenario: 已配置 LLM

- **WHEN** 已配置 `DEEPSEEK_API_KEY`，客户端提交 `{"sessionid": "<sid>", "type": "chat", "text": "介绍下这门课"}`
- **THEN** 该会话开始朗读 LLM 生成的回答
- **AND** 响应为 `{"code": 0, "msg": "ok"}`

#### Scenario: 未配置 LLM

- **WHEN** 未配置 `DEEPSEEK_API_KEY`，客户端提交 `type=chat` 的请求
- **THEN** 服务返回成功响应，且该会话不产生新的朗读内容

#### Scenario: type 为未知值

- **WHEN** 客户端提交 `{"sessionid": "<sid>", "type": "unknown", "text": "你好"}`
- **THEN** 服务返回 `{"code": 0, "msg": "ok"}`
- **AND** 该会话不产生新的朗读内容

### Requirement: 幻灯片标签解析与清洗

`/human` 的 `text` 中形如 `[PPT:N]` 或 `[slide:N]` 的标签 MUST 被解析并从文本中移除。清洗后交给 TTS 朗读的文本与交给 LLM 的文本中 MUST NOT 再出现该标签。标签中的数字 MUST 先按幻灯片 `id` 精确匹配，匹配不到时再按 1 起始的页序解析。

#### Scenario: 标签被清洗

- **WHEN** 客户端提交 `type=echo`、`text="[PPT:2] 今天介绍新产品。"`
- **THEN** 朗读文本为"今天介绍新产品。"，不含 `[PPT:2]`

#### Scenario: 标签数字按页序回退

- **WHEN** 标签数字不等于任何幻灯片 `id`，但处于幻灯片总数范围内
- **THEN** 该数字被解释为 1 起始的页序并解析出对应幻灯片

### Requirement: echo 模式按段切页朗读

`type=echo` 时，服务 MUST 按标签在文本中出现的先后顺序把文本切分为多段，每段携带其应显示的幻灯片。幻灯片 MUST 在该段开始朗读时才切换，MUST NOT 在请求受理时一次性切到最后一页。未被任何标签覆盖的前导文本 MUST 使用切换前的当前页。

#### Scenario: 随段切换

- **WHEN** 客户端提交 `text="[PPT:1] 欢迎。[PPT:2] 今天介绍新产品。"`
- **THEN** 服务先朗读"欢迎。"并显示第 1 页，再朗读"今天介绍新产品。"并显示第 2 页

#### Scenario: 标签前有正文

- **WHEN** 客户端提交 `text="开场白。[PPT:2] 切换到第二页。"`
- **THEN** "开场白。"在不切换幻灯片的情况下朗读
- **AND** 朗读"切换到第二页。"时切换到第 2 页

### Requirement: chat 模式先切页再交给 LLM

`type=chat` 时，服务 MUST 在调用 LLM 之前解析最后一个幻灯片标签并立即切换该会话的当前页，MUST 把移除全部标签后的文本作为 LLM 输入。回答播放过程中，服务 MAY 依据回答文本与幻灯片关键词的匹配继续切换页面。

#### Scenario: 提问带标签

- **WHEN** 客户端提交 `type=chat`、`text="[PPT:3] 这页讲了什么？"`
- **THEN** 会话当前页在 LLM 调用前切换为标签对应的页
- **AND** 传给 LLM 的用户文本为"这页讲了什么？"

### Requirement: 打断参数与打断接口

`POST /human` 请求体中 `interrupt` 为真值时，服务 MUST 先清空该会话当前合成中与队列中尚未播放的文本，再登记本次请求。`POST /interrupt_talk` MUST 只接受 `sessionid` 并执行同样的清空动作，且 MUST NOT 产生新的朗读内容。二者成功时 MUST 返回 `{"code": 0, "msg": "ok"}`。

#### Scenario: 带 interrupt 的新请求

- **WHEN** 会话正在朗读一段长文本，客户端提交 `interrupt=true` 的新 `POST /human`
- **THEN** 尚未播放的旧文本被丢弃，只朗读新请求的内容

#### Scenario: 单独调用打断接口

- **WHEN** 客户端调用 `POST /interrupt_talk` 且会话存在
- **THEN** 该会话立即停止并清空待播内容
- **AND** 响应为 `{"code": 0, "msg": "ok"}`

### Requirement: 被打断的旧请求不得继续入队

打断发生时，服务 MUST 使此前已受理、仍在生成中的请求失效：这些请求 MUST NOT 再向朗读队列追加新的文本或音频。被打断的 LLM 回答 MUST NOT 写入该会话的对话记忆。

#### Scenario: LLM 流式回答期间被打断

- **WHEN** `type=chat` 的回答仍在流式生成时，客户端调用 `POST /interrupt_talk`
- **THEN** 该回答不再向朗读队列追加任何分段
- **AND** 该回答的内容不出现在会话的对话记忆中

#### Scenario: echo 多段朗读期间被打断

- **WHEN** `type=echo` 的分段文本尚未全部入队时，客户端提交带 `interrupt=true` 的新请求
- **THEN** 旧请求剩余分段不再入队

### Requirement: chat 模式的会话级多轮记忆

chat 模式 MUST 按会话隔离对话记忆，不同 `sessionid` MUST NOT 共享历史。服务 MUST 只保留每个会话最近 N 轮（一问一答为一轮）历史，N 由环境变量 `CHAT_HISTORY_ROUNDS` 控制、默认 10。历史 MUST 一并作为 LLM 输入，且 MUST NOT 被复述进回答文本。

#### Scenario: 记忆按会话隔离

- **WHEN** 会话 A 提问后再由会话 B 提问
- **THEN** 会话 B 的 LLM 输入不包含会话 A 的历史

#### Scenario: 历史轮数上限

- **WHEN** 某会话的对话轮数超过 `CHAT_HISTORY_ROUNDS` 配置值
- **THEN** 只有最近 N 轮历史被送入 LLM

### Requirement: 音频上传驱动

`POST /humanaudio` MUST 接受 `multipart/form-data`，字段为 `file`（音频文件）与 `sessionid`。文件内容 MUST 被解码为音频流并按会话既有采样率送入驱动流程，且 MUST NOT 作为用户文本进入 LLM。成功时 MUST 返回 `{"code": 0, "msg": "ok"}`。

#### Scenario: 上传音频

- **WHEN** 客户端以 multipart 上传 `sessionid` 与 `file`（16kHz 单声道 WAV）
- **THEN** 该会话按音频内容驱动口型与语音
- **AND** 响应为 `{"code": 0, "msg": "ok"}`

#### Scenario: 缺少 file 字段

- **WHEN** 客户端只提交 `sessionid` 字段
- **THEN** 服务返回 `code` 为 `-1` 的业务错误响应

### Requirement: 自定义动作状态设置接口

`POST /set_audiotype` MUST 从请求体读取 `sessionid` 与 `audiotype`，并 MUST 把 `audiotype` 交给该会话的自定义动作编排状态设置。会话不存在时 MUST 返回 `{"code": -1, "msg": "session not found"}`；`audiotype` 缺失时 MUST 返回 `code` 为 `-1` 的业务错误响应。成功时 MUST 返回 `{"code": 0, "msg": "ok"}`，不含 `data` 键。

#### Scenario: 设置自定义动作状态

- **WHEN** 客户端提交 `{"sessionid": "<sid>", "audiotype": 2}`
- **THEN** 该会话的自定义动作状态被设为 `2`
- **AND** 响应为 `{"code": 0, "msg": "ok"}`

#### Scenario: 会话不存在

- **WHEN** 客户端提交不存在会话的 `sessionid`
- **THEN** 响应为 `{"code": -1, "msg": "session not found"}`

#### Scenario: 缺少 audiotype 字段

- **WHEN** 客户端提交 `{"sessionid": "<sid>"}`
- **THEN** 服务返回 `code` 为 `-1` 的业务错误响应

### Requirement: 说话状态与 LLM 文本查询

`POST /is_speaking` MUST 从请求体读取 `sessionid`，并在 `data` 中返回布尔值，表示该会话当前是否正在说话。`POST /llm_status` MUST 在 `data` 中返回 `text`（当前会话累计的 LLM 输出文本）与 `done`（本轮 LLM 生成是否已结束），供前端同步问答气泡。两者对未知会话 MUST 返回 `session not found` 业务错误。

#### Scenario: 查询说话状态

- **WHEN** 客户端调用 `POST /is_speaking`
- **THEN** 响应为 `{"code": 0, "msg": "ok", "data": <bool>}`

#### Scenario: 轮询 LLM 文本

- **WHEN** chat 模式回答生成过程中客户端反复调用 `POST /llm_status`
- **THEN** `data.text` 随生成内容增长
- **AND** 生成结束后 `data.done` 为 `true`

### Requirement: 录制控制与录像下载

`POST /record` MUST 从请求体读取 `sessionid` 与 `type`：`start_record` MUST 开始录制该会话的音视频，`end_record` MUST 结束录制并生成 MP4。`GET /record/{sessionid}` MUST 下载已生成的 MP4 文件流；文件不存在时 MUST 返回 404 与文本 `Record not found`；路径参数为空时 MUST 返回 400 与文本 `sessionid is required`。

#### Scenario: 开始与结束录制

- **WHEN** 依次提交 `type=start_record` 与 `type=end_record`
- **THEN** 两次调用均返回 `{"code": 0, "msg": "ok"}`
- **AND** 录制结束后可按 `sessionid` 下载到 MP4 文件

#### Scenario: 下载不存在的录像

- **WHEN** 客户端请求 `GET /record/<从未录制的会话>`
- **THEN** HTTP 状态码为 404
- **AND** 响应体为 `Record not found`

### Requirement: 服务状态查询接口

`GET /api/status` MUST 返回 `data.rag_available`（RAG 服务健康检查是否可用，超时上限约 0.8 秒）、`data.llm_configured`（`DEEPSEEK_API_KEY` 是否非空）与 `data.sessions`（当前活跃会话数量）。RAG 不可用时 MUST 以 `false` 返回而 MUST NOT 使接口失败。

#### Scenario: RAG 未启动

- **WHEN** RAG 服务未运行且客户端调用 `GET /api/status`
- **THEN** 响应为 `{"code": 0, "msg": "ok", "data": {"rag_available": false, "llm_configured": <bool>, "sessions": <int>}}`

### Requirement: 管理配置与活跃会话查询

`GET /api/admin/config` MUST 在 `data.config` 中返回服务启动参数的字段集合；启动参数不可用时 MUST 返回 `{"code": -1, "msg": "Config not found"}`。`GET /api/admin/sessions` MUST 在 `data.sessions` 中返回活跃会话数组，每项 MUST 含 `sessionid`、`speaking`、`recording`，并在会话配置可用时含 `model`、`avatar_id`、`REF_FILE`、`transport`、`batch_size`、`customopt`。

#### Scenario: 读取启动参数

- **WHEN** 客户端调用 `GET /api/admin/config`
- **THEN** `data.config` 包含 `listenport`、`transport`、`model`、`avatar_id`、`batch_size` 等启动参数

#### Scenario: 读取活跃会话

- **WHEN** 存在一个正在说话的会话，客户端调用 `GET /api/admin/sessions`
- **THEN** 返回数组中含该会话，且 `speaking` 为 `true`、`recording` 为 `false`
- **AND** 数组不含无对应会话实例的空占位项

### Requirement: 声音克隆样音管理

`POST /api/voice/upload` MUST 接受 `multipart/form-data`，字段为 `file`（音频文件）与 `name`（声音名称，缺省为 `未命名`），并返回 `data.path`（相对路径）、`data.name`、`data.size`；缺少文件时 MUST 返回 `请上传音频文件` 业务错误。`GET /api/voices` MUST 返回已登记声音数组（`data` 直接为数组），每项含 `name`、`path`、`size`，且只包含 `.wav`、`.mp3`、`.ogg`、`.m4a`、`.flac`。`POST /api/voice/apply` MUST 校验 `sessionid` 与 `ref_file`，并把该音色应用到会话，返回 `ref_file`、`ref_text`、`engine` 回显。

#### Scenario: 上传并应用音色

- **WHEN** 客户端上传名为"讲师甲"的 WAV，再以该文件的 `path` 调用 `POST /api/voice/apply`
- **THEN** 首次响应 `data.name` 为"讲师甲"且 `data.path` 指向 `data/voices/` 下的文件
- **AND** 应用响应回显相同的 `ref_file`，该会话后续朗读使用新音色

#### Scenario: 列出声音

- **WHEN** 客户端调用 `GET /api/voices`
- **THEN** `data` 为按文件名排序的声音对象数组，且不包含非音频文件

#### Scenario: 未指定声音文件

- **WHEN** 客户端调用 `POST /api/voice/apply` 但请求体不含 `ref_file`
- **THEN** 服务返回 `{"code": -1, "msg": "请指定声音文件"}`

### Requirement: 声音样本删除

`POST /api/voice/delete` MUST 从请求体读取 `path`；`path` 缺失时 MUST 返回业务错误 `请指定文件路径`。相对路径 MUST 相对项目根目录解析。目标存在时 MUST 删除该文件并返回 `{"code": 0, "msg": "ok"}`；目标不存在时 MUST 返回业务错误 `文件不存在`。

#### Scenario: 删除已登记声音

- **WHEN** 客户端提交 `GET /api/voices` 返回的某个 `path`
- **THEN** 该文件被删除，响应为 `{"code": 0, "msg": "ok"}`
- **AND** 再次列举声音时不再包含该项

#### Scenario: 目标文件不存在

- **WHEN** 客户端提交一个在项目根目录下不存在的 `path`
- **THEN** 服务返回 `{"code": -1, "msg": "文件不存在"}`

#### Scenario: 未提供 path

- **WHEN** 请求体不含 `path`
- **THEN** 服务返回 `{"code": -1, "msg": "请指定文件路径"}`

### Requirement: RAG 旁路服务代理

`POST /rag/ask` MUST 把请求体 JSON 原样转发到 `RAG_BASE_URL`（默认 `http://127.0.0.1:8090`）的 `/rag/ask`，并把上游状态码与响应体原样返回给调用方；转发超时上限由 `RAG_PROXY_TIMEOUT` 控制（默认 120 秒）。上游超时或不可达时 MUST 返回 503 与 `{"code": 503, "msg": "RAG 服务不可用: <原因>"}`。

#### Scenario: 转发问答

- **WHEN** RAG 服务可用，客户端提交 `{"question": "什么是微课"}`
- **THEN** 响应状态码与响应体与上游 `/rag/ask` 一致

#### Scenario: RAG 服务未启动

- **WHEN** RAG 服务不可达且客户端调用 `POST /rag/ask`
- **THEN** 服务返回 HTTP 503 与 `code` 为 503 的 JSON 错误

### Requirement: RAG 管理接口代理与超时码

`/rag/admin/{tail}` MUST 显式支持 `GET`、`POST`、`PUT`、`DELETE` 四种方法，并 MUST 原样透传方法、查询串、请求体以及 `Content-Type`、`Authorization` 请求头，同时返回上游状态码、响应体与 `Content-Type`、`Content-Disposition`、`Content-Length` 响应头。超时上限由 `RAG_PROXY_TIMEOUT` 控制（默认 600 秒）。上游超时时 MUST 返回 504 与提示"知识库处理超时…"；其它转发失败 MUST 返回 503。

#### Scenario: 透传方法

- **WHEN** 客户端以 `DELETE /rag/admin/documents/7` 调用
- **THEN** 上游以 `DELETE` 方法、相同路径尾部与查询串收到请求
- **AND** 上游状态码与响应体被原样返回

#### Scenario: 上游处理超时

- **WHEN** 文档解析与索引耗时超过 `RAG_PROXY_TIMEOUT`
- **THEN** 服务返回 HTTP 504 与 `code` 为 504 的 JSON 错误

### Requirement: 跨域访问配置

服务 MUST 为全部已注册路由应用同一套跨域配置：`allow_credentials` 为真、`expose_headers` 与 `allow_headers` 为 `*`。管理类与状态类接口（`/api/admin/*`、`/api/voice/*`、`/api/status`、`/rag/admin/*`）与其他接口使用相同的跨域响应头。

#### Scenario: 同源前端调用

- **WHEN** 由主服务自身页面（同源）调用管理类接口
- **THEN** 请求正常返回统一 JSON 响应信封

#### Scenario: 跨域来源调用管理接口

- **WHEN** 浏览器从与主服务不同的来源携带凭据调用 `GET /api/admin/config`
- **THEN** 响应含 `Access-Control-Allow-Origin` 与 `Access-Control-Allow-Credentials: true` 跨域响应头
- **AND** 响应体为 `{"code": 0, "msg": "ok", "data": {"config": {...}}}`

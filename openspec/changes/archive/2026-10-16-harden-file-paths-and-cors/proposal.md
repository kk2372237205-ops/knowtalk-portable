# Proposal

## Why

2026-10-12 的项目审计（`docs/audit/2026-10-12-project-audit.md` 第 3.5 节）登记了四处"请求参数直接决定文件系统路径"的注入面与一处跨域通配配置。审计之后只落地了**鉴权**（`server/auth.py` 中间件），**路径与跨域这几处至今未修**，现状可逐行复核：

- `server/routes.py:556-572`：`voice_delete` 把请求体的 `path` 直接 `os.path.join(项目根, path)` 后 `os.remove`。传入绝对路径时 `os.path.join` 会丢弃前缀，传入 `..` 时逐级上跳——两条路径都能删掉项目根以外的任意文件。
- `server/routes.py:478-487`：`voice_upload` 用请求体里的 `name` 拼落盘文件名（`f"{voice_name}{ext}"`），`name` 未做任何净化，`ext` 直接取上传文件的扩展名。`name` 传 `../../server/evil` 即可写到 `data/voices/` 之外。
- `server/avatar_routes.py:142-152`：上传视频的落盘名为 `f"{uuid4()}_{filename}"`，`filename` 是浏览器可控的原始文件名，含 `..\\..\\` 时前缀 `uuid` 抵消不了上跳。
- `server/avatar_routes.py:173-175`：`video_path` 为绝对路径时原样接受并交给生成任务读取，可指向磁盘上任意文件。
- `server/avatar_routes.py:198` → `server/task_manager.py:136`：`notifyurl` 由请求方任意指定，服务在任务每次状态变更时 `requests.post` 过去，构成服务端请求伪造（SSRF），可用来探测内网。
- `app.py:537-547`：全部路由套用 `defaults={"*": ResourceOptions(allow_credentials=True)}`，即通配来源 + 允许凭据。

为什么现在做：这几处都是"传入一个字符串就能越界读写"，修复成本集中在参数校验，不需要改动任何业务流程；同时它们与已落地的口令鉴权是**互补**关系——口令是唯一一道门，一旦口令泄露或某个接口漏登记受保护路径（`server/auth.py` 的 `PROTECTED_PREFIXES`/`PROTECTED_EXACT` 需要人工维护），路径校验就是最后一道防线。跨域通配则在"口令尚未启用或已泄露"时让任意站点可以带凭据读取管理数据。

## What Changes

- **声音样本删除限定在声音目录内**：`POST /api/voice/delete` 的 `path` 解析成绝对路径后必须落在 `data/voices/` 之内（用 `realpath` 比较，防符号链接逃逸），越界时返回业务错误——而不是静默不删或抛异常。
- **声音样本落盘文件名净化**：`POST /api/voice/upload` 的 `name` 只接受不含路径分隔符、控制字符与 Windows 保留字符的取值，扩展名限定为 `GET /api/voices` 已认可的音频后缀集合，两者任一非法即返回业务错误且不落盘。
- **上传视频落盘文件名净化**：`POST /api/avatar/task` 的 `multipart` 上传只取原始文件名的 basename 并做同样的字符净化，仍保留"随机前缀 + 净化后原名"的命名语义与"不覆盖同名文件"的性质。
- **`video_path` 限定在允许的根目录内**：服务自己的 `data/` 目录永远是允许根；`AVATAR_VIDEO_ROOTS`（逗号分隔）在此基础上**追加**更多根，只能放宽不能收紧。相对路径仍相对 `./data/avatars` 解析并做同样的包含性校验。越界时返回业务错误且不创建任务。
- **`notifyurl` 改为显式允许列表**：新增环境变量 `AVATAR_NOTIFY_ALLOWED_HOSTS`（逗号分隔的主机名或 `主机:端口`）。只有在允许列表内的 `notifyurl` 才被接受，且 URL 协议限定为 `http`/`https`；允许列表为空（缺省）时任何 `notifyurl` 都被拒绝并返回指明该环境变量的业务错误。任务创建时校验，越界即不创建任务。
- **跨域来源收敛为显式允许列表**：新增环境变量 `CORS_ALLOWED_ORIGINS`（逗号分隔的完整来源）。只对列出的来源返回跨域响应头；未配置时不再对任何来源返回跨域响应头（同源请求不受影响，浏览器本就不需要跨域头）。
- **不改变的部分**：同源前端、`GET /api/voices` 的返回结构、口令鉴权、RAG 旁路、TTS/讲稿/提问模式/手势链路、所有 `data/` 之外的业务逻辑。
- **BREAKING（配置面）**：依赖跨域调用管理接口的部署需要在 `CORS_ALLOWED_ORIGINS` 中显式登记来源；依赖任务回调的部署需要在 `AVATAR_NOTIFY_ALLOWED_HOSTS` 中显式登记回调主机；把 `video_path` 指向 `data/` 之外绝对路径的部署需要把该目录加进 `AVATAR_VIDEO_ROOTS`。三者的缺省行为都是"拒绝并给出指明环境变量的错误信息"，不会静默放行也不会静默失败。

## Capabilities

### New Capabilities

无。本次只收紧既有接口的参数接受范围，不引入新的能力域。

### Modified Capabilities

- `digital-human-api`：
  - **声音克隆样音管理**：`name` 与扩展名 MUST 通过合法性校验，非法时 MUST 返回业务错误且 MUST NOT 落盘。
  - **声音样本删除**：`path` MUST 落在声音目录内，越界 MUST 返回业务错误且 MUST NOT 删除。
  - **跨域访问配置**：从"全部路由通配来源 + 允许凭据"改为"仅配置来源获得跨域响应头"，并新增越界来源不获得响应头的场景。
- `avatar-management`：
  - **视频来源的落盘与路径解析**：上传落盘文件名 MUST 使用净化后的 basename；`video_path`（含绝对路径）MUST 落在允许根目录内，越界 MUST 返回业务错误且 MUST NOT 创建任务。
  - **任务回调通知**：`notifyurl` MUST 通过协议与主机允许列表校验，未通过 MUST 返回业务错误且 MUST NOT 创建任务。

## Impact

- **修改代码**：`server/routes.py`（音色上传/删除）、`server/avatar_routes.py`（上传落盘与 `video_path` 校验）、`server/task_manager.py`（回调地址校验，或新增独立校验模块）、`app.py`（CORS 来源收敛）。
- **新增代码**：建议把"路径包含性校验"与"回调地址校验"抽成一个独立小模块（如 `utils/path_guard.py`），供三处复用，避免三份各自实现出现判定口径不一致。
- **新增配置**：`CORS_ALLOWED_ORIGINS`、`AVATAR_VIDEO_ROOTS`、`AVATAR_NOTIFY_ALLOWED_HOSTS`，需同步补进 `.env.example` 与 README 的部署说明。
- **新增测试**：`tests/test_path_guard.py`（校验函数本身：正常、`..`、绝对路径、符号链接、Windows 大小写与分隔符）、以及对 `voice_delete`/`voice_upload`/`avatar` 上传与 `video_path`/`notifyurl` 的接口级用例。
- **不影响**：前端（`web/` 下无任何 `video_path` 或 `notifyurl` 用法，已经逐文件检索确认）、`openspec/specs/` 之外的既有实现、依赖清单。
- **风险**：把"绝对路径直接使用"改为"必须在允许根内"是行为收紧，可能让既有部署的绝对路径用法失效。缓解：缺省允许根取项目内的 `data/`，并用环境变量保留放开能力；失败时返回的选择性错误信息直接指出该配哪个变量，可自助恢复。

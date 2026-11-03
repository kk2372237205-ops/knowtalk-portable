# Design

## Context

审计（`docs/audit/2026-10-12-project-audit.md` 第 3.5 节）登记的注入面，在 2026-10-12 之后只落地了鉴权层：`server/auth.py` 提供 `create_auth_middleware`，`app.py` 把它注册为 aiohttp 中间件，受保护路径由 `PROTECTED_PREFIXES` / `PROTECTED_EXACT` 两张常量表人工维护。路径与跨域部分未动。

约束与现状：

- 服务是单一 aiohttp 应用，配置来源是**环境变量 + `data/settings/settings.json`**（`utils/settings_store.py`）。运行期设置支持 `EFFECT_RESTART`，设置页可以改并提示重启。
- 跨域配置发生在 `app.py` 的启动函数里，遍历 `appasync.router.routes()` 逐条 `cors.add(route)`，因此**只能在启动期决定**，改配置必然要重启——这与 `EFFECT_RESTART` 的语义一致。
- `data/voices/` 是应用自己的声音目录，`GET /api/voices` 的 `path` 一律形如 `data/voices/<file>`，前端只回传这个值。
- `video_path` 与 `notifyurl` 在 `web/` 下**零引用**（已逐文件检索 `web/*.html`、`web/*.js` 确认），说明这两条参数目前只有外部脚本/API 调用方在用，收紧不会打断任何内置界面流程。
- 目标平台以 Windows 为主（`start.bat`、`chcp 65001`、conda 环境），因此路径比较必须处理盘符、反斜杠、大小写不敏感；同时项目在 Linux/Docker 下也要能跑，不能写死 Windows 语义。
- 现有测试用 pytest，测试目录 `tests/`，接口级用例用 aiohttp 的测试客户端风格。**本机 `livetalking` 环境原本未安装 pytest**，本次已安装 `pytest 9.1.1`。

## Goals / Non-Goals

**Goals:**

- 让"请求参数里的字符串"再也无法决定项目允许范围之外的文件系统路径。
- 让服务只向显式登记的外部主机发起回调、只向显式登记的来源返回跨域响应头。
- 三处路径判定共用**同一份**判定实现，避免出现"声音目录查得严、形象目录查得松"这类口径漂移。
- 每种拒绝都返回**可自助恢复**的业务错误：错误信息直接点名该配哪个环境变量。
- 判定逻辑与 aiohttp 解耦，能用纯单元测试覆盖 Windows/Linux 两套语义。

**Non-Goals:**

- 不做认证/授权改造（已由 `server/auth.py` 落地），不引入角色或权限模型。
- 不做 DNS 解析后的内网地址封堵（anti-DNS-rebinding）。允许列表**按主机名匹配**，不解析 IP；这条边界在规格与文档里如实写明。
- 不重构 `app.py` 的启动流程，不把端口绑定提前（那是审计里另一条 `improve-startup-failure-handling` 的范围）。
- 不新增 `video_path` 上传接口或改变 `data/tmp` 的上传约定。

## Decisions

### D1：抽出 `utils/path_guard.py` 统一路径判定

提供三个原语：

- `resolve_within(root, candidate)`：把 `candidate` 解析为绝对真实路径，若不在 `root` 之内则抛 `PathGuardError`。
- `is_within(root, candidate)`：布尔版本，`resolve_within` 的实现基础。
- `sanitize_filename(name, fallback)`：取 basename、去掉路径分隔符与控制字符、拒绝 `.`/`..`/空串，失败时返回 `fallback`。

判定实现：

1. 用 `os.path.realpath` 归一化**两侧**，让符号链接在比较前就被解开。
2. 用 `os.path.normcase` 统一大小写与分隔符（Windows 小写化，Linux 原样）。
3. 用 `os.path.commonpath` 判断包含关系；它跨盘符时抛 `ValueError`，捕获后一律判定为"不在之内"。
4. 额外要求 `commonpath` 的结果**等于** `root`——防止 `data/voices2` 这类前缀相同但并非子目录的路径被误判为在 `data/voices` 之内（`startswith` 的经典陷阱）。

**为什么不用 `startswith`**：字符串前缀判断既是上面那个陷阱的来源，又处理不了符号链接与盘符大小写。**为什么不用 `os.path.relpath` + `..` 判断**：它同样不处理符号链接，且在不同盘符上行为依赖实现。

### D2：`data/voices/` 固定，不做成可配置

声音目录是应用自身的数据目录，不是部署时用户放置素材的位置，没有放开的需求。固定为 `<项目根>/data/voices` 让规格可以写成"MUST 位于 `data/voices/` 之内"这种无歧义的强约束。相比之下 `video_path` 确实有"素材在别处"的真实诉求，才引入可配置允许根（D3）。

### D3：`video_path` 允许根 = 服务自己的 `data/` **加上** `AVATAR_VIDEO_ROOTS`

- 相对路径先按既有语义解析为 `./data/avatars/<value>`，再走包含性校验。
- 绝对路径直接走包含性校验。
- 服务自己的 `data/` 目录（形象素材 `data/avatars` 与上传落盘 `data/tmp` 所属的目录）**永远**是允许根。
- `AVATAR_VIDEO_ROOTS`（逗号分隔，相对路径按项目根解析）是**追加**，不是替换。

**为什么是追加而不是替换**：实现期的测试直接暴露了两个反例——若配置值替换默认根，
（1）`POST /api/avatar/task` 刚把上传文件写进 `data/tmp`，紧接着的包含性校验就会把它判成
越界，**上传功能整体失效**；（2）相对 `video_path` 会解析到 `data/avatars`，同样失效。
运维设置这个变量的意图是"再允许一个素材目录"，不是"关掉上传与形象素材"。因此配置只能
放宽、不能收紧。这条由"配置允许根不影响服务自己的上传与素材"两个用例固化。

**权衡**：默认值会让"把 `video_path` 指向 `D:\videos\x.mp4`"的既有用法失效，需要显式配置
`AVATAR_VIDEO_ROOTS`。这是**故意的**——审计把该行为登记为"中危：可指向任意本地文件"，
而它的合法诉求可以用环境变量表达。失效方式是明确报错并点名环境变量，不是静默失败。

### D4：`notifyurl` 采用"协议允许集合 + 主机允许列表"，缺省拒绝全部

- 用 `urllib.parse.urlsplit` 解析；`scheme` 必须落在 `{http, https}`。
- 主机匹配 `AVATAR_NOTIFY_ALLOWED_HOSTS`（逗号分隔）。条目可以是 `host`（匹配该主机的任意端口）或 `host:port`（要求端口一致）；比较对 host 做小写归一化。
- 列表为空/未设置 → 拒绝所有 `notifyurl`。
- 校验时机放在**任务创建**（`server/avatar_routes.py`），而不是发送时（`server/task_manager.py`）。理由：创建时就拒绝才能让调用方立刻知道配错了；放在发送时只会静默丢掉通知。`task_manager._notify` 保留既有的"失败不影响任务"姿态，作为第二道防线。

**权衡**：缺省拒绝会让既有回调部署立刻报错。选择"响亮失败"而不是"静默不发"，因为后者会让调用方以为回调机制正常而实际永远收不到通知。错误信息点名 `AVATAR_NOTIFY_ALLOWED_HOSTS`，恢复只需一条配置。

**已知边界（如实写进文档）**：允许列表按主机名匹配，不解析 IP，因此无法防住 DNS rebinding；攻击者若要利用必须先能改自己在允许列表内主机的 DNS。这条边界的代价被认为是可接受的——它挡的是"任意指定内网地址"这一主要利用方式。

### D5：跨域来源由 `CORS_ALLOWED_ORIGINS` 决定，为空时不安装跨域中间件

- 启动时解析环境变量为来源列表。
- 列表非空：`aiohttp_cors.setup(app, defaults={origin: ResourceOptions(allow_credentials=True, expose_headers="*", allow_headers="*") for origin in origins})`，再逐路由 `cors.add(route)`。
- 列表为空：**完全不调用** `aiohttp_cors.setup`，也不 `cors.add`。这样服务对任何来源都不返回跨域响应头，同源请求照常（浏览器同源请求本就不需要这些头）。

**为什么是"不安装"而不是"装一个空 defaults"**：避免依赖 `aiohttp_cors` 在空配置下的具体行为。不安装的语义是确定的，且实现更简单。这一点会用接口级测试固化下来。

### D6：拒绝一律走既有业务错误信封

沿用 `json_error(msg)`（`code: -1` / 指定码），不引入新状态码。理由是既有规格的 `统一 JSON 响应格式与业务错误` 已经定义了这套信封，前端也已经按它解析；本次只增加新的 `msg` 取值。

## Risks / Trade-offs

| 风险 | 影响 | 缓解 |
|---|---|---|
| 行为收紧导致既有部署失效 | 跨域、回调、绝对 `video_path` 三类用法需要补配置 | 三者缺省都是"拒绝 + 点名环境变量"，可自助恢复；`.env.example` 与 README 同步给出示例 |
| `realpath` 对不存在的路径行为 | 上传（目标文件尚不存在）时校验的是父目录 | 无需特殊分支：`realpath` 会解开父目录上的符号链接、把不存在的末段原样保留，创建场景与既存文件走同一套判定。测试覆盖"目标不存在但父目录在根内"与"父目录是符号链接"两种情形 |
| 路径判定在 Windows 与 Linux 语义不同 | 测试在本机（Windows）通过但在 CI（Linux）失败 | 判定全部走 `normcase`/`commonpath` 而非手工字符串处理；测试用例同时覆盖两种分隔符与跨盘符/跨根情形 |
| `commonpath` 对大小写与短文件名（8.3）的处理 | Windows 上 `PROGRA~1` 形式的短名可能绕过包含判断 | `realpath` 会归一化短名，且允许根由服务端配置而非请求方提供，利用前提不成立；作为已知边界记录 |
| 允许列表按主机名匹配，不防 DNS rebinding | 见 D4 已知边界 | 约束允许列表只填可信回调主机；边界写进文档而不是假装不存在 |
| 新增三个环境变量增加部署复杂度 | 运维成本 | 三个都有安全的缺省值（拒绝/仅同源），不配也能安全运行 |

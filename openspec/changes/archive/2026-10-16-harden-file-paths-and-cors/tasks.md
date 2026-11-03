# Tasks

## 1. 共用校验原语

- [x] 1.1 新增 `utils/path_guard.py`：实现 `PathGuardError`、`resolve_within(root, candidate)`、`is_within(root, candidate)`、`sanitize_filename(name, fallback='upload')`。判定用 `realpath` 归一化两侧、`normcase` 统一大小写与分隔符、`commonpath` 判包含，并要求 `commonpath` 结果**等于** root。验证：`tests/test_path_guard.py` 覆盖——目录内相对路径放行；`..` 上跳拒绝；绝对路径在 root 外拒绝；`data/voices2` 不被判为在 `data/voices` 内；跨盘符(Windows)拒绝；指向 root 之外的符号链接拒绝；`sanitize_filename` 对 `../../x`、`a\\b`、`..`、空串、含 `<>:"|?*` 与控制字符的取值均返回安全 basename（含 `..` 与空串时回退 fallback）
- [x] 1.2 新增 `utils/net_guard.py`：实现 `parse_allowed_origins(value)`、`parse_allowed_hosts(value)`、`validate_notify_url(url, allowed_hosts)`。协议只允许 `http`/`https`；host 匹配大小写不敏感；条目 `host` 匹配任意端口、`host:port` 要求端口一致。验证：`tests/test_net_guard.py` 覆盖——空列表拒绝全部；未登记主机拒绝；已登记主机放行；`host:port` 条目端口不符时拒绝；`file://`、`ftp://`、无 host 的取值拒绝；大小写不同的主机名放行；`parse_allowed_origins` 忽略空条目与首尾空白

## 2. 声音接口加固

- [x] 2.1 `server/routes.py` 的 `voice_delete` 改为用 `resolve_within(VOICES_DIR, ...)` 校验后再删除；越界返回业务错误且不触碰文件系统。验证：`tests/test_voice_guard.py` 断言 `path` 为 `../../README.md`、`data/voices` 之外的绝对路径、以及指向目录外的符号链接时响应 `code` 非 0 且目标文件仍存在；合法 `data/voices/x.wav` 仍被删除并返回 `{"code": 0, "msg": "ok"}`
- [x] 2.2 `server/routes.py` 的 `voice_upload` 改为对 `name` 走 `sanitize_filename` 风格的合法性校验、对扩展名走 `.wav/.mp3/.ogg/.m4a/.flac` 白名单，任一不合法即返回业务错误且不落盘。验证：`tests/test_voice_guard.py` 断言 `name` 为 `../../server/routes` 时无文件产生（含 `data/voices` 之外）、上传 `payload.py` 被拒、合法 `讲师甲` + `.wav` 成功且 `data.path` 位于 `data/voices/` 之内

## 3. 形象任务加固

- [x] 3.1 `server/avatar_routes.py` 的上传落盘改为使用净化后的 basename（保留"随机前缀 + 原名"语义与不覆盖性质）。验证：`tests/test_avatar_guard.py` 以文件名为 `..\\..\\evil.mp4` 的 multipart 上传，断言落盘路径仍在 `data/tmp` 之内、项目根之外未出现该文件
- [x] 3.2 `server/avatar_routes.py` 的 `video_path` 改为走 `resolve_within_any`，允许根 = 服务自己的 `data/` **加上** `AVATAR_VIDEO_ROOTS`（**实现期修正**：原计划为"配置替换缺省"，但实测会让服务自己刚上传的文件与相对路径双双被判越界，故改为追加；specs 与 design 已同步）。验证：`tests/test_avatar_guard.py` 断言——允许根内绝对路径创建任务成功且路径原样保留；允许根外绝对路径（如 `C:\\Windows\\win.ini`，非 Windows 下用 `/etc/passwd`）返回业务错误且任务数不变；相对路径仍解析为 `./data/avatars/<value>`；配置允许根后上传与相对路径仍可用
- [x] 3.3 `server/avatar_routes.py` 创建任务前用 `validate_notify_url` 校验 `notifyurl`，未通过返回点名 `AVATAR_NOTIFY_ALLOWED_HOSTS` 的业务错误且不创建任务。验证：`tests/test_avatar_guard.py` 断言——未配置允许列表时任何 `notifyurl` 被拒；`http://169.254.169.254/latest/meta-data` 被拒且无任务创建（用请求替身断言未发生出站调用）；已登记主机放行；`file:///etc/passwd` 被拒

## 4. 跨域收敛

- [x] 4.1 `app.py` 改为按 `CORS_ALLOWED_ORIGINS` 构造 `aiohttp_cors` 配置：列表非空时按来源逐一设置 `ResourceOptions(allow_credentials=True, expose_headers='*', allow_headers='*')` 并逐路由 `cors.add`；列表为空时完全不安装跨域中间件。验证：`tests/test_cors_policy.py` 构造只装本策略的最小 aiohttp 应用，断言——已登记来源获得该来源的 `Access-Control-Allow-Origin` 与 `Access-Control-Allow-Credentials: true`；未登记来源两者都不获得；未配置来源时同源请求正常返回且无跨域响应头；OPTIONS 预检对已登记来源返回 200 并对未登记来源不返回允许头
- [x] 4.2 把三个新环境变量补进 `.env.example`，并在 README 的部署/配置一节说明各自语义、缺省行为与恢复方式。验证：逐项对照实现核对变量名与缺省值一致；`CORS_ALLOWED_ORIGINS`、`AVATAR_VIDEO_ROOTS`、`AVATAR_NOTIFY_ALLOWED_HOSTS` 三者在 `.env.example` 中均出现且带注释

## 5. 复核与收尾

- [x] 5.1 回归确认收紧未破坏既有行为：`GET /api/voices`、`POST /api/voice/apply`、`GET /api/avatar/tasks`、既有 `tests/test_auth.py` 与 `tests/test_gesture_routes.py` 全部保持通过。验证：`python -m pytest tests/ -q` 输出无新增失败（实测 539 passed，基线 433 + 本次新增 106）
- [x] 5.2 复核 `server/auth.py` 的受保护路径表是否仍覆盖本次涉及的全部写接口（`/api/voice/upload`、`/api/voice/delete`、`/api/avatar/task`），确认路径校验是叠加而非替换鉴权。验证：`tests/test_auth.py` 中相关路径断言仍通过，并在 README 中记录两层的关系
- [x] 5.3 更新 `docs/audit/2026-10-12-project-audit.md` 第 3.5 节与第 6 节，把本次已修复的条目标注为已完成并指向本变更。验证：被标注的条目在本变更的实现中逐条能找到对应代码与测试（另补记了审计未登记但同属 P0 的 `os.system` 遗留项）

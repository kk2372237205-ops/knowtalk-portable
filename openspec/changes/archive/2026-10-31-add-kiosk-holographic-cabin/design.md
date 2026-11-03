# 设计

## 架构

- **存储层** `utils/kiosk_store.py`：纯文件 JSON，模块级单例函数 + `RLock` + 原子写（写临时文件再 `rename`）。`KIOSK_DIR` 写死指向 `data/kiosk`，不读环境变量（隔离测试用 `ks._path = lambda n: os.path.join(tmp, n)`）。
- **接口层** `server/kiosk_routes.py`：aiohttp 子路由，REST 风格。菜单上屏走"按 id 直达"而非文本模糊，避免多菜单串台；`activate_menu` 内部 RAG 检索 → `_speak` → 记 `qa_log`，无预置问题则直接念标题。
- **端侧** `desktop_app.py`：Tkinter 单文件；事件泵 `_drain_ui_q` 消费 `ui_q`（busy/ready/fail/login_ok）驱动界面切换。菜单叠加层 `place` 在 BroadcastPage 舞台底部，全屏另有 `_build_fs_menu_overlay`；每 15s 线程刷新 `list_enabled_menus`。
- **TTS** `tts/xtts.py` 依赖独立克隆服务 `TTS_SERVER=http://127.0.0.1:50000`（`scripts/voice_clone_server.py`，模型 `data/models/xtts_v2/model.pth`）；`ServiceLauncher` 在 `TTS=xtts` 时自动 `Popen` 该服务（端口已开则跳过），解决"服务不随主服务自启"导致的静默失败。
- **接近迎宾** `scripts/proximity_detect.py` 守护进程，检测成功回调写 proximity 状态并触发问候。

## 关键决策

- 菜单上屏用"按 id 直达 + activate 接口 + RAG"而非客户端模糊匹配，避免多菜单串台。
- XTTS 失败从"静默收尾 1 帧"改为"抛错触发 sapi 兜底"，并支持克隆服务后起自动恢复 speaker，消除永久静音。
- 桌面端连接流程先显示"正在检测主服务"再按 `_drain_ui_q` 状态切到登录/主界面，修复白屏。

## 验证手法

- 隔离验证：mock `subprocess.Popen` / `_port_open` / `sess`，临时 dir 指向 `ks._path`，不进 `mainloop`；`py_compile` 全过；`aiohttp.test_utils.TestClient` 验接口。
- 三文件 `py_compile` 通过；`data/settings/settings.json` 未污染。

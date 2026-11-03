# 全息舱展项能力（kiosk）

## Why

面向悦滔 75 寸 AI 圆形全息舱（9:16 竖屏 / I5-11·8G·1050Ti）的展项能力此前是零散补丁：唤醒/打断词库、菜单库、闲时视频/待机、问答记录、摄像头接近迎宾，以及桌面端（Tkinter）对应 UI 与参考音频声音克隆自启，都是在没有任何 OpenSpec 能力规范覆盖的情况下直接落地的，违反了 `engineering-workflow` 规范第 87–100 行的硬门禁（改变对外可观察行为必须先走 OpenSpec 变更）。

本次为**已交付**的 kiosk 能力补齐规范与变更记录，使"改什么"重新有据可依，并确立之后每个可观察行为变更先走 OpenSpec 变更的纪律。这是一次**回顾性（retrospective）捕获**：代码已实现并通过隔离验证，这里把它正式登记进规范体系。

## What Changes

- 新增 `utils/kiosk_store.py`：全息舱配置/记录的本地 JSON 存储（唤醒词、菜单、闲时视频、问答记录、接近迎宾），落本仓忽略路径 `data/kiosk/*.json`，原子替换 + 线程锁。
- 新增 `server/kiosk_routes.py`：`setup_kiosk_routes(app)` 挂 `/api/kiosk/*`，含唤醒词/菜单/闲时/问答/接近接口，以及本次补的 `get_menu` 与 `activate_menu`（按 id 直达 + RAG 检索 + 播报）、`list_enabled_menus`。
- 新增 `scripts/proximity_detect.py`：基于摄像头的人体接近检测守护进程（Detector/run/main），触发迎宾。
- 桌面端 `desktop_app.py`：导航栏重构、连接/登录流程修复（消除白屏）、按钮 tooltip、菜单库在 BroadcastPage 舞台与全屏叠加层上屏并可点击介绍、MenuPage 预览触发；`ServiceLauncher` 在 TTS=xtts 时自动拉起 `voice_clone_server.py --port 50000`。
- `tts/xtts.py`：speaker 惰性获取 + 克隆服务后起自动恢复；合成失败抛 `TTSSynthesisError` 触发 sapi 兜底，消除"参考音频静默失败"。

## Impact

- 新增：`utils/kiosk_store.py`、`server/kiosk_routes.py`、`scripts/proximity_detect.py`（`scripts/voice_clone_server.py` 既有）。
- 修改：`desktop_app.py`、`tts/xtts.py`、`data/settings/settings.json`（默认仍 edgetts，未污染）。
- 不引入第三方依赖；存储走本仓忽略路径 `data/`，MUST NOT 入库。
- 不做（见缺口文档 `docs/全息舱适配缺口分析.md`）：商品库、关键词问答库+素材、设备管理下发、阵列麦+AEC、自启/看门狗。

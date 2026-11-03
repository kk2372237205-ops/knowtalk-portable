# 全息舱展项能力（kiosk）

> 回顾性捕获：代码已于 2026-10-31 前实现并通过隔离验证，本任务清单记录已完成的实质工作，并据此补齐规范。

## 1. 存储层

- [x] 1.1 `utils/kiosk_store.py`：唤醒词 `add_wakeword/match_wakeword/load_wakewords`；菜单 `add_menu/match_menu/move_menu/enabled_menus/get_menu`；闲时 `get_idle/set_idle`；接近 `get_proximity/set_proximity`；问答 `append_qa/query_qa/qa_stats/clear_qa/export_qa_csv`
- [x] 1.2 原子写 + `RLock`；`data/kiosk/*.json` 落本仓忽略路径

## 2. 接口层

- [x] 2.1 `server/kiosk_routes.py`：`setup_kiosk_routes(app)` 挂 `/api/kiosk/*`
- [x] 2.2 补 `get_menu` 与 `activate_menu`（按 id + RAG + 播报 + 记 qa）；`list_enabled_menus`

## 3. 接近迎宾

- [x] 3.1 `scripts/proximity_detect.py`：Detector/run/main 守护进程

## 4. 桌面端

- [x] 4.1 导航栏重构、连接/登录流程修复（消除白屏）、按钮 tooltip（`make_btn` + `RoundButton`）
- [x] 4.2 菜单库 BroadcastPage 舞台叠加层 + 全屏叠加层 + 15s 刷新 + 点击激活介绍；MenuPage 预览触发
- [x] 4.3 `ServiceLauncher` 在 `TTS=xtts` 自动拉起 `voice_clone_server.py --port 50000`

## 5. 声音克隆兜底

- [x] 5.1 `tts/xtts.py`：speaker 惰性 + 服务后起恢复；失败抛 `TTSSynthesisError` 触发 sapi 兜底

## 6. 验证

- [x] 6.1 三文件 `py_compile` 全过
- [x] 6.2 隔离验证：唤醒词/菜单/问答/接近接口、菜单上屏点击、克隆服务自启判定均通过
- [x] 6.3 `data/settings/settings.json` 未污染（仍默认 edgetts）

## 7. 缺口（未做，见 docs/全息舱适配缺口分析.md）

- [ ] 7.1 商品库
- [ ] 7.2 关键词问答库 + 素材
- [ ] 7.3 设备管理下发
- [ ] 7.4 阵列麦 + AEC
- [ ] 7.5 自启 / 看门狗

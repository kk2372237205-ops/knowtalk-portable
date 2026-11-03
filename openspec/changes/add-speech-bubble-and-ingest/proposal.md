# 屏幕对话气泡 + 检测器上报放行

## Why

两件现场问题，都出在「配置看起来全对、现场就是没反应」：

1. **人走近了数字人不做动作。** 摄像头检测器是独立进程（`scripts/proximity_detect.py`），
   没有登录态，它 `POST /api/kiosk/proximity/event` 被登录中间件拦成 401。服务端日志里只有
   一行 debug 级的「未登录访问被拦截」，桌面端界面上一切正常——于是现场看到的是
   「感应运行中、检测到有人」，但既没迎宾语也没动作，查不出是感应没触发还是动作没配上。
   同一个 bug 还有一层：`idle_sway` / `wave_one` 两个动作生成完没有 MuseTalk 资产
   （`gestures/<gid>/musetalk/`），加载期抛错后被**静默降级**成默认素材，同样没有动作。

2. **数字人说话时画面里没有字幕。** 数字人占着 9:16 的画面，观众听到的是一整段话，
   但屏幕上看不到任何一个字；展厅环境嘈杂时基本听不清。原来那块「对话浮层」按**画布**
   坐标居中铺在底部，9:16 画面在 16:9 容器里只占中间一条窄栏，浮层一半压在两侧黑边上。

## What Changes

- 登录中间件放行**仅限本机回环地址**的 `POST /api/kiosk/proximity/event`：
  路径命中 + 来源是 `127.0.0.1` / `::1` 才放行，其它 kiosk 接口与外部来源照旧要登录。
- `/api/kiosk/state` 增加 `last_gesture{name,ok,note,at}`：把「动作为什么没播」直接
  回报给桌面端（未配置 / 无会话 / 形象不匹配 / 请求失败），不用再翻日志。
- 动作生成流程末尾自动补齐 MuseTalk 资产（`build_muse_assets`），
  失败只记 warning 不让整次生成判失败。
- 抽出 `bubble_snapshot()`：屏幕气泡显示「谁后开始念的那句」——LLM 流式回答或
  直接朗读的整句（欢迎语 / 菜单介绍），并给出 `bubble_key` 作为「新一轮」的标识。
- 桌面端对话气泡重做：贴在 **9:16 画面矩形**的右下角（不是画布），圆角 + 指向画面
  角落的小尾巴 + 磨砂底；字号与最大宽度随画面宽度收缩；流式追加时末尾带光标，
  超出 5 行只留最后几行；说完且不再出声 8 秒后自动收起。
- 轮询 `/llm_status` 移到后台线程（原来是 UI 线程每 300ms 一次同步 HTTP）。
- 气泡出现时收起端侧菜单条与底部提示语（它们和气泡抢同一块地方）。
- 检测进程日志按 UTF-8 输出（此前 `logs/proximity.log` 全是 `��Ӧ` 乱码）。

## Impact

- 修改：`server/auth_mw.py`、`server/kiosk_routes.py`、`server/routes.py`
  （`bubble_snapshot`）、`avatars/base_avatar.py`（`say_text/say_at`、`put_msg_txt`）、
  `utils/gesture_make.py`（自动补资产）、`utils/numpy_compat.py`（新增）、
  `utils/gestures.py`、`desktop_app.py`（气泡与状态行、检测进程环境变量）、`app.py`。
- 测试：新增 `tests/test_kiosk_ingest_auth.py`、`tests/test_bubble_snapshot.py`；
  扩 `tests/test_kiosk_actions.py`、`tests/test_gesture_muse_assets.py`；`_smoke_kiosk.py`
  增加「有会话时动作必须真的发出去」。
- 数据：`data/avatars/musetalk_ke/gestures/{idle_sway,wave_one}/musetalk/` 已补齐。

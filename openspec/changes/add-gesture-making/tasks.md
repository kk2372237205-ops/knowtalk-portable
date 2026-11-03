# 任务

## 1. 前置核实（先证通路，再写功能）

- [x] 1.1 查 MiniMax H3 文档：`model: MiniMax-H3`、`POST /v2/video_generation`、
      `content[]` 里 `role=first_frame` 传首帧、异步任务 + 查询列表
- [x] 1.2 用真 key 实测：`POST https://api.minimax.cn/v2/video_generation` → 200 `{"task_id": ...}`；
      `GET /v2/query/video_generation?task_id=` 返回 `items[]`，状态值实测是 `running` / `succeeded`，
      成功后 `content.url` 是带签名的 mp4 地址
- [x] 1.3 取回成片（704×1440、124 帧、24fps），跑通 `generate_avatar(img_size=256, pads=[0,10,0,0], pad_scale=0.35)`
      → 124 帧 full_imgs/face_imgs + coords.pkl，用时 58 秒
- [x] 1.4 量头部漂移：`top Δ = 5px`、`left Δ = 2px`（文档口径 ≤4 通过、>8 重生成，实测在既有素材同量级）
- [x] 1.5 抽帧人工核对：同一人、同一套西装、白底，静止 → 双臂抬起「请看这里」→ 回位

## 2. 变更与后端

- [x] 2.1 建 `add-gesture-making`（5 需求 12 场景），`validate --strict` 通过
- [x] 2.2 `utils/gesture_presets.py`：8 套预设（id / 中文名 / 用途 / 完整中文提示词），
      提示词从 `docs/gesture-prompt-pack.md` 用脚本抽取，避免手抄漂移
- [x] 2.3 `utils/minimax_video.py`：建任务 / 查任务（按 id 在 `items[]` 里挑）/ 轮询 / 下载；
      密钥、基址、模型、时长、分辨率全部走设置，未配密钥直接抛 `MinimaxNotConfigured`，不发请求
- [x] 2.4 `utils/gesture_make.py`：作业状态机（queued→submitting→rendering→downloading→converting→done/failed），
      同形象串行（`_active`），失败清理半成品目录，落 `gesture.json`（label=动作名）并预热预览
- [x] 2.5 `normalize_size`：高度一致时偏窄补白边 / 偏宽裁两侧（人脸框同步平移），
      只有高度也不一致才缩放——**修掉一版把所有窄画面都拉伸的写法**（被单测逮到）
- [x] 2.6 `server/gesture_make_routes.py` + `app.py` 注册：
      `GET /api/gestures/presets`、`POST /api/gestures/make`、`GET /api/gestures/make/{id}`、`GET /api/gestures/make?avatar_id=`
- [x] 2.7 设置页新增「动作制作（MiniMax H3）」分组：api_key / base_url / video_model / resolution / duration

## 3. 前端

- [x] 3.1 动作面板顶部加「制作新动作」：预设下拉、名称、提示词、时长、分辨率、开始生成、进度条
- [x] 3.2 选预设自动填名称与提示词，两者都可改（自定义提示词也走同一条路）
- [x] 3.3 每 2.5 秒轮询作业状态；完成即刷新列表；失败显示上游原因；未配密钥时按钮禁用并给设置页链接
- [x] 3.4 打开面板时接上未完成的作业（`resumeMakeJob`），不会因为关掉弹窗丢进度

## 4. 验证

- [x] 4.1 `tests/test_gesture_making.py` **16 项**：预设结构与三条硬约束、建任务载荷、
      未配密钥不发请求、查询按 id 挑、状态映射、归一化补边/不缩放、目录不撞名、busy 409、接口结构
- [x] 4.2 后端全量 **877 passed**（861 + 16）
- [x] 4.3 `openspec validate --all --strict` 全绿
- [x] 4.4 界面截图 `docs/design/gesture-make-ui.png`：预设下拉、名称、提示词、时长/分辨率、开始生成、下方动作列表
- [x] 4.5 实产出一套动作 `open-h3`（124 帧、538×1080、label「掌心向上展示（H3 测试）」），
      与既有 `open` 并列可见；画布手势节点读同一接口，因此也能选到

## 5. 已知边界

- 一次 5 秒 768P 约 2–4 分钟（出片 ~2 分钟 + 切帧 ~1 分钟），按次计费；面板里写明了。
- 只做首帧图生视频，不支持参考视频/参考音频那几种 H3 模式。
- 成片不入库（成功即删），只留拆好的素材；失败时保留 `data/tmp/gesture_make/*.mp4` 便于排查。

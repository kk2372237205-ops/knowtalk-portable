# 任务清单

## 1. 素材侧

- [x] 1.1 手势 MuseTalk 资产布局 `gestures/<gid>/musetalk/{latents.pt, mask_coords.pkl, coords.pkl, mask/}`
- [x] 1.2 `utils/gestures.py`：`has_muse_assets()` / `load_muse_assets()` / `_muse_ready()`；
      `list_gestures` 每项新增 `muse_ready`
- [x] 1.3 `build_muse_assets()`：以**基础形象的恒定框**逐帧 VAE 编码 + 人脸解析
      （不跑关键点检测）；用临时数据目录真实跑通：6 帧 → latents(1,8,32,32) fp16 + 6 张 mask
- [x] 1.4 幂等：已就绪返回 `skipped=True`
- [x] 1.5 只读 `full_imgs/`，不写 `face_imgs/`、`coords.pkl`、`gesture.json`（有测试断言）

## 2. 运行期

- [x] 2.1 `base_avatar._activate()` 支持三元组（wav2lip）/四元组（带模型专属资产）payload；
      新增 `_active_extra`
- [x] 2.2 `musetalk_avatar.inference_batch()`：latent 取当前帧源（`_active_extra['latents']`）
- [x] 2.3 `musetalk_avatar.paste_back_frame()`：原帧/框/遮罩/遮罩框全部取当前帧源
- [x] 2.4 手势播完回落基础素材（沿用 `advance_gesture`）
- [x] 2.5 MusеReal 置 `_requires_muse_assets = True`：缺资产时抛错走
      `_load_then_activate` 的降级分支（记 warning、改用默认素材继续朗读）

## 3. 接口

- [x] 3.1 `GET /api/avatars/{aid}/gestures` 每项含 `muse_ready`
- [x] 3.2 `POST /api/avatars/{aid}/gestures/{gid}/prepare`（线程池执行，幂等；404/400 齐备）
- [x] 3.3 受理阶段 `partition_gestures` 按 `ACTIVE_MODEL` 判定：musetalk 下缺资产的手势
      计入 missing → 摘除该段手势 + 回 `data.warnings`，朗读照常

## 4. 验证

- [x] 4.1 单元：`tests/test_gesture_muse_assets.py` 8 项（就绪判定 / 读取 / 幂等 /
      不动 wav2lip 素材 / 模型感知分区）；同步 `tests/test_gestures.py` 两处字段断言
- [x] 4.2 真实补齐链路（临时目录）：构造 6 帧手势 → 补齐 → 读出 6 组资产 → 幂等 skip
- [x] 4.3 真机播放：MiniMax 4 秒动作素材 + aiortc 打 `/offer` → 朗读期间手臂区域
      与静止帧差异 25% 帧超阈值（空闲最大 3.58 / 朗读峰值 18.06）→ **动作确实播出**，
      且推流画面里嘴仍在动（MuseTalk 贴嘴在动作帧上依然生效）
- [x] 4.4 回归：手势相关 118 项全过；后端全量 930 passed
- [x] 4.5 生成 6 套动作（`count` / `open` / `compare` / `zoom` / `press` / `thanks`），
      每套 4 秒 → 107 帧 538×1080，均已补齐 MuseTalk 资产（`muse_ready=true`）
- [ ] 4.6 用 `docs/课程脚本-精简带动作.txt` 走一遍完整讲课（9 次动作，6 个去重 id）
      —— 待用户真机确认；自动化侧已验证帧数/时长/逐帧对齐（见 7.5）

## 5. 途中修掉的阻塞

- [x] 5.1 `utils/gesture_make.py`：人脸检测批大小 16 → 4（`FACE_DET_BATCH`）。
      16 帧 1080×528 转 int64 要一块 209MB 连续内存，在 torch+CUDA 已占内存的进程里
      直接 `Unable to allocate 209 MiB`，且它抛的是 numpy 的 `ArrayMemoryError`
      （不是 `RuntimeError`），genavatar 自带的「批减半重试」接不住
- [x] 5.2 `avatars/musetalk/models/unet.py`：权重先落 CPU、参数先转 fp16 再搬卡。
      原来 `torch.load(model_path)` 直接进显存，加上 fp32 参数两份同时在卡上，
      启动峰值 6845MiB（8G 卡只剩 27MiB），服务启动期会随机 OOM；改后峰值 1957MiB
- [x] 5.3 批量生成按「一套一进程」：单套拆帧峰值约 1.5GB（107 帧原图 + int64 中间数组），
      同进程连做第二套必然失败

## 6. 文档

- [x] 6.1 `docs/gesture-mode-manual-test.md`：新增 T7「动作按原速播放」验收项；
      同时按 2026-10-26 的新口径改写 T5（缺手势只摘除 + warning，不再整篇拒绝）
- [x] 6.3 `docs/gesture-mode-manual-test.md`：新增 T10「动作期间脸型/身形与形象一致」验收项
- [ ] 6.2 `openspec validate --all --strict` 通过后归档

## 7. 动作按原始速度播放（2026-10-27）

> 行为规格落在未归档变更 `add-gesture-module` 的 `gesture-orchestration`
> （Requirement: 手势按素材原始时长播放）——播放速度属编排语义，不在本变更重复声明。

- [x] 7.1 `utils/gestures.py`：`playback_indices()` 按时间轴重采样 +
      `gesture_source_fps()`（`gesture.json` 的 `fps` 优先，缺失/非法回退 24fps）
- [x] 7.2 `load_gesture()` / `load_muse_assets()` 接受 `target_fps`，画面帧 / 人脸裁剪 /
      人脸框 / latent / 遮罩 / 遮罩框共用同一套下标
- [x] 7.3 `base_avatar._load_gesture_payload()` 传 `opt.fps`；
      `gesture_make` 出素材时用 `cv2` 读源视频帧率写进 `gesture.json`
- [x] 7.4 现有 6 套手势的 `gesture.json` 补 `fps: 24`
- [x] 7.5 单元测试 `tests/test_gesture_fps_normalize.py` 14 项；
      真机素材实测：107 帧@24fps → 89 帧@20fps，四类资产长度一致，播放时长 4.45s（= 素材时长）


## 8. 动作期间的人脸恒等于形象（2026-10-27）

> 用户反馈：「动作的身形跟默认的数字人身形不太一样，总有点胖瘦变化，从脸看特别明显」。

- [x] 8.1 量化：`D:/tmp/probe_face_align.py` 用 68 点算人脸框 —— 六套动作与形象的框
      宽高差 1%~5%、位移 <4px（**构图没问题**，是框内脸型漂移：动作帧明显更宽/更圆）
- [x] 8.2 验证：`D:/tmp/probe_face_swap.py` 把形象的脸贴回动作帧，`get_image_blending`
      融合后无残影、脖子接缝可接受 → 固定 latent 路线可行
- [x] 8.3 `utils/gestures.py`：新增 `_load_avatar_face_assets()`（取形象第 0 份
      latent / 遮罩 / 遮罩框 / 人脸框）；`build_muse_assets()` 改为写**共享一份**资产
      + `asset.json`（`mode: shared`），不再逐帧跑 VAE 编码与 face parsing
- [x] 8.4 `_muse_ready()` 认 `asset.json`：旧版逐帧资产判为未就绪 → prepare 自动迁移
      （清掉旧 `mask/`，改写为 1 份）
- [x] 8.5 现有 6 套动作已迁移（各 1 份，`muse_ready=true`，latent 与形象逐位相同）
- [x] 8.6 注释同步：`base_avatar._load_gesture_payload`、`musetalk_avatar.__init__`、
      `avatar_routes.prepare_gesture_assets`（不再"逐帧/数十秒"）
- [x] 8.7 测试 `tests/test_gesture_muse_assets.py` 18 项（新增 `SharedFaceAssetsTest`）；
      `test_gesture_fps_normalize.py` 的 Muse 资产断言改为「恒 1 份」
- [x] 8.8 规格：新增 Requirement「动作期间的人脸恒等于形象」+ 3 场景；「资产可补齐」
      补「旧版逐帧资产自动迁移」场景；proposal 增补「后续调整」一节
- [ ] 8.9 真机确认：重启后播一遍精简讲稿，动作全程脸型应与静止时一致（T10）

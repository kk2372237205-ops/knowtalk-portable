# Tasks

## 1. 手势素材的发现与加载

- [x] 1.1 新增 `utils/gestures.py`：`list_gestures(avatar_id)` 扫描 `data/avatars/{avatar_id}/gestures/*/`，只保留三件套齐备（`coords.pkl` 存在、`face_imgs/` 与 `full_imgs/` 均有图片）的手势，显示名取 `gesture.json` 的 `label`，缺失时回退为目录名，按 id 排序返回。验证：单元测试覆盖"齐备""缺 coords""face_imgs 为空""无 gestures 目录""只含目录名非法的项"五种输入
- [x] 1.2 手势标识合法性校验 `is_valid_gesture_id`：只接受 `A-Za-z0-9_.-`，拒绝路径分隔符与空格。验证：单元测试覆盖合法、含 `/`、含 `\`、含空格、含 `..`、空串
- [x] 1.3 `load_gesture(avatar_id, gesture_id, ref_size, ref_face_size)` 读取三件套；尺寸与默认素材不一致时把 `full_imgs`/`face_imgs` 归一化到参考尺寸、`coords.pkl` 按同比例缩放，并记一条警告。验证：单元测试喂入一套与参考尺寸不同的素材，断言输出尺寸等于参考尺寸、coords 被等比缩放、警告被记录
- [x] 1.4 确认手势目录不进入版本库（素材是本地数据）。验证：`git check-ignore` 命中 `data/avatars/*/gestures/` 下的文件（命中 `.gitignore:26:/data/`）

## 2. 讲稿分段解析扩展

- [x] 2.1 把 `server/routes.py` 的分段解析扩展为同时识别 `[PPT:N]`/`[slide:N]` 与 `[GESTURE:<id>]`（标签名大小写不敏感），段落结果从 `(text, slide_id)` 变为 `(text, slide_id, gesture)`；`_parse_ppt_segments` 保留为只取前两个字段的薄包装，既有调用点不改。验证：单元测试覆盖"两类混用""只有 PPT""只有手势""无标签"四种输入
- [x] 2.2 段内出现多个手势标签时取第一个、其余一并从朗读文本移除；未带标签的段落手势为空（不继承上一段）。验证：单元测试断言每段的 gesture 取值与清洗后文本
- [x] 2.3 不含手势标签时，分段结果必须与改动前逐项相同（含"整套讲稿重复粘贴只播最后一套"的截断逻辑）。验证：现有 PPT 分段用例全绿，并新增一条"含手势标签的讲稿与等价的纯 PPT 讲稿产出相同 slide_id 序列"的等价性断言

## 3. 运行时帧源切换

- [x] 3.1 `BaseAvatar` 引入 `_active_frames`/`_active_faces`/`_active_coords` 与默认三件套引用，`paste_back_frame`、静音分支、补帧分支一律从 `_active_*` 取帧。验证：单元测试断言未使用手势时 `_active_*` 与默认三件套指向同一对象，且三条取帧路径都读 `_active_*`（静音/补帧分支在渲染线程内，无法在单元层执行，故对其接线做源码断言并在测试中标注）
- [x] 3.2 实现 `request_gesture(name)` 与 `_activate(name)`，以及帧源代数 `_frame_source_gen`；`inference()` 检测代数变化时把 `index` 重置为 0，并把 `length` 的取值移入 while 循环。验证：单元测试断言切换后产出的首个索引对应手势第 0 帧，且 `length` 随帧源长度更新（用 5 帧手势断言在第 6 个产出帧处回绕，而非按默认 150 帧）
- [x] 3.3 实现"只完整播一遍"：用独立计数器 `_gesture_frames_played` 按实际产出的帧数累加（静音与说话两条分支都累加），达到手势帧数后自动 `_activate(None)` 回到默认素材。验证：单元测试断言播满帧数后 `_active_*` 回到默认；并断言该计数器与 `index` 无关（人为重置 `index` 不影响计数）
- [x] 3.4 索引安全：所有按帧号取帧统一 `idx % len(...)`，保证两套素材帧数不同时切换窗口内不越界。验证：单元测试用 150 帧与 121 帧两套素材模拟"旧索引 + 新帧源"，断言不抛异常且输出落在合法范围
- [x] 3.5 后台加载与缓存：`preheat_gestures(names)` 在 daemon 线程加载（**只加载、不激活**，见下方说明），`OrderedDict` 做 LRU，上限 2 套，会话结束清空。验证：单元测试断言同一手势第二次触发不重复读盘（用读盘计数替身）、超过上限后最久未用被淘汰、`clear_gestures()` 后缓存为空
- [x] 3.6 运行时降级：素材在入队后变得不可读取时改用默认素材并记录警告，渲染线程不退出。验证：单元测试断言读取抛错时 `_active_*` 为默认、有警告记录、循环未中断

> 3.5 的措辞校正：原任务写"加载完成后激活"。规格要求手势**在该段音频开始播放的时刻**才切换（`gesture-orchestration` 的"段首切换并从首帧开始"），因此预热只加载进缓存、不激活；"加载完成后激活"只适用于段首请求（`request_gesture`）这条路径。实现按规格执行。

## 4. 接口

- [x] 4.1 新增 `GET /api/avatars/{avatar_id}/gestures`，返回 `data.gestures = [{"id", "label"}, ...]`，顺序稳定；非法 `avatar_id` 与不存在的形象返回业务错误，无手势返回成功与空数组。验证：`tests/test_gesture_routes.py` 覆盖四种情形并断言非法 id 时不触碰文件系统
- [x] 4.2 `POST /human` 的 echo 模式在入队前校验文本中的手势标签：**逐个区分可用与不可用**。可用的照常下发给渲染层；不可用的 MUST 从该段手势指令中摘除（该段按默认素材渲染），MUST NOT 整篇报错；MUST 记录一条给出不可用手势名与该形象可用手势列表的警告，并在响应体里带上该警告（`data.warnings`），MUST NOT 静默降级。验证：单元测试断言不可用手势时响应仍为 `code=0`、该段文本照常入队且不带 `gesture`、同篇中可用手势照常生效、响应里带不可用手势名与可用列表
- [x] 4.3 校验通过后对本次用到的手势触发一次后台预热；预热失败不影响本次响应成功。验证：单元测试断言预热被调用一次、且预热抛错时响应仍为成功

## 5. 前端

- [x] 5.1 `web/index.html` 在"预设文本"区新增手势标签按钮组，点击把 `[GESTURE:<id>]` 插入 `textarea` 的光标/选区位置；没有可用手势时显示提示而不是留空。验证：`tests/js/gesture-tags.test.js` 断言插入到选区中间、光标落点正确、空列表时的提示文案
- [x] 5.2 手势列表随形象下拉变化刷新（切换形象时用新 `avatar_id` 重新请求并重渲染按钮）。验证：JS 单元测试断言切换形象后请求参数变化且按钮被重建
- [x] 5.3 展示页 MUST 把 `/human` 返回的 `data.warnings` 显示给用户（复用 `#statusBanner` 的 warn 样式），MUST NOT 因为是「预设讲稿静默提交」而把警告丢掉。验证：JS 单元测试断言带 warnings 的响应会在 banner 上出现提示文案

## 6. 素材迁移与集成验证

- [x] 6.1 迁移素材：`data/avatars/Mr.Chen_gesture/` 的 `full_imgs`、`face_imgs`、`coords.pkl` 移到 `data/avatars/Mr.Chen/gestures/explain/`，写入 `gesture.json`（`{"label": "讲解手势"}`），并核对迁移后三件套帧数一致。验证：`GET /api/avatars/Mr.Chen/gestures` 返回 `explain` 且 `GET /api/avatars` 不再包含 `Mr.Chen_gesture`（实测：三件套各 121 帧；`list_gestures('Mr.Chen')` 返回 `[{'id':'explain','label':'讲解手势'}]`；形象目录只剩 `Mr.Chen`；`load_gesture` 把 536×1080 归一化到 538×1080 且人脸框等比缩放）
- [ ] 6.2 端到端人工走查（需真实浏览器 + WebRTC 会话）：带手势讲稿能触发、手势播完自动回默认、朗读不断流不重复、输出分辨率全程不变；不含手势标签的讲稿与改动前表现一致。验证：走查清单写入 `docs/gesture-mode-manual-test.md` 并逐项记录结果（**清单已写就绪，等待人工执行并回填**）
- [x] 6.3 文档：README 新增手势一节（素材目录约定、"视频→手势素材"的产出方式、`[GESTURE:id]` 语法、"只播一遍后回默认"的语义、查询接口），`docs/README.md` 补索引。验证：文档中的字段名、接口路径与默认值逐项对照实际实现核对一致

## 7. 手势按素材原始时长播放（2026-10-27 追加）

- [x] 7.1 `utils/gestures.py`：`playback_indices()` + `gesture_source_fps()`；`load_gesture` /
      `load_muse_assets` 接 `target_fps`，所有逐帧素材共用同一套下标
- [x] 7.2 `avatars/base_avatar._load_gesture_payload()` 传 `opt.fps`；
      `utils/gesture_make.py` 用 cv2 读源视频帧率写进 `gesture.json` 的 `fps`
- [x] 7.3 测试 `tests/test_gesture_fps_normalize.py`（14 项）+ 真机素材实测
      107 帧@24fps → 89 帧@20fps、播放 4.45s（= 素材时长）
- [x] 7.4 `docs/gesture-mode-manual-test.md` 新增 T7 验收项；T5 按「只摘除不整篇报错」的新口径改写

## 8. 动作播放流畅性（2026-10-27 追加）

> 现象：带动作时经常卡顿。日志实证：① 讲到第 3 个动作时才去读盘，触发到真正切换晚 1.9 秒；
> ② 缓存只有 2 套、讲稿有 6 套动作 → 讲到中间反复"淘汰—重载"；③ 推理约 18.6fps、输出 20fps，
> 缺帧时画面被冻结 → 手臂一顿一顿。

- [x] 8.1 全帧改**按需解码**（`utils/gestures.py::LazyFrames`）：只保留最近几帧，
      单套素材常驻从约 180MB 降到约 24MB（实测 6 套共 +144MB）
- [x] 8.2 `GESTURE_CACHE_LIMIT` 2 → 8；`preheat_gestures` 预热讲稿用到的**全部**手势
      （去重保序），并由**单线程顺序**完成，避免并发解码抢 CPU
- [x] 8.3 补位帧改为"下一帧动作画面 + 贴回上一张已输出画面的人脸"
      （`base_avatar.splice_face` / `_face_rect`），既不停手也不闪嘴；补位计入手势进度
- [x] 8.4 每 200 帧打一条 `[平滑] 近 N 帧中补帧 M 帧（x%）`，卡顿时可量化归因
- [x] 8.5 测试 `tests/test_gesture_lazy_frames.py` 18 项（含**真跑 process_frames** 的补位用例）；
      后端全量 **963 passed**
- [ ] 8.6 真机复测：重启服务后用 `docs/课程脚本-精简带动作.txt` 走一遍，确认动作不再一顿一顿、
      日志里 `[平滑]` 的补帧率与 `[手势] 开始预热 6 套动作` 均出现

## 9. 动作收尾卡顿（2026-10-27 追加）

> 现象：卡顿集中在**某个动作快结束时**。日志实证（08:21:31）：
>
> ```
> [手势] count 已完整播放一遍（89 帧），回到默认素材
> 帧源已切换，索引重置为 0（当前手势：None）
> paste_back_frame error: list index out of range      × 16
> audio queue underrun: count=1, queued=0              × 数十条
> ```
>
> 根因三层：① `MuseReal.paste_back_frame` 非手势分支用**裸下标**，而默认素材只有 1 帧、
> 手势 89 帧 → 切回瞬间在途帧逐帧越界；② 越界被 `except` 吞掉后该帧**视频与音频一起丢弃**
> → 接收端欠载；③ 手势进度由推理线程按批量产出推进，超前于画面 → 切换早于末帧动作。

- [x] 9.1 `MuseReal.paste_back_frame` / `LightReal.paste_back_frame` 全部索引改取模
      （含原帧、人脸框、遮罩、遮罩框、人脸裁剪），序列为空时返回 None 而非抛错
- [x] 9.2 渲染线程贴回失败不再 `continue` 丢帧：退化为输出当前帧源的合法画面，
      视频与音频照常推送（避免 `audio queue underrun`）
- [x] 9.3 手势进度只由渲染线程按**实际输出帧**推进；删除推理线程里 3 处 `advance_gesture`，
      暂停期间不推进
- [x] 9.4 手势期间帧索引改用渲染线程自己的 `_gesture_frames_played`，不再取推理传来的 `idx`
      （补位推进的帧会被推理 idx 回写倒回去，动作末尾"回抽"）
- [x] 9.5 补位分支补上 `_last_out_frame` 更新，并引入 `_last_out_gen`：切源后的补位按新帧源
      取帧 + 贴回旧人脸，不再把上一套素材的姿态钉在屏幕上
- [x] 9.6 测试 `tests/test_gesture_tail_handover.py` 14 项（含真跑 `process_frames` 的
      帧数/末帧/不丢帧/跨源补位用例）；后端全量 **977 passed**
- [x] 9.7 真机素材探针：默认 1 帧 + 手势 89 帧，切回后在途索引 0..96 贴回**零失败**
- [ ] 9.8 真机复测：重启服务后跑一遍精简讲稿，日志 MUST NOT 再出现
      `paste_back_frame error: list index out of range`

## Workflow follow-up

- 变更归档前，确认 `docs/gesture-mode-manual-test.md` 的 6.2 走查项已由人工执行并回填结果。
- 归档后同步主规格并复用 `add-quiz-mode` 的归档流程。

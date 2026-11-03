# Proposal

## Why

数字人讲课时画面里的身体姿态全程不变：素材帧序列是静止循环（`data/avatars/Mr.Chen/full_imgs` 150 帧，第 0 帧与第 75 帧完全一致），wav2lip 只重绘嘴部再贴回原帧，手、肩、身体一律沿用静音帧。结果是一节课从头到尾"只有嘴在动"，观感僵硬。

项目其实已经具备所需的两个前提：一是「视频 → 素材」的本地通路（`genavatar.generate_avatar` + `web/avatar.html`），二是已经产出一套可用的手部动作素材（`data/avatars/Mr.Chen_gesture`，121 帧，双手从垂放抬到胸前比划再放下）。缺的是把它们接进朗读流程的机制：既无法声明"这个形象有哪些手势"，也无法在讲稿里指定"讲到这句时做哪个动作"。

现有的 `customopt` / `set_audiotype` 机制不能用来解决这个问题——它在 `custom_audiotype > 1` 时**优先吐自定义音频**（`avatars/audio_features/base_asr.py:107`），即"停下来做一段动作"，无法一边说话一边动。手势必须走另一条路：直接替换渲染所用的帧序列。

## What Changes

- **手势素材目录约定**：手势作为形象的附属素材，放在 `data/avatars/{avatar_id}/gestures/{gesture_id}/` 下，含完整三件套（`full_imgs/`、`face_imgs/`、`coords.pkl`），可选 `gesture.json` 提供显示名。
- **讲稿标签 `[GESTURE:name]`**：与现有 `[PPT:N]` 标签同一层解析，在标签处切段；段落携带手势标识，随音频帧下发到渲染层。
- **运行时切换帧源**：段首开始朗读时把手势的三件套换成当前帧源，从手势第 0 帧起**只播一遍**，播完后自动恢复到默认素材。未插标签的段落行为与现在完全一致。
- **切换不得阻塞渲染**：手势素材在后台线程加载完成后原子替换，加载期间继续播当前素材，画面不断流。
- **手势列表接口**：新增按形象查询可用手势的接口，供前端渲染标签按钮。
- **讲稿校验**：`POST /human` 的 echo 模式在入队前校验手势标签引用的手势是否可用，不可用时以业务错误指明名称与可选列表，避免打错字静默失效。
- **前端插入入口**：在"预设文本"旁提供手势标签按钮组，点击把 `[GESTURE:name]` 插入光标处；手势列表随所选形象刷新。
- **BREAKING（数据布局）**：已产出的 `data/avatars/Mr.Chen_gesture` 从「独立形象」登记为 `data/avatars/Mr.Chen/gestures/explain/`。形象下拉由 `/api/avatars` 动态生成，因此 `Mr.Chen_gesture` 不再作为独立形象出现——它本就是同一身份的动作素材，不应被当作另一个人。

## Capabilities

### New Capabilities

- `gesture-orchestration`: 数字人手势编排——手势素材的组织与发现、讲稿中的手势标签语义、运行时帧源切换与恢复、切换期间的画面连续性与失败降级。

### Modified Capabilities

- `digital-human-api`: `POST /human` 的 echo 模式在解析 `[PPT:N]` 之外还要识别并校验 `[GESTURE:name]`；新增「按形象列举可用手势」的查询接口。

## Impact

**代码**

- `server/routes.py`：脚本分段解析（扩展为识别两类标签）、echo 模式的手势校验
- `avatars/base_avatar.py`：帧源切换与恢复、切换后的索引安全、异步加载
- `avatars/wav2lip_avatar.py`：取帧接口随帧源切换保持索引一致（`paste_back_frame` 等）
- `server/avatar_routes.py`：手势列表接口
- `web/index.html`：手势标签按钮组与形象联动

**数据**

- 新增 `data/avatars/Mr.Chen/gestures/explain/`（由现有 `Mr.Chen_gesture` 迁移而来）
- `data/avatars/` 一级目录不再包含 `Mr.Chen_gesture`

**不影响**

- 不含手势标签的讲稿、`customopt` 抢占式动作、提问模式、PPT 切页时序、TTS 与判分链路均保持不变
- 会话内存：单手势素材约 210 MB（121 帧 × 536×1080），按需加载并有上限，不会因手势数量线性增长

**测试**

- Python 单测：分段解析、标签校验、帧源切换与恢复、索引安全
- JS 单测：标签按钮插入行为、形象切换时手势列表刷新

# 演讲手势提示词包（通用版 · 头部完全锁死）

> 用于「单帧图 → 图生视频（Seedance / 即梦 / 可灵）→ `genavatar` → `data/avatars/{id}/gestures/{gid}/`」这条既有通路。
> 与平台已下线的「Seedance 手势视频」功能内置提示词（`utils/seedance_video.py::GESTURE_PROMPTS`，2026-10-24 随功能下线删除，历史见 `openspec/changes/archive/`）的区别：**性别中立**（原文写死 `She`）、**头部零动作**（本次硬约束）、**补齐回位与禁动**、**多出 3 个常用动作**。
> 目标形象示例：`Mr.Chen`（538×1080 白底全身正面站姿）。
> **8 条提示词均为完整可直接粘贴版**，不含需要手工拼接的省略号。

---

## 一、头部零动作（本次最硬的约束）

### 为什么必须零动作

`genavatar` 会逐帧写 `coords.pkl`，那个 bbox 就是 wav2lip **贴嘴的位置**。头一动 → bbox 动 → 嘴贴错位、画面抖。这一条比"手好不好看"重要一个量级。

### 提示词里的写法（关键是"零"和"像照片"）

```
The head is completely locked: zero head movement of any kind — no nodding, no shaking,
no turning, no tilting, no lifting or lowering the chin, no head bob, no micro movement.
The head position and angle stay pixel-identical from the first frame to the last frame,
as if the head were a still photograph.
The neck and shoulders do not shift in response to the arm movement, and the facial
expression does not change.
```

中文版：

```
头部完全锁死：全程零头部位移。不点头、不摇头、不转头、不歪头、不抬头、不低头、不晃头，
连轻微晃动都不允许。头部的位置和角度从第一帧到最后一帧保持完全一致，就像一张静止照片。
颈部与肩膀不因手臂动作而产生连带位移，面部表情不变。
```

### 三处容易被忽略的漏口

| 漏口 | 模型会怎么钻 | 已加的对策 |
|---|---|---|
| 只说 "head still" 不够 | 它理解成"别大幅点头"，仍会跟着呼吸微晃 | 加 `zero head movement of any kind` + `micro movement` |
| 手臂一抬，脖子肩膀跟着偏 | bbox 横向漂移（最致命） | 加 `neck and shoulders do not shift in response to the arm movement` |
| 表情/眨眼在动 | 脸框跟着跳 | 加 `facial expression does not change` |

**代价**：头完全不动会略僵。但手势只播一遍、幅度又小，这点僵硬远不如嘴抖显眼——先保住嘴。

### 验收：直接量 bbox 漂移

```python
import pickle
c = pickle.load(open('data/avatars/Mr.Chen/gestures/frame/coords.pkl', 'rb'))
ys = [b[0] for b in c]   # y1 上边
xs = [b[2] for b in c]   # x1 左边
print('top  Δ =', max(ys) - min(ys), 'px')
print('left Δ =', max(xs) - min(xs), 'px')
```

- `Δ ≤ 4px` → 通过（现有 `explain` 首末帧差 5px，属可接受量级）
- `Δ > 8px` → 头在动，重生成这条

---

## 二、其它三条硬约束

| 约束 | 原因 | 提示词写法 |
|---|---|---|
| **嘴必须闭合** | wav2lip 会重绘嘴部，素材自己动嘴 = 双重口型 | `mouth stays closed, no lip movement, no teeth showing` |
| **手完整入镜、不遮脸** | 全身竖构图，手一抬高就出画 | `hands stay fully inside the frame and never cover the face` |
| **首尾姿态接近** | 手势只播一遍后切回静止帧 | `returns to rest at the side, ending in exactly the same pose` |

**幅度宁小勿大**：幅度一大，图生视频就开始糊手、粘指、长第六根指头。

---

## 三、骨架公式（自己加新动作时套这个）

```
① 镜头锁定  A steady locked-off frontal full-body shot ... both feet visible.
② 动作主体  <一句话，一个动作，含"起手 → 动作 → 保持">
③ 回位      Then the hand returns to rest at the side, ending in exactly the same pose as the start.
④ 嘴        The mouth stays closed with lips together, no speaking, no lip movement, no teeth showing.
⑤ 头        The head is completely locked: zero head movement of any kind ... (见 §一)
⑥ 躯干      Torso and legs stay fixed, feet planted. Only the arm and hand move.
⑦ 收尾      Hands stay fully inside the frame ... Locked camera, no zoom, no pan.
```

**⑤ 是本次重点，一个字都别省。** 顺序别改：动作描述在前、禁动约束在后，模型对末尾约束的遵守度更高。

---

## 四、8 个主要演讲动作（完整可粘贴）

| id | 中文名 | 讲稿里什么时候用 | 风险 |
|---|---|---|---|
| `open` | 掌心向上展示 | 引出结论、"给大家看这个" | 低 |
| `point` | 指向引导 | 配合 PPT 某处、指方向 | 中（用整掌，别用单指） |
| `count` | 手指点数列举 | 讲"第一、第二、第三" | **高**（手指粘连） |
| `chop` | 切砍强调 | 强调判断、下结论 | 低 |
| `compare` | 左右交替对比 | 讲"A 和 B 的区别" | 中 |
| `frame` | 双手框定范围 | 讲"范围 / 结构 / 流程 / 尺寸" | 低 |
| `press` | 双掌下压 | 讲"降低 / 收敛 / 一步一步来" | 低 |
| `invite` | 侧向邀请 | 讲"请看 / 大家注意" | 中（易带动转身） |

---

### 1. `open` 掌心向上展示（最万用）

**EN**
```
A steady locked-off frontal full-body shot of a digital-human presenter standing in a bright
white studio, identical camera framing, lighting, wardrobe and background to the reference
image, both feet visible. The presenter raises both forearms to waist height with the palms
turned up toward the audience, opens them slightly outward in a calm "here it is" presenting
gesture, and holds for a beat. Then the hands return to rest at the sides, ending in exactly
the same pose as the start. The mouth stays closed with lips together, no speaking, no lip
movement, no teeth showing. The head is completely locked: zero head movement of any kind,
no nodding, no shaking, no turning, no tilting, no lifting or lowering the chin, no head bob,
no micro movement. The head position and angle stay pixel-identical from the first frame to
the last frame, as if the head were a still photograph. The neck and shoulders do not shift in
response to the arm movement, and the facial expression does not change. Torso and legs stay
fixed, feet planted, no walking and no weight shift. Only the arms and hands move, with subtle
natural breathing in the chest only. Hands stay fully inside the frame and never cover the
face. Locked camera, no zoom, no pan, no crop change. Realistic, high quality, consistent
identity throughout.
```

**中文**
```
镜头完全固定，正面全身像，白底影棚，构图/光线/服装/背景与参考图完全一致，双脚完整入画。
人物双前臂抬到腰腹高度，掌心向上朝向观众，略微向外摊开，做出"请看这里"的展示手势，
停顿一拍后双手自然回到体侧，末帧与首帧姿态完全一致。全程嘴巴闭合、不说话、无口型动作、不露齿。
头部完全锁死：全程零头部位移。不点头、不摇头、不转头、不歪头、不抬头、不低头、不晃头，
连轻微晃动都不允许；头部的位置和角度从第一帧到最后一帧保持完全一致，就像一张静止照片；
颈部与肩膀不因手臂动作而产生连带位移，面部表情不变。躯干与双腿固定，双脚站定，不走动、
不重心偏移。只有手臂和手在动，仅胸腔有轻微自然呼吸。双手始终完整在画面内且不遮挡面部。
镜头锁死，不推拉不摇移，不做画面裁剪变化。写实风格，高画质，人物身份全程一致。
```

---

### 2. `point` 指向引导

> 用**整只手掌**指，不要用单指——单指几乎必糊、易粘连。

**EN**
```
A steady locked-off frontal full-body shot of a digital-human presenter standing in a bright
white studio, identical camera framing, lighting, wardrobe and background to the reference
image, both feet visible. The presenter raises the right arm to chest height and extends it
forward and slightly to the side, with the hand open and the palm angled up, as if guiding the
audience's attention to a slide beside the presenter, and holds the point for a beat. Then the
arm returns to rest at the side, ending in exactly the same pose as the start. The mouth stays
closed with lips together, no speaking, no lip movement, no teeth showing. The head is
completely locked: zero head movement of any kind, no nodding, no shaking, no turning, no
tilting, no lifting or lowering the chin, no head bob, no micro movement. The head position and
angle stay pixel-identical from the first frame to the last frame, as if the head were a still
photograph. The neck and shoulders do not shift in response to the arm movement, and the facial
expression does not change. Torso and legs stay fixed, feet planted, no walking and no weight
shift. Only the right arm and hand move, with subtle natural breathing in the chest only. Hands
stay fully inside the frame and never cover the face. Locked camera, no zoom, no pan, no crop
change. Realistic, high quality, consistent identity throughout.
```

**中文**
```
镜头完全固定，正面全身像，白底影棚，构图/光线/服装/背景与参考图完全一致，双脚完整入画。
人物抬起右臂到胸口高度，向前并略偏向一侧伸出，手掌张开、掌心斜向上，
像在引导观众看身旁的幻灯片，保持指引姿势一拍后手臂收回体侧，末帧与首帧姿态完全一致。
全程嘴巴闭合、不说话、无口型动作、不露齿。头部完全锁死：全程零头部位移。不点头、不摇头、
不转头、不歪头、不抬头、不低头、不晃头，连轻微晃动都不允许；头部的位置和角度从第一帧到
最后一帧保持完全一致，就像一张静止照片；颈部与肩膀不因手臂动作而产生连带位移，
面部表情不变。躯干与双腿固定，双脚站定，不走动、不重心偏移。只有右臂和手在动，
仅胸腔有轻微自然呼吸。双手始终完整在画面内且不遮挡面部。镜头锁死，不推拉不摇移，
不做画面裁剪变化。写实风格，高画质，人物身份全程一致。
```

---

### 3. `count` 手指点数列举

> 手指是全套动作里最容易崩的（粘指、六指）。生成崩了就退化成 `chop` 或 `press`，语义损失很小。

**EN**
```
A steady locked-off frontal full-body shot of a digital-human presenter standing in a bright
white studio, identical camera framing, lighting, wardrobe and background to the reference
image, both feet visible. The presenter counts three points on the right hand at a slow even
pace while keeping the hand at chest height and the amplitude small: index finger, then index
and middle finger, then three fingers. Then the hand returns to rest at the side, ending in
exactly the same pose as the start. The mouth stays closed with lips together, no speaking, no
lip movement, no teeth showing. The head is completely locked: zero head movement of any kind,
no nodding, no shaking, no turning, no tilting, no lifting or lowering the chin, no head bob,
no micro movement. The head position and angle stay pixel-identical from the first frame to the
last frame, as if the head were a still photograph. The neck and shoulders do not shift in
response to the arm movement, and the facial expression does not change. Torso and legs stay
fixed, feet planted, no walking and no weight shift. Only the right hand and fingers move, with
subtle natural breathing in the chest only. Hands stay fully inside the frame and never cover
the face. Locked camera, no zoom, no pan, no crop change. Realistic, high quality, consistent
identity throughout.
```

**中文**
```
镜头完全固定，正面全身像，白底影棚，构图/光线/服装/背景与参考图完全一致，双脚完整入画。
人物在胸口高度用右手缓慢均匀地点数三点：先伸食指，再伸食指和中指，最后伸出三根手指，
幅度小、节奏稳，然后手收回体侧，末帧与首帧姿态完全一致。全程嘴巴闭合、不说话、无口型动作、
不露齿。头部完全锁死：全程零头部位移。不点头、不摇头、不转头、不歪头、不抬头、不低头、
不晃头，连轻微晃动都不允许；头部的位置和角度从第一帧到最后一帧保持完全一致，
就像一张静止照片；颈部与肩膀不因手臂动作而产生连带位移，面部表情不变。
躯干与双腿固定，双脚站定，不走动、不重心偏移。只有右手和手指在动，仅胸腔有轻微自然呼吸。
双手始终完整在画面内且不遮挡面部。镜头锁死，不推拉不摇移，不做画面裁剪变化。
写实风格，高画质，人物身份全程一致。
```

---

### 4. `chop` 切砍强调

**EN**
```
A steady locked-off frontal full-body shot of a digital-human presenter standing in a bright
white studio, identical camera framing, lighting, wardrobe and background to the reference
image, both feet visible. The presenter makes one calm downward chopping motion with the edge
of the right hand at chest height, small amplitude, a single stroke. Then the hand returns to
rest at the side, ending in exactly the same pose as the start. The mouth stays closed with
lips together, no speaking, no lip movement, no teeth showing. The head is completely locked:
zero head movement of any kind, no nodding, no shaking, no turning, no tilting, no lifting or
lowering the chin, no head bob, no micro movement. The head position and angle stay
pixel-identical from the first frame to the last frame, as if the head were a still photograph.
The neck and shoulders do not shift in response to the arm movement, and the facial expression
does not change. Torso and legs stay fixed, feet planted, no walking and no weight shift. Only
the right arm and hand move, with subtle natural breathing in the chest only. Hands stay fully
inside the frame and never cover the face. Locked camera, no zoom, no pan, no crop change.
Realistic, high quality, consistent identity throughout.
```

**中文**
```
镜头完全固定，正面全身像，白底影棚，构图/光线/服装/背景与参考图完全一致，双脚完整入画。
人物在胸口高度用右手掌外侧从容地向下切一次，幅度小、只做一次，然后手收回体侧，
末帧与首帧姿态完全一致。全程嘴巴闭合、不说话、无口型动作、不露齿。头部完全锁死：
全程零头部位移。不点头、不摇头、不转头、不歪头、不抬头、不低头、不晃头，
连轻微晃动都不允许；头部的位置和角度从第一帧到最后一帧保持完全一致，就像一张静止照片；
颈部与肩膀不因手臂动作而产生连带位移，面部表情不变。躯干与双腿固定，双脚站定，
不走动、不重心偏移。只有右臂和手在动，仅胸腔有轻微自然呼吸。双手始终完整在画面内且
不遮挡面部。镜头锁死，不推拉不摇移，不做画面裁剪变化。写实风格，高画质，人物身份全程一致。
```

---

### 5. `compare` 左右交替对比

**EN**
```
A steady locked-off frontal full-body shot of a digital-human presenter standing in a bright
white studio, identical camera framing, lighting, wardrobe and background to the reference
image, both feet visible. The presenter holds both hands at waist height with the palms facing
each other, then shifts the gesture from the left hand to the right hand once, contrasting two
ideas. Then both hands return to rest at the sides, ending in exactly the same pose as the
start. The mouth stays closed with lips together, no speaking, no lip movement, no teeth
showing. The head is completely locked: zero head movement of any kind, no nodding, no shaking,
no turning, no tilting, no lifting or lowering the chin, no head bob, no micro movement. The
head position and angle stay pixel-identical from the first frame to the last frame, as if the
head were a still photograph. The neck and shoulders do not shift in response to the arm
movement, and the facial expression does not change. Torso and legs stay fixed, feet planted,
no walking and no weight shift. Only the hands move, with subtle natural breathing in the chest
only. Hands stay fully inside the frame and never cover the face. Locked camera, no zoom, no
pan, no crop change. Realistic, high quality, consistent identity throughout.
```

**中文**
```
镜头完全固定，正面全身像，白底影棚，构图/光线/服装/背景与参考图完全一致，双脚完整入画。
人物双手停在腰腹高度、掌心相对，然后手势由左手交到右手一次，做出对比两件事的感觉，
之后双手回到体侧，末帧与首帧姿态完全一致。全程嘴巴闭合、不说话、无口型动作、不露齿。
头部完全锁死：全程零头部位移。不点头、不摇头、不转头、不歪头、不抬头、不低头、不晃头，
连轻微晃动都不允许；头部的位置和角度从第一帧到最后一帧保持完全一致，就像一张静止照片；
颈部与肩膀不因手臂动作而产生连带位移，面部表情不变。躯干与双腿固定，双脚站定，
不走动、不重心偏移。只有双手在动，仅胸腔有轻微自然呼吸。双手始终完整在画面内且
不遮挡面部。镜头锁死，不推拉不摇移，不做画面裁剪变化。写实风格，高画质，人物身份全程一致。
```

---

### 6. `frame` 双手框定范围

**EN**
```
A steady locked-off frontal full-body shot of a digital-human presenter standing in a bright
white studio, identical camera framing, lighting, wardrobe and background to the reference
image, both feet visible. The presenter raises both hands to chest height with the palms facing
each other about shoulder width apart, draws them apart to indicate a span or size, then brings
them slightly together again. Then the hands return to rest at the sides, ending in exactly the
same pose as the start. The mouth stays closed with lips together, no speaking, no lip movement,
no teeth showing. The head is completely locked: zero head movement of any kind, no nodding, no
shaking, no turning, no tilting, no lifting or lowering the chin, no head bob, no micro
movement. The head position and angle stay pixel-identical from the first frame to the last
frame, as if the head were a still photograph. The neck and shoulders do not shift in response
to the arm movement, and the facial expression does not change. Torso and legs stay fixed, feet
planted, no walking and no weight shift. Only the arms and hands move, with subtle natural
breathing in the chest only. Hands stay fully inside the frame and never cover the face. Locked
camera, no zoom, no pan, no crop change. Realistic, high quality, consistent identity
throughout.
```

**中文**
```
镜头完全固定，正面全身像，白底影棚，构图/光线/服装/背景与参考图完全一致，双脚完整入画。
人物双手抬到胸口高度、掌心相对，间距约与肩同宽，向两侧拉开表示"范围/大小"，
再略微收拢一次，然后双手回到体侧，末帧与首帧姿态完全一致。全程嘴巴闭合、不说话、
无口型动作、不露齿。头部完全锁死：全程零头部位移。不点头、不摇头、不转头、不歪头、
不抬头、不低头、不晃头，连轻微晃动都不允许；头部的位置和角度从第一帧到最后一帧
保持完全一致，就像一张静止照片；颈部与肩膀不因手臂动作而产生连带位移，面部表情不变。
躯干与双腿固定，双脚站定，不走动、不重心偏移。只有手臂和手在动，仅胸腔有轻微自然呼吸。
双手始终完整在画面内且不遮挡面部。镜头锁死，不推拉不摇移，不做画面裁剪变化。
写实风格，高画质，人物身份全程一致。
```

---

### 7. `press` 双掌下压

**EN**
```
A steady locked-off frontal full-body shot of a digital-human presenter standing in a bright
white studio, identical camera framing, lighting, wardrobe and background to the reference
image, both feet visible. The presenter raises both palms to chest height facing down and
presses them downward once in a calm "settle down, step by step" gesture, small amplitude.
Then the hands return to rest at the sides, ending in exactly the same pose as the start. The
mouth stays closed with lips together, no speaking, no lip movement, no teeth showing. The head
is completely locked: zero head movement of any kind, no nodding, no shaking, no turning, no
tilting, no lifting or lowering the chin, no head bob, no micro movement. The head position and
angle stay pixel-identical from the first frame to the last frame, as if the head were a still
photograph. The neck and shoulders do not shift in response to the arm movement, and the facial
expression does not change. Torso and legs stay fixed, feet planted, no walking and no weight
shift. Only the arms and hands move, with subtle natural breathing in the chest only. Hands stay
fully inside the frame and never cover the face. Locked camera, no zoom, no pan, no crop change.
Realistic, high quality, consistent identity throughout.
```

**中文**
```
镜头完全固定，正面全身像，白底影棚，构图/光线/服装/背景与参考图完全一致，双脚完整入画。
人物双掌抬到胸口高度、掌心朝下，从容地向下压一次，做出"稳住、一步一步来"的手势，幅度小，
然后双手回到体侧，末帧与首帧姿态完全一致。全程嘴巴闭合、不说话、无口型动作、不露齿。
头部完全锁死：全程零头部位移。不点头、不摇头、不转头、不歪头、不抬头、不低头、不晃头，
连轻微晃动都不允许；头部的位置和角度从第一帧到最后一帧保持完全一致，就像一张静止照片；
颈部与肩膀不因手臂动作而产生连带位移，面部表情不变。躯干与双腿固定，双脚站定，
不走动、不重心偏移。只有手臂和手在动，仅胸腔有轻微自然呼吸。双手始终完整在画面内且
不遮挡面部。镜头锁死，不推拉不摇移，不做画面裁剪变化。写实风格，高画质，人物身份全程一致。
```

---

### 8. `invite` 侧向邀请

**EN**
```
A steady locked-off frontal full-body shot of a digital-human presenter standing in a bright
white studio, identical camera framing, lighting, wardrobe and background to the reference
image, both feet visible. The presenter sweeps the right arm outward to the side at waist
height with the hand open and the palm up, as if inviting the audience to look, without
rotating the torso. Then the arm returns to rest at the side, ending in exactly the same pose
as the start. The mouth stays closed with lips together, no speaking, no lip movement, no teeth
showing. The head is completely locked: zero head movement of any kind, no nodding, no shaking,
no turning, no tilting, no lifting or lowering the chin, no head bob, no micro movement. The
head position and angle stay pixel-identical from the first frame to the last frame, as if the
head were a still photograph. The neck and shoulders do not shift in response to the arm
movement, and the facial expression does not change. Torso and legs stay fixed, feet planted,
no walking and no weight shift, no torso rotation. Only the right arm and hand move, with subtle
natural breathing in the chest only. Hands stay fully inside the frame and never cover the face.
Locked camera, no zoom, no pan, no crop change. Realistic, high quality, consistent identity
throughout.
```

**中文**
```
镜头完全固定，正面全身像，白底影棚，构图/光线/服装/背景与参考图完全一致，双脚完整入画。
人物右臂在腰腹高度向体侧外方展开，手掌张开、掌心向上，像在邀请观众观看，躯干不转动，
然后手臂回到体侧，末帧与首帧姿态完全一致。全程嘴巴闭合、不说话、无口型动作、不露齿。
头部完全锁死：全程零头部位移。不点头、不摇头、不转头、不歪头、不抬头、不低头、不晃头，
连轻微晃动都不允许；头部的位置和角度从第一帧到最后一帧保持完全一致，就像一张静止照片；
颈部与肩膀不因手臂动作而产生连带位移，面部表情不变。躯干与双腿固定，双脚站定，不走动、
不重心偏移、不转体。只有右臂和手在动，仅胸腔有轻微自然呼吸。双手始终完整在画面内且
不遮挡面部。镜头锁死，不推拉不摇移，不做画面裁剪变化。写实风格，高画质，人物身份全程一致。
```

---

## 五、负向提示词（有独立负向输入框时填这里）

```
任何头部动作, 点头, 摇头, 转头, 歪头, 抬头, 低头, 晃头, 头部微动, 头部前后位移, 眨眼,
表情变化, 说话, 张嘴, 露齿, 笑, 脖子前倾, 肩膀耸动, 躯体转动, 侧身, 走动, 位移,
镜头运动, 推拉摇移, 变焦, 画面裁剪变化, 换脸, 人脸变形, 手指粘连, 六指, 手部模糊,
手出画, 手挡脸, 换衣服, 换背景, 字幕, 水印, logo, 文字, 画风突变, 低清晰度
```

## 六、生成参数

| 项 | 取值 | 说明 |
|---|---|---|
| 首帧 | 上传原图（`00000000.png`） | 图生视频，不要文生视频 |
| 时长 | **4~5 s** | Seedance 最低档 4s 最便宜；5s ≈ 121 帧（现有 `explain` 就是 121 帧） |
| 画幅 | **与原图一致（9:16）** | 尺寸不一致会在加载期被缩放归一化 |
| 运镜 | **关**（不选推/拉/摇/移） | 一运镜构图就变，人脸框跟着漂 |
| 一次一条 | 一个动作一条视频 | 混动作必然糊 |

## 七、验收清单（拿到 mp4 先过一遍，再转素材）

- [ ] **头部零动作**：`coords.pkl` 首末 bbox 左上角 Δ ≤ 4px（判据见 §一）
- [ ] 首帧 ≈ 末帧姿态（手都回到体侧）
- [ ] 全程嘴闭合、不露齿
- [ ] 双手完整在画面内，任意一帧都没遮脸
- [ ] 没换衣服、没换白底、没改构图、没转体
- [ ] 只有一个动作，没有"顺手多做两个"
- [ ] 帧数 ≤ 150（`read_imgs` 一次性全读进内存，150 帧 538×1080 ≈ 249 MB）

## 八、转成手势素材

1. 视频走 `web/avatar.html` 上传（`POST /api/avatar/task`）→ `genavatar` 产出 `full_imgs/`、`face_imgs/`、`coords.pkl`；
2. 把这三件套拷到 `data/avatars/Mr.Chen/gestures/<id>/`；
3. 目录里放 `gesture.json`：`{"label": "双手框定范围"}`；
4. 先跑 §一 的 bbox 检查，通过后再写讲稿 `[GESTURE:frame]`。

> `list_gestures()` 要求**三件套齐备**才认，缺任一项这套手势不会出现在可用列表里（静默跳过，不报错）。

---

## 九、想把这 8 条写进代码内置目录？

平台原有的内置手势目录（`utils/seedance_video.py::GESTURE_PROMPTS` 与 `/api/seedance/gestures`）已随「Seedance 手势视频」功能于 2026-10-24 下线删除，代码内不再有内置提示词表。当前手势素材统一走「本文件提示词 → 外部图生视频 → `genavatar` → `data/avatars/{id}/gestures/{gid}/`」的离线产线；若未来要重建代码内置目录，加 id 会改变对外可见的手势列表——属于对外可观察行为变更，按项目约定需先建 OpenSpec 变更再动代码。

---

## 十、定稿版：两段式提示词（身份锁定 + 动作槽位）

### 10.1 结构

| 段 | 作用 | 是否随动作变 |
|---|---|---|
| **段① 身份锁定** | 锁死是同一个人（发型/发际线/脸型/眉眼/鼻口/肤色/年龄感/表情），禁止换脸、美化、加配件 | **8 条一字不改** |
| **段② 动作与禁动** | 镜头、构图、动作、回位、嘴、头、躯干、身份一致 | **只改两处**（见 10.2） |

### 10.2 唯一可变的两处

| id | ①动作句（替换点） | ②"只有…在动" |
|---|---|---|
| `open` | 双前臂抬到腰腹高度，掌心向上摊开 | 手臂和手 |
| `point` | 右臂抬到胸口高度，向前并略偏右侧伸出，整掌张开 | 右臂和手 |
| `count` | 右手在胸口高度缓慢点数三点：食指→食指中指→三指 | 右手和手指 |
| `chop` | 右手掌外侧在胸口高度向下切一次 | 右臂和手 |
| `compare` | 双手腰腹高度掌心相对，手势由左手交到右手一次 | 双手 |
| `frame` | 双手胸口高度掌心相对，向两侧拉开再略收拢 | 手臂和手 |
| `press` | 双掌胸口高度掌心朝下，向下压一次 | 手臂和手 |
| `invite` | 右臂在腰腹高度向体侧外方展开，掌心向上 | 右臂和手 |

其余字句（镜头/双脚/回位/嘴/头/躯干/呼吸/收尾）**全部原样照抄**，一个标点都别动。

### 10.3 一处必须修正的措辞（否则动作可能出不来）

段① 原句："不要改变服装、身体比例、**手臂动作**、镜头、背景或灯光"。

这句和段②"要抬起手臂做新手势"**互相打架**——模型很可能因此把手臂锁住、只做极小幅度的抖动。建议改为：

> 除下文明确描述的手臂动作外，不要改变服装、身体比例、镜头、背景或灯光

下面的定稿版已采用此写法（改动只有这一处，其余与你给的原句完全一致）。

### 10.4 八条定稿（直接整段粘贴）

#### `open` 掌心向上展示

```
严格匹配图片1中的同一成年东亚男性：完全一致的短黑发、发际线、脸型、眉眼、鼻口比例、肤色、年龄感和不变的面部表情。头部位置与角度继续完全锁死，嘴巴全程闭合，不说话、不露齿、不做口型。除下文明确描述的手臂动作外，不要改变服装、身体比例、镜头、背景或灯光；不要生成新人物，不要美化成另一张脸，不要添加胡须、眼镜或其他配件。
镜头完全固定，正面全身像，白底影棚，构图/光线/服装/背景与参考图完全一致，双脚完整入画。人物双前臂抬到腰腹高度，掌心向上朝向观众，略微向外摊开，做出“请看这里”的展示手势，停顿一拍后双手自然回到体侧，末帧与首帧姿态完全一致。全程嘴巴闭合、不说话、无口型动作、不露齿。头部完全锁死：全程零头部位移。不点头、不摇头、不转头、不歪头、不抬头、不低头、不晃头，连轻微晃动都不允许；头部的位置和角度从第一帧到最后一帧保持完全一致，就像一张静止照片；颈部与肩膀不因手臂动作而产生连带位移，面部表情不变。躯干与双腿固定，双脚站定，不走动、不重心偏移。只有手臂和手在动，仅胸腔有轻微自然呼吸。双手始终完整在画面内且不遮挡面部。镜头锁死，不推拉不摇移，不做画面裁剪变化。写实风格，高画质，人物身份全程一致。
```

#### `point` 指向引导

```
严格匹配图片1中的同一成年东亚男性：完全一致的短黑发、发际线、脸型、眉眼、鼻口比例、肤色、年龄感和不变的面部表情。头部位置与角度继续完全锁死，嘴巴全程闭合，不说话、不露齿、不做口型。除下文明确描述的手臂动作外，不要改变服装、身体比例、镜头、背景或灯光；不要生成新人物，不要美化成另一张脸，不要添加胡须、眼镜或其他配件。
镜头完全固定，正面全身像，白底影棚，构图/光线/服装/背景与参考图完全一致，双脚完整入画。人物抬起右臂到胸口高度，向前并略偏向画面右侧伸出，整只手掌张开、掌心斜向上，像在引导观众看身旁的幻灯片，保持指引姿势一拍后手臂收回体侧，末帧与首帧姿态完全一致。全程嘴巴闭合、不说话、无口型动作、不露齿。头部完全锁死：全程零头部位移。不点头、不摇头、不转头、不歪头、不抬头、不低头、不晃头，连轻微晃动都不允许；头部的位置和角度从第一帧到最后一帧保持完全一致，就像一张静止照片；颈部与肩膀不因手臂动作而产生连带位移，面部表情不变。躯干与双腿固定，双脚站定，不走动、不重心偏移。只有右臂和手在动，仅胸腔有轻微自然呼吸。双手始终完整在画面内且不遮挡面部。镜头锁死，不推拉不摇移，不做画面裁剪变化。写实风格，高画质，人物身份全程一致。
```

#### `count` 手指点数列举

```
严格匹配图片1中的同一成年东亚男性：完全一致的短黑发、发际线、脸型、眉眼、鼻口比例、肤色、年龄感和不变的面部表情。头部位置与角度继续完全锁死，嘴巴全程闭合，不说话、不露齿、不做口型。除下文明确描述的手臂动作外，不要改变服装、身体比例、镜头、背景或灯光；不要生成新人物，不要美化成另一张脸，不要添加胡须、眼镜或其他配件。
镜头完全固定，正面全身像，白底影棚，构图/光线/服装/背景与参考图完全一致，双脚完整入画。人物在胸口高度用右手缓慢均匀地点数三点：先伸食指，再伸食指和中指，最后伸出三根手指，幅度小、节奏稳，保持一拍后右手收回体侧，末帧与首帧姿态完全一致。全程嘴巴闭合、不说话、无口型动作、不露齿。头部完全锁死：全程零头部位移。不点头、不摇头、不转头、不歪头、不抬头、不低头、不晃头，连轻微晃动都不允许；头部的位置和角度从第一帧到最后一帧保持完全一致，就像一张静止照片；颈部与肩膀不因手臂动作而产生连带位移，面部表情不变。躯干与双腿固定，双脚站定，不走动、不重心偏移。只有右手和手指在动，仅胸腔有轻微自然呼吸。双手始终完整在画面内且不遮挡面部。镜头锁死，不推拉不摇移，不做画面裁剪变化。写实风格，高画质，人物身份全程一致。
```

#### `chop` 切砍强调

```
严格匹配图片1中的同一成年东亚男性：完全一致的短黑发、发际线、脸型、眉眼、鼻口比例、肤色、年龄感和不变的面部表情。头部位置与角度继续完全锁死，嘴巴全程闭合，不说话、不露齿、不做口型。除下文明确描述的手臂动作外，不要改变服装、身体比例、镜头、背景或灯光；不要生成新人物，不要美化成另一张脸，不要添加胡须、眼镜或其他配件。
镜头完全固定，正面全身像，白底影棚，构图/光线/服装/背景与参考图完全一致，双脚完整入画。人物在胸口高度用右手掌外侧从容地向下切一次，幅度小、只做一次，保持一拍后右手收回体侧，末帧与首帧姿态完全一致。全程嘴巴闭合、不说话、无口型动作、不露齿。头部完全锁死：全程零头部位移。不点头、不摇头、不转头、不歪头、不抬头、不低头、不晃头，连轻微晃动都不允许；头部的位置和角度从第一帧到最后一帧保持完全一致，就像一张静止照片；颈部与肩膀不因手臂动作而产生连带位移，面部表情不变。躯干与双腿固定，双脚站定，不走动、不重心偏移。只有右臂和手在动，仅胸腔有轻微自然呼吸。双手始终完整在画面内且不遮挡面部。镜头锁死，不推拉不摇移，不做画面裁剪变化。写实风格，高画质，人物身份全程一致。
```

#### `compare` 左右交替对比

```
严格匹配图片1中的同一成年东亚男性：完全一致的短黑发、发际线、脸型、眉眼、鼻口比例、肤色、年龄感和不变的面部表情。头部位置与角度继续完全锁死，嘴巴全程闭合，不说话、不露齿、不做口型。除下文明确描述的手臂动作外，不要改变服装、身体比例、镜头、背景或灯光；不要生成新人物，不要美化成另一张脸，不要添加胡须、眼镜或其他配件。
镜头完全固定，正面全身像，白底影棚，构图/光线/服装/背景与参考图完全一致，双脚完整入画。人物双手停在腰腹高度、掌心相对，然后手势由左手交到右手一次，做出对比两件事的感觉，保持一拍后双手回到体侧，末帧与首帧姿态完全一致。全程嘴巴闭合、不说话、无口型动作、不露齿。头部完全锁死：全程零头部位移。不点头、不摇头、不转头、不歪头、不抬头、不低头、不晃头，连轻微晃动都不允许；头部的位置和角度从第一帧到最后一帧保持完全一致，就像一张静止照片；颈部与肩膀不因手臂动作而产生连带位移，面部表情不变。躯干与双腿固定，双脚站定，不走动、不重心偏移。只有双手在动，仅胸腔有轻微自然呼吸。双手始终完整在画面内且不遮挡面部。镜头锁死，不推拉不摇移，不做画面裁剪变化。写实风格，高画质，人物身份全程一致。
```

#### `frame` 双手框定范围

```
严格匹配图片1中的同一成年东亚男性：完全一致的短黑发、发际线、脸型、眉眼、鼻口比例、肤色、年龄感和不变的面部表情。头部位置与角度继续完全锁死，嘴巴全程闭合，不说话、不露齿、不做口型。除下文明确描述的手臂动作外，不要改变服装、身体比例、镜头、背景或灯光；不要生成新人物，不要美化成另一张脸，不要添加胡须、眼镜或其他配件。
镜头完全固定，正面全身像，白底影棚，构图/光线/服装/背景与参考图完全一致，双脚完整入画。人物双手抬到胸口高度、掌心相对，间距约与肩同宽，向两侧拉开表示“范围/大小”，再略微收拢一次，保持一拍后双手回到体侧，末帧与首帧姿态完全一致。全程嘴巴闭合、不说话、无口型动作、不露齿。头部完全锁死：全程零头部位移。不点头、不摇头、不转头、不歪头、不抬头、不低头、不晃头，连轻微晃动都不允许；头部的位置和角度从第一帧到最后一帧保持完全一致，就像一张静止照片；颈部与肩膀不因手臂动作而产生连带位移，面部表情不变。躯干与双腿固定，双脚站定，不走动、不重心偏移。只有手臂和手在动，仅胸腔有轻微自然呼吸。双手始终完整在画面内且不遮挡面部。镜头锁死，不推拉不摇移，不做画面裁剪变化。写实风格，高画质，人物身份全程一致。
```

#### `press` 双掌下压

```
严格匹配图片1中的同一成年东亚男性：完全一致的短黑发、发际线、脸型、眉眼、鼻口比例、肤色、年龄感和不变的面部表情。头部位置与角度继续完全锁死，嘴巴全程闭合，不说话、不露齿、不做口型。除下文明确描述的手臂动作外，不要改变服装、身体比例、镜头、背景或灯光；不要生成新人物，不要美化成另一张脸，不要添加胡须、眼镜或其他配件。
镜头完全固定，正面全身像，白底影棚，构图/光线/服装/背景与参考图完全一致，双脚完整入画。人物双掌抬到胸口高度、掌心朝下，从容地向下压一次，做出“稳住、一步一步来”的手势，幅度小，保持一拍后双手回到体侧，末帧与首帧姿态完全一致。全程嘴巴闭合、不说话、无口型动作、不露齿。头部完全锁死：全程零头部位移。不点头、不摇头、不转头、不歪头、不抬头、不低头、不晃头，连轻微晃动都不允许；头部的位置和角度从第一帧到最后一帧保持完全一致，就像一张静止照片；颈部与肩膀不因手臂动作而产生连带位移，面部表情不变。躯干与双腿固定，双脚站定，不走动、不重心偏移。只有手臂和手在动，仅胸腔有轻微自然呼吸。双手始终完整在画面内且不遮挡面部。镜头锁死，不推拉不摇移，不做画面裁剪变化。写实风格，高画质，人物身份全程一致。
```

#### `invite` 侧向邀请

```
严格匹配图片1中的同一成年东亚男性：完全一致的短黑发、发际线、脸型、眉眼、鼻口比例、肤色、年龄感和不变的面部表情。头部位置与角度继续完全锁死，嘴巴全程闭合，不说话、不露齿、不做口型。除下文明确描述的手臂动作外，不要改变服装、身体比例、镜头、背景或灯光；不要生成新人物，不要美化成另一张脸，不要添加胡须、眼镜或其他配件。
镜头完全固定，正面全身像，白底影棚，构图/光线/服装/背景与参考图完全一致，双脚完整入画。人物右臂在腰腹高度向体侧外方展开，手掌张开、掌心向上，像在邀请观众观看，保持一拍后手臂收回体侧，末帧与首帧姿态完全一致。全程嘴巴闭合、不说话、无口型动作、不露齿。头部完全锁死：全程零头部位移。不点头、不摇头、不转头、不歪头、不抬头、不低头、不晃头，连轻微晃动都不允许；头部的位置和角度从第一帧到最后一帧保持完全一致，就像一张静止照片；颈部与肩膀不因手臂动作而产生连带位移，面部表情不变。躯干与双腿固定，双脚站定，不走动、不重心偏移。只有右臂和手在动，仅胸腔有轻微自然呼吸。双手始终完整在画面内且不遮挡面部。镜头锁死，不推拉不摇移，不做画面裁剪变化。写实风格，高画质，人物身份全程一致。
```

### 10.5 换动作时只改这两句的检查法

生成前自查一遍：把段② 的第 2 句（动作）和第 8 句（"只有…在动"）挑出来看，**这两句之外必须逐字等于 `open` 版**。只要有一处不同，就是串味来源。

---

## 十一、已安装手势清单（Mr.Chen，2026-10-24）

| id | 显示名 | 帧数 | 来源 | 头部 bbox 漂移 max | 首尾回位 |
|---|---|---|---|---|---|
| `explain` | 讲解手势 | 121 | 图生视频（2026-10-14） | 14.3px | 2.8px |
| `open` | 掌心向上展示 | 107 | MiniMax（v2 裁切版） | 1.8px | 1.5px |
| `chop` | 切砍强调 | 107 | MiniMax（v2 裁切版） | 1.4px | 0.0px |
| `compare` | 左右交替对比 | 107 | MiniMax（v2 裁切版） | 2.1px | 1.0px |

讲稿里写 `[GESTURE:open]` / `[GESTURE:chop]` / `[GESTURE:compare]` 即可触发；`list_gestures('Mr.Chen')` 已返回这 4 套。
三者均 538×1080、人脸图 256×256，与基础视频**零运行期缩放告警**（`explain` 是 536×1080，加载时会有一条归一化告警，历史遗留、无害）。
漂移/回位为 2026-10-24 v2 版实测（`scripts/verify_gestures.py`）；v1 压扁版已废弃，留档 `data/tmp/gesture_backup_v1_compressed/`，详见 11.2。

### 11.1 转换工具

`scripts/install_gesture_videos.py`：读 mp4 → `genavatar`（`img_size=256, pads=[0,10,0,0], pad_scale=0.35`，与 `explain` 一致）→ 帧**裁切**到 538×1080 且 `coords` 同步偏移修正 → 拷贝进 `gestures/<id>/` + 写 `gesture.json`。
尺寸归一化放在安装期做，会话内就不会触发 `load_gesture` 的 resize 告警，coords 也不会被二次取整。

两个参数是拿默认素材逐像素核对死的，**别改**：

- `pad_scale=0.35`：默认素材 `coords.pkl[75]=(82,252,203,330)` 正是检测框 `[229,117,304,217]` 按 0.35 扩边所得；传 0 会得到紧贴脸的小框，与整套素材不一致（v1 踩过，见 11.2）。
- 归一化用**裁切**不用 resize：源 1440×2560（宽高比 0.562）比目标 538×1080（0.498）宽，裁两侧各 34px 即可；若硬 resize 会把宽度压掉 11.2%，人明显变窄（v1 踩过，用户目视发现）。
- 目标尺寸取 **538×1080 = 基础视频 `full_imgs` 尺寸**（`load_gesture` 的 `ref_size` 就是它）。注意 `explain` 本身是 536×1080，加载时会被做一次 0.37% 的横向拉伸——历史遗留、无害，别去"对齐"它。

### 11.2 已解决：v1 转换缺陷复盘（构图问题子虚乌有）

2026-10-24 首版转换（v1）曾记录"新视频构图比默认素材松、人脸小 35%"，**该结论是错误的**，实测数据已推翻。复盘如下，避免再被同一个测量误差带偏。

**当时的错误测量**（v1 素材，`pad_scale=0` + resize 压扁）：

| 素材 | 脸框（均值） | 脸高占画面 | 头顶留白 |
|---|---|---|---|
| 默认素材 | 127×170 | 15.7% | 7.6% |
| `explain` | 123×169 | 15.7% | 8.0% |
| `open` / `chop` / `compare`（v1 错误测量） | **64×110** | **10.2%** | 10.8% |

**错在哪**：v1 用了 `pad_scale=0`，coords 是**紧贴检测框**（约 64×110，不含额头与下颌外扩）；而默认素材的 127×170 是检测框按 0.35 扩边后的框。拿紧贴框去比扩边框，天然差 35%——人物的实际 scale 和在画面中的位置**从来就是一致的**，不存在"构图松"。

**v1 的真实缺陷**（与构图无关，都已修复）：

1. `pad_scale=0`：人脸框/人脸裁剪与整套素材不一致，贴回区域对不齐；
2. 帧归一化用了 `cv2.resize(606×1080 → 538×1080)`：宽度压掉 11.2%，**人体被横向压扁**（用户目视发现"身形压缩了"）。

**修复后实测**（v2 = 当前安装版，裁切 + `pad_scale=0.35`）：

| 素材 | 脸框（均值） | 脸高占画面 | 头顶留白 | 头部漂移 max | 首尾回位 |
|---|---|---|---|---|---|
| 默认素材 | 127×170 | 15.7% | 7.6% | — | — |
| `explain` | 124×169 | 15.6% | 7.9% | 14.3px | 2.8px |
| `open` | 123×171 | 15.8% | 7.5% | 1.8px | 1.5px |
| `chop` | 122×170 | 15.7% | 7.5% | 1.4px | 0.0px |
| `compare` | 122×168 | 15.6% | 7.6% | 2.1px | 1.0px |

四项验收全部通过：① 帧尺寸 538×1080 与基础视频一致，**加载期零归一化**（真实 loader 复验无 warning）；② 脸框宽高比 0.717–0.727 vs explain 0.731，若仍有横向压扁会掉到 0.67 附近；③ 人体剪影外接框四套几乎一致（宽 248–253、高 864–867、头顶 y≈91、脚底 y≈958），全身完整入画；④ 头部漂移 ≤2.1px，优于 explain 的 14.3px。

**验收工具**：`scripts/verify_gestures.py`（帧尺寸/脸框/漂移/回位/剪影五项 + 首中末三帧对比图 `data/tmp/gesture_stage/framing_compare.png`）。v1 压扁版留档于 `data/tmp/gesture_backup_v1_compressed/`。

**给未来生成的建议**（非必需）：MiniMax 不保证多次生成之间几何一致，若再生成新手势，仍在段①后追加锁定句以防漂移：

```
Match the reference framing exactly: identical subject scale, position and margins.
The face height is about 16% of the frame height, with about 8% headroom.
Do not zoom in or out, do not add extra margin around the subject,
and keep the same aspect ratio as the reference image.
```

中文版：

```
构图与参考图严格一致：人物在画面中的大小、位置、留白完全一致，
脸部高度约占画面高度的 16%，头顶留白约 8%。不要放大或缩小人物，
不要在四周增加留白，画幅比例与参考图相同。
```

---

## 十二、想把这 8 条写进代码内置目录？

## 十三、扩展的 8 套（2026-10-26）

在 §四 的 8 套之上补齐：开场、提问、叫停、放大细节、画圈、权衡、确认、收尾。
文案已写进 `utils/gesture_presets.py`，本节由该模块的常量导出，两边不会走样。

| id | 中文名 | 讲稿里什么时候用 | 风险 |
|---|---|---|---|
| `welcome` | 挥手致意 | 开场打招呼、欢迎 | 中（挥手易带动肩） |
| `question` | 摊手提问 | 抛出问题、「这是怎么回事」 | 低 |
| `stop` | 手掌前推 | 提示停顿、「先注意这一点」 | 低 |
| `zoom` | 双手拉近 | 讲「放大看这个细节」 | 低 |
| `circle` | 手指画圈 | 讲循环、迭代、流程闭环 | 中（手指粘连） |
| `weigh` | 双手掂量 | 讲权衡、取舍、两方面 | 中（掌形/手指） |
| `ok` | OK 确认 | 讲「这样就对了 / 完成」 | **高**（手指粘连） |
| `thanks` | 双手合十 | 收尾致谢 | 低 |

### 提示词（完整可粘贴）

#### `welcome` 挥手致意

```
镜头完全固定，正面全身像，白底影棚，构图/光线/服装/背景与参考图完全一致，双脚完整入画。人物抬起右臂到肩部高度，手掌张开、掌心朝向观众，自然地左右挥动两下示意打招呼，停顿一拍后右手自然回到体侧，末帧与首帧姿态完全一致。全程嘴巴闭合、不说话、无口型动作、不露齿。头部完全锁死：全程零头部位移。不点头、不摇头、不转头、不歪头、不抬头、不低头、不晃头，连轻微晃动都不允许；头部的位置和角度从第一帧到最后一帧保持完全一致，就像一张静止照片；颈部与肩膀不因手臂动作而产生连带位移，面部表情不变。躯干与双腿固定，双脚站定，不走动、不重心偏移。只有右臂和手在动，仅胸腔有轻微自然呼吸。双手始终完整在画面内且不遮挡面部。镜头锁死，不推拉不摇移，不做画面裁剪变化。写实风格，高画质，人物身份全程一致。
```

#### `question` 摊手提问

```
镜头完全固定，正面全身像，白底影棚，构图/光线/服装/背景与参考图完全一致，双脚完整入画。人物把双前臂抬到腰腹高度，掌心向上、手指自然张开，向两侧轻轻摊开一次，做出询问的姿态，停顿一拍后双手自然回到体侧，末帧与首帧姿态完全一致。全程嘴巴闭合、不说话、无口型动作、不露齿。头部完全锁死：全程零头部位移。不点头、不摇头、不转头、不歪头、不抬头、不低头、不晃头，连轻微晃动都不允许；头部的位置和角度从第一帧到最后一帧保持完全一致，就像一张静止照片；颈部与肩膀不因手臂动作而产生连带位移，面部表情不变。躯干与双腿固定，双脚站定，不走动、不重心偏移。只有手臂和手在动，仅胸腔有轻微自然呼吸。双手始终完整在画面内且不遮挡面部。镜头锁死，不推拉不摇移，不做画面裁剪变化。写实风格，高画质，人物身份全程一致。
```

#### `stop` 手掌前推

```
镜头完全固定，正面全身像，白底影棚，构图/光线/服装/背景与参考图完全一致，双脚完整入画。人物抬起右手到胸口高度，掌心朝前、五指自然张开，向前短促地推一下即收住，像在示意"先停一下"。停顿一拍后右手自然回到体侧，末帧与首帧姿态完全一致。全程嘴巴闭合、不说话、无口型动作、不露齿。头部完全锁死：全程零头部位移。不点头、不摇头、不转头、不歪头、不抬头、不低头、不晃头，连轻微晃动都不允许；头部的位置和角度从第一帧到最后一帧保持完全一致，就像一张静止照片；颈部与肩膀不因手臂动作而产生连带位移，面部表情不变。躯干与双腿固定，双脚站定，不走动、不重心偏移。只有右臂和手在动，仅胸腔有轻微自然呼吸。双手始终完整在画面内且不遮挡面部。镜头锁死，不推拉不摇移，不做画面裁剪变化。写实风格，高画质，人物身份全程一致。
```

#### `zoom` 双手拉近

```
镜头完全固定，正面全身像，白底影棚，构图/光线/服装/背景与参考图完全一致，双脚完整入画。人物把双手抬到胸前、掌心相对，先合到约一拳距离，再向两侧拉开到约肩宽，像把某个细节放大来看。停顿一拍后双手自然回到体侧，末帧与首帧姿态完全一致。全程嘴巴闭合、不说话、无口型动作、不露齿。头部完全锁死：全程零头部位移。不点头、不摇头、不转头、不歪头、不抬头、不低头、不晃头，连轻微晃动都不允许；头部的位置和角度从第一帧到最后一帧保持完全一致，就像一张静止照片；颈部与肩膀不因手臂动作而产生连带位移，面部表情不变。躯干与双腿固定，双脚站定，不走动、不重心偏移。只有手臂和手在动，仅胸腔有轻微自然呼吸。双手始终完整在画面内且不遮挡面部。镜头锁死，不推拉不摇移，不做画面裁剪变化。写实风格，高画质，人物身份全程一致。
```

#### `circle` 手指画圈

```
镜头完全固定，正面全身像，白底影棚，构图/光线/服装/背景与参考图完全一致，双脚完整入画。人物抬起右手到胸口高度，食指伸出、其余手指自然收拢，在空中画一个直径约一个手掌的小圆圈，只画一圈。停顿一拍后右手自然回到体侧，末帧与首帧姿态完全一致。全程嘴巴闭合、不说话、无口型动作、不露齿。头部完全锁死：全程零头部位移。不点头、不摇头、不转头、不歪头、不抬头、不低头、不晃头，连轻微晃动都不允许；头部的位置和角度从第一帧到最后一帧保持完全一致，就像一张静止照片；颈部与肩膀不因手臂动作而产生连带位移，面部表情不变。躯干与双腿固定，双脚站定，不走动、不重心偏移。只有右臂和手在动，仅胸腔有轻微自然呼吸。双手始终完整在画面内且不遮挡面部。镜头锁死，不推拉不摇移，不做画面裁剪变化。写实风格，高画质，人物身份全程一致。
```

#### `weigh` 双手掂量

```
镜头完全固定，正面全身像，白底影棚，构图/光线/服装/背景与参考图完全一致，双脚完整入画。人物把双手平抬到腰腹高度、掌心向上，像双手各托着一样东西，交替地上下轻轻掂量两下，幅度很小。停顿一拍后双手自然回到体侧，末帧与首帧姿态完全一致。全程嘴巴闭合、不说话、无口型动作、不露齿。头部完全锁死：全程零头部位移。不点头、不摇头、不转头、不歪头、不抬头、不低头、不晃头，连轻微晃动都不允许；头部的位置和角度从第一帧到最后一帧保持完全一致，就像一张静止照片；颈部与肩膀不因手臂动作而产生连带位移，面部表情不变。躯干与双腿固定，双脚站定，不走动、不重心偏移。只有手臂和手在动，仅胸腔有轻微自然呼吸。双手始终完整在画面内且不遮挡面部。镜头锁死，不推拉不摇移，不做画面裁剪变化。写实风格，高画质，人物身份全程一致。
```

#### `ok` OK 确认

```
镜头完全固定，正面全身像，白底影棚，构图/光线/服装/背景与参考图完全一致，双脚完整入画。人物抬起右手到胸前高度，拇指与食指轻轻相触成环，其余三指自然伸展，停顿一拍。停顿一拍后右手自然回到体侧，末帧与首帧姿态完全一致。全程嘴巴闭合、不说话、无口型动作、不露齿。头部完全锁死：全程零头部位移。不点头、不摇头、不转头、不歪头、不抬头、不低头、不晃头，连轻微晃动都不允许；头部的位置和角度从第一帧到最后一帧保持完全一致，就像一张静止照片；颈部与肩膀不因手臂动作而产生连带位移，面部表情不变。躯干与双腿固定，双脚站定，不走动、不重心偏移。只有右臂和手在动，仅胸腔有轻微自然呼吸。双手始终完整在画面内且不遮挡面部。镜头锁死，不推拉不摇移，不做画面裁剪变化。写实风格，高画质，人物身份全程一致。
```

#### `thanks` 双手合十

```
镜头完全固定，正面全身像，白底影棚，构图/光线/服装/背景与参考图完全一致，双脚完整入画。人物把双手在胸前合拢、指尖向上，做一个轻点头的替代——仅手部合十致意，停顿一拍。停顿一拍后双手自然回到体侧，末帧与首帧姿态完全一致。全程嘴巴闭合、不说话、无口型动作、不露齿。头部完全锁死：全程零头部位移。不点头、不摇头、不转头、不歪头、不抬头、不低头、不晃头，连轻微晃动都不允许；头部的位置和角度从第一帧到最后一帧保持完全一致，就像一张静止照片；颈部与肩膀不因手臂动作而产生连带位移，面部表情不变。躯干与双腿固定，双脚站定，不走动、不重心偏移。只有手臂和手在动，仅胸腔有轻微自然呼吸。双手始终完整在画面内且不遮挡面部。镜头锁死，不推拉不摇移，不做画面裁剪变化。写实风格，高画质，人物身份全程一致。
```

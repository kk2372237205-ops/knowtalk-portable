# 设计

## 1. 页码门槛放在编排器，而不是出题侧

提问点是否"该问了"是**时间条件**，只能由推进循环判定；出题侧（`quiz_plan`）只决定
"哪一页问哪道题"。因此：

- `quiz_plan` 负责**顺序**：纯提问模式下按 `slide_id` 升序（未指定页码的排最后，保持填写顺序）；
- `QuizOrchestrator` 负责**时机**：`_advance()` 拿到 `ask` 步骤时先算目标页码，
  当前页未到则进入 `Stage.WAITING_SLIDE` 并记住待抛的题目。

页码来源优先级：提问点所属教学段的 `slide_id` → 题目自带的 `slide_id`。
两者都为空则不设门槛（保持旧行为），避免无标签讲稿被卡住。

## 2. 页码比较用"页序"而不是数值大小

`material_manager` 的 `slide_id` 是自增 ID，未必连续也未必与页码一致。
直接比较 `current >= target` 在跳页、删页后不可靠。因此新增

```python
def slide_reached(current, target):   # utils/material_manager.py
    order = {slide['id']: index for index, slide in enumerate(list_slides())}
    if current in order and target in order:
        return order[current] >= order[target]
    return current is not None and current >= target   # 兜底：按数值
```

编排器只依赖注入的 `slide_probe(sessionid, slide_id) -> bool`，不直接 import 材质库；
默认实现在 `server/quiz_routes.py::_default_slide_probe`，单测可注入假实现。

## 3. 等待页码不设时限

页码是**触发条件**，不是"最多等一会儿就照念"的软条件。因此编排器里不存任何等待阈值，
`waiting_slide` 阶段只在 `slide_probe` 返回"已到达"时离开（`skip()` 手动跳过除外）。

代价是要解决"等不到怎么办"：

- **手动出口**：`/api/quiz/skip` 在等待阶段直接抛题（不记未作答）；**不从播放按钮触发**；
- **可观测**：等待期间每 `SLIDE_WAIT_LOG_INTERVAL = 15s` 打一条 info
  「仍在等待第 N 页（已等 Xs）」。它只是日志节流，不参与任何判定——
  课堂"卡住"与"正在等翻页"必须能从日志分清。

## 3.1 等页码期间绝不能暂停（踩过的坑）

第一版让 `waiting_slide` 也走"提问即暂停"，并把「继续播放」当成"不等了直接出题"。结果：

- 翻页发生在**段落音频首帧播放时**，一暂停，第 3 页永远不到 → 等不到页码；
- 学员点「继续播放」只想继续听课，却被解释成跳过等待 → 题目在第 1 页就念了出来。

定论：暂停只由 `awaiting_answer` 触发；`waiting_slide` 与普通讲授完全一致——
音频不断、按钮不变、幻灯片跟随不冻结。等页码靠播放推进，不是靠人点。

## 4. 讲稿留底：存哪里、何时失效

存 `server/session_manager.py::_lecture_texts[sessionid]`，与 `_session_slides` 同一层级：

- 写入点唯一：`server/routes.py::human` 的 `type == 'echo'` 分支，且文本含 `PPT:` 标签
  （避免把 RAG 朗读、闲聊长句误当讲稿）；
- 读取点唯一：`quiz_lecture` 在 `text` 与 `segments` 都缺失时回退；
- 会话销毁（`remove_session`）时一并清理，避免字典随刷新无限增长。

为什么不存到 `BaseAvatar` 属性上：展示页刷新会换会话对象，属性跟着丢；
字典以 sessionid 为键，与既有 `_session_slides` 的迁移方式一致。

## 5. "继续讲授"的归属

| 场景 | 谁负责讲授 | 判分后如何继续 |
|---|---|---|
| 有讲稿 | 编排器逐段投喂 | `FEEDBACK` 念完 → `tick` 投喂下一段 |
| 无讲稿 | 展示页自身的预设演讲 | 展示页**不得**因 `in_quiz` 销毁演讲状态 |

因此 `/api/quiz/status` 增加 `has_lecture`（`len(segments) > 0`）。
展示页据此决定是否调用 `stopPresentationForQuiz()`：

```js
if (quizActive) {
    quizWasActive = true;
    if (status.has_lecture) { stopPresentationForQuiz(); }   // 编排器接管
    // 否则保留本页演讲：讲了才有"剩下的课"
}
```

提问暂停（`pauseForQuiz` / `resumeFromQuizPause`）与这套判定正交，两个场景都生效。

## 6. 兼容性

- `Stage` 新增 `waiting_slide`：前端未知阶段一律按"讲授中"渲染，不会破版；
- `has_lecture` 缺省为 `false`（旧客户端/旧响应）——按"编排器接管"处理更安全，
  因为 `stopPresentationForQuiz()` 是幂等空操作，误停只会少一段声音，不会重复朗读；
  但为了让旧行为不回归，服务端恒定返回该字段；
- `slide_probe` 注入为 `None` 时不设门槛，等价于变更前行为（单测默认如此）。

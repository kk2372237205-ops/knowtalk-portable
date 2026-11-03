# 设计

## 1. 手势为什么必须跟着「教学段」而不是「整篇讲稿」走

`[GESTURE:x]` 的语义是**段首生效、只播一遍、不继承**（见
`openspec/changes/add-gesture-module/specs/gesture-orchestration/spec.md`）：
解析器 `_parse_segments()` 已经把标签切成独立的段，每段自带 `gesture`。
提问模式要做的事只有一件——**别把这个字段扔掉**，然后在该段真正开始朗读时原样交给下游。

因此不在编排器里维护「当前手势」状态，也不在整堂课开始时设置一次；
每投喂一段就只看这一段自己的 `gesture`，自然满足"不继承"。

## 2. 分段结构用三元组，而不是新对象

`_parse_segments()` 已经返回 `[(text, slide_id, gesture), ...]`，echo 分支也按三元组解包。
提问链路此前只是用了丢字段的薄包装。把提问链路统一到三元组：

- 改动面最小：`utils/quiz_plan.py` 只读 `item[1]`，`utils/quiz_session.py` 只读 `[0]`/`[1]`，
  三元组对它们透明；
- 不需要在 `quiz_plan` / `quiz_session` 之间新增类型或序列化；
- 与既有测试里的 `segments` 字面量兼容（Python 两元组也能被 `len(item) > 2` 判断挡住）。

显式 `segments` 的兼容策略：`[文本, 幻灯片]` 与 `[文本, 幻灯片, 手势]` 都接受，
缺失的手势补 `None`；不新增必填字段。

## 3. 手势校验放在 `/api/quiz/lecture`，不依赖 `/human`

提问模式会 `flush_talk()` 清空展示页刚投喂的整篇讲稿，讲稿可能来自请求体，
也可能来自 `material_manager.get_lecture()` 的留底——**不保证再经过一次 `/human`**。
所以手势可用性校验必须在编排入口做，否则不可用手势会直达渲染层。

校验语义与 echo 分支保持一致（`server/routes.py:235-260`）：

- `partition_gestures(avatar_id, requested)` 把讲稿引用的手势分成可用 / 不可用；
- 不可用：只把**该段**的 `gesture` 置空，文字与页码照常投喂；记一条 warning 日志，
  并在响应里回 `warnings`（`code: GESTURE_UNAVAILABLE`，含缺失项与可选列表）；
- 可用：调用 `speaker.preheat_gestures(...)` 后台预热（失败只记日志，不影响开课）。

校验时机必须**早于** `QuizRegistry.start()`：`start()` 内部会立刻 `tick()` 一次、
马上投喂第一段；晚一步第一段的手势就已经带着未校验的值下发。

## 4. 预热与「首段手势」的取舍

echo 模式也是在受理阶段预热，然后才投喂。提问模式同样在 `start()` 前预热。
预热是后台线程，第一段如果正好带手势，仍可能晚几十到几百毫秒才开始动作；
这与 echo 的既有取舍一致，不额外引入"等预热完成再开课"的阻塞——
课堂开讲不能被素材解码拖住。已缓存的手势则会立即生效。

## 5. 不继承、不串场

- 题干（`_deliver_question`）、判分反馈、结束评分都只投喂 `datainfo={}`（或仅页码），
  不带手势：提问本身不该触发讲课动作。
- 手势只在该段音频首帧生效，由渲染层消费；编排器不直接调用 `request_gesture()`，
  避免"投喂时刻就切动作"这类提前触发（与 `slide_id` 的处理原则相同）。

## 6. 兼容性

| 输入 / 输出 | 旧行为 | 新行为 |
|---|---|---|
| 讲稿无 `[GESTURE]` | 两元组，无手势 | 三元组但 `gesture=None`，投喂结果不变 |
| 显式 `segments` 两元素 | 接受 | 仍接受，`gesture=None` |
| 显式 `segments` 三元素 | 被当作两元素（第三项被忽略） | 第三项作为手势生效 |
| `/api/quiz/lecture` 响应 | 无 `warnings` | 仅在手势不可用时新增 `warnings` |
| 旧前端 | — | 忽略未知字段即可，无破坏 |

## 7. 不在本次范围

- **chat 模式**仍用 `_parse_ppt_tags()`，不解析手势标签——它与本次问题无关，
  且 chat 的文本是交给 LLM 的，行为不同。
- **`quiz_pairs_generate`** 继续用 `_parse_ppt_segments()`：它只需要去掉标签后的纯文本，
  手势字段本来就该被丢弃（否则会把 `[GESTURE]` 带进 LLM 提示词或题库）。
- **手势素材的生成 / 登记 / 预览**不变。

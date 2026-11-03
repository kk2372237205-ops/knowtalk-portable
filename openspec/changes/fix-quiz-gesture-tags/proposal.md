# 提问式讲课：讲稿里的手势标签必须生效

## 问题

在提问式讲课里，讲稿中的 `[GESTURE:<id>]` 完全不起作用：讲到该段时数字人没有任何动作，
服务端日志里也看不到 `[手势标签]` 的分段记录。

同一份讲稿、同一个形象，走展示页的「连接后播放预设」（`/human` 的 echo 模式）时动作正常；
一旦由控制台启动提问式讲课，手势就消失。可见手势素材、渲染层与 `[GESTURE]` 语法本身都没问题，
丢失发生在提问模式的入口。

## 根因

提问模式在**解析讲稿的第一步**就把 `gesture` 字段丢掉了，之后整条链路都只认两个字段：

1. `server/quiz_routes.py::_build_segments()` 与 `_segments_from_stored_lecture()` 用的是
   `_parse_ppt_segments()`。这个函数（`server/routes.py:119`）是给旧调用点留的薄包装，
   只保留 `(文本, slide_id)`，而底层 `_parse_segments()` 明明已经解析出了第三个字段 `gesture`。
2. `utils/quiz_session.py::_deliver_segment()` 也按两元组解包，只把 `slide_id` 放进 `datainfo`，
   从不放 `gesture`。
3. 渲染层 `avatars/base_avatar.py` 只从音频帧的 `frame.userdata['gesture']` 取手势
   （`base_avatar.py:953`）。`datainfo` 里没有，帧上就没有，动作自然不触发。

还有一处连带影响：提问模式**绕过 `/human`**（它在 `QuizRegistry.start()` 里先
`flush_talk()` 独占朗读通道），因此 echo 分支里的手势可用性校验与预热都不会发生。
不可用的手势会一路下发到渲染层，才在后台加载时失败（只剩一条 warning）；
可用手势也不会预热，首次动作容易错过段首。

## 方案

1. **解析保留手势**：提问模式的讲稿解析统一改用 `_parse_segments()`，分段结构由
   `(文本, slide_id)` 升级为 `(文本, slide_id, gesture)`；显式传入的 `segments`
   仍接受两元素形式（`gesture` 视为 `None`）。
2. **投喂带上手势**：`QuizOrchestrator._deliver_segment()` 在该教学段开始时把 `gesture`
   放进 `datainfo`，与 `slide_id` 同一时机（该段音频首帧），且不继承到下一段。
3. **校验与预热**：启动提问式讲课时按当前形象校验手势。不可用的手势只摘除该段的手势指令，
   文字照常朗读，并在响应里回一条 `warnings`（降级但不静默）；可用的手势在开课前预热。
4. **留底路径同样保留**：`_segments_from_stored_lecture()` 回退读取的讲稿也必须带手势，
   否则「先播预设、再开课」这条现实路径仍然丢动作。

## 影响

- 提问式讲课里 `[GESTURE:x]` 的行为与 echo 模式一致：讲到该段时触发；
  题干、判分反馈、结束评分不触发动作。
- 不含手势标签的讲稿行为完全不变；两元素的 `segments` 请求保持兼容。
- `/api/quiz/lecture` 响应新增可选字段 `warnings`（仅在手势不可用时出现），
  旧客户端忽略即可。
- 不改 `/human` 契约、不改手势素材格式、不改 `[GESTURE]` 语法。

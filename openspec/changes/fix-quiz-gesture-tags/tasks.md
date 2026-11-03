# Tasks

## 1. 分段与数据通路

- [x] 1.1 `server/quiz_routes.py::_build_segments` 改用 `_parse_segments()`，分段保留第三个字段 `gesture`
- [x] 1.2 `server/quiz_routes.py::_build_segments` 的显式 `segments` 同时接受两元素与三元素（缺失手势补 `None`）
- [x] 1.3 `server/quiz_routes.py::_segments_from_stored_lecture` 回退讲稿时同样保留手势
- [x] 1.4 `utils/quiz_session.py::_deliver_segment` 兼容三元组，在该段 `datainfo` 中放 `gesture`，且不继承到下一段
- [x] 1.5 更新 `utils/quiz_plan.py` / `utils/quiz_session.py` 的模块说明，写明可选的第三个字段

## 2. 手势校验与预热

- [x] 2.1 启动提问式讲课时按当前形象 `partition_gestures()`：不可用的手势只从对应段摘除
- [x] 2.2 不可用时记 warning 日志，并在 `/api/quiz/lecture` 响应中返回 `warnings`（含缺失项与可用列表）
- [x] 2.3 可用手势在 `QuizRegistry.start()`（会立刻投喂第一段）之前调用 `preheat_gestures()`
- [x] 2.4 预热失败只记日志，不阻止开课

## 3. 测试

- [x] 3.1 `tests/test_quiz_gesture.py`：解析保留手势、显式三元组、留底回退带手势
- [x] 3.2 `tests/test_quiz_gesture.py`：段首投喂带手势、不继承、两元组兼容、题干/反馈不带手势
- [x] 3.3 `tests/test_quiz_gesture.py`：不可用手势摘除 + `warnings` + 文字照读；可用手势预热
- [x] 3.4 相关回归：`tests/test_quiz_routes.py`、`tests/test_quiz_session.py`、`tests/test_quiz_plan.py`、
      `tests/test_quiz_lecture_recovery.py`、`tests/test_quiz_slide_gate.py`、`tests/test_gesture_segments.py`、
      `tests/test_gesture_routes.py` —— 230 passed
- [x] 3.5 变异检查：去掉 `_deliver_segment` 里的 gesture 下发 → 3 项失败；
      把 `_build_segments` 改回 `_parse_ppt_segments()`（丢字段）→ 4 项失败

## 4. 验证

- [x] 4.1 全量后端测试无新增失败（`python -m pytest tests -q`：**1009 passed**）
- [x] 4.2 `openspec validate --all --strict` 通过（**39 passed, 0 failed**）
- [ ] 4.3 平台内实测：提问式讲课讲到带 `[GESTURE]` 的段落时动作触发；不可用手势有提示且不影响朗读

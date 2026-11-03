# Tasks

## 1. 页码判定基础设施

- [x] 1.1 `utils/material_manager.py` 新增 `slide_reached(current, target)`（按页序比较，缺失时按数值兜底）
- [x] 1.2 `utils/material_manager.py` 新增讲稿留底：`remember_lecture(sessionid, text)` / `get_lecture(sessionid)` / `forget_lecture(sessionid)`
- [x] 1.3 `server/routes.py::human` 的 echo 分支在文本含 `PPT:` 标签时留底

## 2. 纯提问模式按页码排序

- [x] 2.1 `utils/quiz_plan.py::assign_inline_questions`：无教学段时按 `slide_id` 升序，未指定的保持填写顺序并排最后
- [x] 2.2 排序后 `points` 与 `assignments` 仍一一对应，且题目不重复

## 3. 编排器的页码门槛

- [x] 3.1 `utils/quiz_session.py` 新增 `Stage.WAITING_SLIDE`
- [x] 3.2 构造函数接受 `slide_probe`（不含任何等待时限参数）
- [x] 3.3 `_advance()` 在 ask 步骤先算目标页码并检查；未到达则进入等待、记住待抛题目
- [x] 3.4 `tick()` 处理等待阶段：到达即抛题；**不设超时**，等不到就一直等
- [x] 3.5 `status()` 暴露 `has_lecture`、`waiting_slide` 与 `waiting_seconds`
- [x] 3.6 目标页码来源：提问点所属教学段 → 题目自带 `slide_id`；都没有则不设门槛
- [x] 3.7 等待期间每 15s 打一条「仍在等待第 N 页」的 info 日志（仅节流日志，不参与判定）
- [x] 3.8 `skip()` 在 `waiting_slide` 阶段作用为"不等了，立即抛题"，不记为未作答（服务端能力，不从播放按钮触发）
- [x] 3.9 等页码期间 MUST NOT 暂停播放：暂停只由 `awaiting_answer` 触发（一停音频，翻页也跟着停）

## 4. 讲稿回退与注入

- [x] 4.1 `server/quiz_routes.py` 新增 `_default_slide_probe(sessionid, slide_id)`
- [x] 4.2 `QuizRegistry` 接受并向下传 `slide_probe`
- [x] 4.3 `quiz_lecture`：`text`/`segments` 都缺失时回退 `material_manager.get_lecture(sessionid)`
- [x] 4.4 内联问答对的页码经 `_resolve_slide_id` 归一化后再分配
- [x] 4.5 `QuizRegistry.start()` 先取讲稿再 `flush_talk()`（回退发生在 `quiz_lecture`，flush 在 `start`，顺序天然正确）

## 5. 前端

- [x] 5.1 展示页 `onQuizStatus`：`has_lecture` 为真才 `stopPresentationForQuiz()`
- [x] 5.2 角标支持 `waiting_slide`（显示「等待翻到第 N 页」）
- [x] 5.5 ~~`waiting_slide` 阶段也切「继续播放」按钮~~（已撤销：会停掉翻页所依赖的音频，见 design 3.1）
- [x] 5.6 `waiting_slide` 阶段保持正常播放，播放按钮语义不变
- [x] 5.3 控制台状态栏在等页码时显示当前阶段
- [x] 5.4 控制台 `buildQuizPayload`：无讲稿但填了页码时，提示"按页码逐页提问"

## 6. 测试与验证

- [x] 6.1 `tests/test_quiz_slide_gate.py`：未到不问 / 到达即问 / 跳页命中 / 无限等待 / 周期日志 / 手动跳过等待 / 禁止时限回归 / 无页码不设门槛（22 项）
- [x] 6.2 `tests/test_quiz_slide_gate.py::PureQuizOrderingTestCase`：页码升序、未指定排最后
- [x] 6.3 `tests/test_quiz_lecture_recovery.py`：讲稿留底与回退、页码归一化（10 项）
- [x] 6.4 `tests/js/quiz-slide-gate-guard.test.js`：`has_lecture` 分支、角标、控制台文案、等页码不暂停（8 项）
- [x] 6.5 后端 **742 passed**、前端 `node --test tests/js/*.test.js` **185 passed**（1 项 `hook-gate-shell` 为预存在失败，与本次改动无关）、`openspec validate --all --strict` **26 passed**
- [x] 6.6 变异检查：去掉门槛 / 恢复 30s 超时兜底 / 去掉 skip 的等待分支 / 去掉等待日志 / 等页码也暂停 / 等页码时点按钮当跳过 / 改排序 / 去掉讲稿回退 / 去掉页码归一化 / 去掉留底 / has_lecture 恒真 / 无条件停演讲 → 全部被抓
- [ ] 6.7 平台内实测：填页码的题目在对应页才问；答完继续讲后面的页

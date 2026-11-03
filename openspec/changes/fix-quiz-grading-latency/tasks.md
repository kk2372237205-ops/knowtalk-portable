# Tasks

## 1. 阻塞调用移出事件循环

- [x] 1.1 新增 `server/blocking.py::run_blocking`（`run_in_executor` 薄封装）
- [x] 1.2 `server/quiz_routes.py::quiz_answer` 用 `run_blocking` 提交判分
- [x] 1.3 `server/stt_routes.py::quiz_answer_audio` 的转写与判分都走 `run_blocking`（`/api/stt/transcribe` 一并修正）

## 2. 判分本身提速

- [x] 2.1 `utils/quiz_grade.py`：`LLM_TIMEOUT` 45s → 12s，`max_retries=1`（最坏 24s，不再是 135s）
- [x] 2.2 `utils/quiz_grade.py`：按 `(api_key, base_url)` 缓存客户端，复用 TLS 连接
- [x] 2.3 `utils/quiz_grade.py`：判分完成日志带耗时（`elapsed=...s`）

## 3. 判分期间编排器不得推进

- [x] 3.1 `utils/quiz_session.py` 新增 `Stage.GRADING`；`submit_answer` 先置 `GRADING` 再判分
- [x] 3.2 `tick()` 在 `GRADING` 阶段直接返回（不投喂、不超时）
- [x] 3.3 `status()` 的 `stage` 能返回 `grading`，供展示页显示"判分中"（角标与卡片都已接上）

## 4. 提问即暂停（展示页）

- [x] 4.1 题目一呈现（`stage === 'asking'`）就切断音频（track + audio 元素）并冻结幻灯片跟随
- [x] 4.2 播放按钮在暂停态显示「继续播放」；文案统一为「暂停播放 / 继续播放」
- [x] 4.3 提交作答后自动恢复；课堂结束时恢复
- [x] 4.4 `awaiting_answer` 暂停态点击「继续播放」先 `POST /api/quiz/skip` 再恢复；
      `asking` 暂停态点击只是恢复音频听念题，**不跳过**
- [x] 4.5 冻结期间 `/api/current_slide` 轮询仍运行但不应用新页码；恢复后追平
- [x] 4.6 `asking` 阶段同样开放「回答」入口（题目已显示而音频已静音，不能让学员干等）
- [x] 4.7 编排器在 `asking` 阶段收到作答时清掉未播完的念题，使判分反馈紧随其后

## 5. 测试与验证

- [x] 5.1 `tests/test_quiz_grading_offload.py`：判分/转写在事件循环外执行，用"心跳比例"度量不阻塞
- [x] 5.2 `tests/test_quiz_grade.py` 增 `ClientReuseTestCase`：复用客户端、超时/重试上限
- [x] 5.3 `tests/test_quiz_session.py` 增 `GradingStageTestCase`：`GRADING` 不推进/不超时/重复提交被拒
- [x] 5.4 `tests/js/quiz-pause-on-question.test.js`：题目呈现即暂停/等页码不暂停/恢复/按钮文案/skip 分支
- [x] 5.4b `tests/js/quiz-panel.test.js`、`tests/js/quiz-inline.test.js`：`asking` 阶段卡片可见且可提交
- [x] 5.4c `tests/test_quiz_slide_gate.py::AnswerWhileAskingTestCase`：念题阶段提交被接受、清空未播完念题、缺 `flush_talk` 不崩（5 项）
- [x] 5.5 后端 **747 passed**、前端 **187 passed**（1 项 `hook-gate-shell` 预存在失败）、`openspec validate --all --strict` **26 passed**
- [x] 5.6 变异检查：去掉 `run_blocking`、去掉 `GRADING` tick 守卫、不先切 `GRADING`、转写回同步、
      暂停退回只认 `awaiting_answer`、念题点「继续播放」当跳过、念题不开放作答、面板 `canAnswer` 退回单阶段、
      念题提交被拒、不清空念题 → 全部被抓
- [ ] 5.7 平台内实测：提问时音频停止、幻灯片停住、按钮变「继续播放」；作答后自动恢复并继续讲课

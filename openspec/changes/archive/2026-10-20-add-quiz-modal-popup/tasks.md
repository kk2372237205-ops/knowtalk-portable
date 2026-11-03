# Tasks

- [x] 1. 在 `web/index.html` 把 `#quizPanelHost` 改为全屏模态遮罩样式，并为 `.quiz-answer-mode-switch` 增加样式
- [x] 2. `web/quiz-panel.js` 的 `buildCard` 增加"文字 / 语音"作答方式切换，并保持 `parts.input` / `parts.recordBtn` 始终在 DOM
- [x] 3. `web/quiz-panel.js` 在挂载卡片时给 host 加 `quiz-modal-open`、卸载时移除（带 `classList` 守卫）
- [x] 4. 新增测试：弹窗仅在 `awaiting_answer` / `feedback` 出现、普通模式不出现；作答方式可切换且两种底层元素始终存在
- [x] 5. 运行 `node --test tests/js/quiz-panel.test.js` 与 `openspec validate --all --strict`，全绿
- [ ] 6. 浏览器内真机走查（弹窗遮罩、麦克风录音）后复核 `README.md` / `docs/` 口径并归档本变更

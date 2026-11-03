# Design

## Decisions

### 决策 1：弹窗用 CSS 把现有 host 变成模态，而不是新建 body 级节点

`buildCard` 当前把卡片 `appendChild` 到注入的 `host`。测试断言 `host.children[0].__parts` 存在，因此**必须保持卡片挂在 host 上**。弹窗效果改成：把 `#quizPanelHost` 用 CSS 做成 `position: fixed; inset:0` 的全屏遮罩，卡片在其中居中。这样零 DOM 结构改动、测试不受影响。

- host 默认 `display:none`；`quiz-panel.js` 在挂载卡片时 `host.classList.add('quiz-modal-open')`、卸载时 `remove`。
- `classList` 操作在测试假节点上不存在，用 `if (host.classList)` 守卫，避免破坏 Node 测试。
- 遮罩不响应点击关闭（题目是必交互），只由提交 / 跳过 / 结束触发卸载。

### 决策 2：作答方式做成"切换"而非"并排"

卡片内加一个分段控件（文字 / 语音）：

- 默认 `文字`：显示 textarea + 提交按钮，隐藏语音区。
- 切到 `语音`：显示"点击开始录音"按钮区，隐藏 textarea。
- 两个底层元素（`parts.input`、`parts.recordBtn`）**始终留在 DOM 中**，只切换 `style.display`，因此现有测试对 `input`/`recordBtn` 的存在性与 `disabled` 断言不受影响。
- `state.answerMode` 记录当前模式，供后续断言与焦点管理使用。

### 决策 3：不新增后端字段

语音 / 文字的提交路径与 `add-quiz-mode` 完全一致，本变更不新增任何接口或请求字段。

## Risks

- **[弹窗遮挡其它控件]** → 遮罩 `z-index` 取页面最高层之一（1050），且只在有题目时出现；普通模式 `display:none` 不占层。
- **[测试假节点无 classList]** → 所有 `classList` 操作加存在性守卫。
- **[显隐时序与轮询竞争]** → 显隐只由 `applyStatus` 的 `VISIBLE_STAGES` 驱动，与现有单定时器逻辑解耦，不引入新定时器。

## Migration

1. `web/index.html`：在 quiz 样式区新增 `#quizPanelHost` 模态样式与 `.quiz-answer-mode-switch` 相关样式。
2. `web/quiz-panel.js`：`buildCard` 增加作答方式切换两段 UI 与事件；`unmountCard`/`buildCard` 维护 `quiz-modal-open` 类。
3. 跑 `node --test tests/js/quiz-panel.test.js` 与新增弹窗/切换断言，全绿后归档。

回滚：删除上述 CSS 与 `quiz-panel.js` 中切换/类操作即可，接口与状态机不受影响。

# Design

## 1. 双段朗读的根因与"单驱动"修复

### 根因

同一会话存在两个独立的朗读驱动源：

- **展示页驱动**：`knowtalk-chat.html` 的 `startPresentation()` 在「连接后播放预设」时于连接后 500ms 自动触发，把整篇讲稿经一次 `POST /human`（`type=echo`，`interrupt=true`）投喂给该会话的 TTS 队列。
- **编排器驱动**：控制台点「开始提问式讲课」后，`QuizRegistry.start()` 立即 `tick()`，`QuizOrchestrator` 按教学段逐段 `put_msg_txt`。

两条路的末端都是同一个 `avatar_session` 的 TTS 队列，且编排器的 `put_msg_txt` **不会**打断已在队列里的展示页音频，因此两篇讲稿叠加播放。

### 修复

- **服务端（权威修复）**：`QuizRegistry.start()` 在创建编排器、首次 `tick()` **之前**，若 `speaker` 提供 `flush_talk()` 则先调用。`BaseAvatar.flush_talk()` 会清空 TTS/ASR 队列并递增 `speech_request_id`，使展示页那次请求的后续入队全部失效（`/human` 已按 `speech_request_id` 做了"旧请求不再入队"的保护）。这一步在服务端关闭了"旧队列"这一路。
- **展示页（状态一致）**：展示页轮询 `/api/quiz/status`，一旦 `in_quiz === true`，把本地 `presentationPlaying` 置 false、`presentationPaused` 置 false 并 `updatePlaybackButton()`，不再调用 `sendPresentationFrom`；`startPresentation()` 在 `in_quiz` 时直接拒绝启动。这样即使队列被清空，展示页的"暂停/继续演讲"按钮状态与真实情况一致，且不会在提问过程中重新灌入整篇讲稿。

> 为什么不在编排器里给 `put_msg_txt` 加 interrupt 参数：`put_msg_txt(text, datainfo)` 是多个调用方共享的稳定契约（`/human`、`llm.py`、手势模块）。改签名会扩散影响面。`flush_talk()` 已经是公开的"打断"入口，复用它最小侵入。

## 1c. 会话对齐：为什么提问必须开到展示页的会话

`server/rtc_manager.py::handle_offer` 对每条 WebRTC 连接都 `session_manager.create_session(params)`，只把请求里的 `sessionid` 当"前端上下文"打印，**并不复用**。因此：

- 控制台 `index.html` 的 `/offer` 得到会话 A；
- 控制台打开 `knowtalk-chat.html?sessionid=A`，展示页自己再 `/offer` 得到**会话 B**（A≠B）；
- 若提问用控制台的 A 启动，编排器 `put_msg_txt` 投给 A 的 TTS，音频只在控制台那个隐藏 `<audio>` 里响；展示页轮询 B，永远看不到 `in_quiz`，弹窗自然不出现。

修复：展示页在 `/offer` 成功后把 B 写入 `localStorage('knowtalk_live_sessionid')`；控制台启动/停止提问时优先用它作为目标会话；展示页以自己的 B 轮询状态并弹窗。展示页窗口已打开但尚未公布 B 时，控制台提示等待，避免误开在 A 上。

> 为什么不直接让 `/offer` 复用传入的 sessionid：`rtc_manager.py` 的注释明确说明，多连接共享一个 session 会让 `output._player` 互相覆盖，造成音频队列欠载、重复播放与电流声。会话隔离是刻意设计，不能回退。

## 2. 展示页问答弹窗

- `knowtalk-chat.html` 引入 `/quiz-panel.js`，增加一个 `#quizPanelHost` 宿主（默认 `display:none`，模态打开时 `display:flex`），样式与 `index.html` 的 `.quiz-card` / `#quizPanelHost` 保持一致。
- 面板实例化时 `getSessionId` 指向展示页既有的 `getSessionId()`（`parentSessionId || #sessionid`）。
- **轮询策略**：面板本身只在进入可见阶段时维护内部定时器；展示页需要一个"常驻探测器"来发现提问**开始/结束**。为此给 `createQuizPanel` 增加 `keepPolling` 选项（默认 `false`，保持控制台行为不变）：开启后，在 `!in_quiz` 或 `finished` 时不停表，而是降到慢速间隔继续轮询，从而在后续任一次提问开始时能自动弹出。
- 语音作答沿用既有 `/api/quiz/answer/audio`（`stt_routes.py`）路径，不新增后端契约。

## 3. 由预设文本生成问答对

- 新接口 `POST /api/quiz/pairs/generate`（**课堂面，免口令**）：
  - 入参：`text`（必填，可含 `[PPT:N]`/`[slide:N]` 标签）、`count`（每段出题数，默认 1）、`context`（可选）。
  - 处理：用 `server/routes.py::_parse_ppt_segments` 切段 → 对每个非空段调用 `utils/quiz_generate.generate_questions(segment_text, count, context)` → 给每条结果附上该段的 `slide_id` → 聚合返回。
  - 出参：`{"pairs": [{"question","reference_answer","rubric","slide_id"}]}`。
  - **不落库**：与 `quiz_bank_generate` 的默认行为一致，避免"生成即污染题库"；需要入库时走既有受口令的 `/api/quiz/bank/generate`。
- **口令边界**：`/api/quiz/pairs/generate` 属于发起讲课前的**课堂面**辅助（与 `/api/quiz/lecture` 同为免口令），因为它不写题库、只回给当前页面；真正落库的 AI 出题仍是受保护的 `/api/quiz/bank/generate`。`server/auth.py` 的 `PROTECTED_PREFIXES` 不含该路径，天然免口令。
- 生成器可注入（`QuizRegistry.generator`），便于测试；未注入时走真实 `utils.quiz_generate.generate_questions`。

## 4. 未连接可配置

- 控制台的提问模式参数与问答对写入 `localStorage`（键 `zhike_quiz_config`），页面加载时回填；这些操作都只依赖 DOM 与本地存储，不需要 `sessionid`。
- "开始提问式讲课"仍需会话（编排器需要 `speaker`），这是物理约束，不改变；文案明确区分"未连接可先配置"与"连接后才能开始"。

## 风险与取舍

- `flush_talk()` 会连正在合成的音频一起打断。这是"提问模式接管朗读"的预期语义；启动提问式讲课本就是一种接管动作。
- 展示页 `keepPolling` 会在提问未开始时也保持慢速轮询（约 2s 一次），开销可接受。
- 自动生成问答对依赖 LLM 配置；未配置时接口返回明确错误，前端提示"先去设置页配置 LLM"，不静默失败。

# Tasks

## 1. 单驱动：提问模式独占朗读（防双段）

- [x] 1.1 `server/quiz_routes.py::QuizRegistry.start` 在创建编排器前，若 `speaker` 有 `flush_talk()` 则调用，清空旧队列并作废旧朗读请求；`start` 仍立即 `tick()` 投喂第一段。验证：单测 `test_start_flushes_pending_queue_before_delivering` 断言 `flush_talk` 被调用一次且首段仍被投喂
- [ ] 1.2 `web/knowtalk-chat.html` 在检测到 `in_quiz === true` 时停止本地预设演讲（`presentationPlaying=false`、`presentationPaused=false`、`updatePlaybackButton()`），并在 `startPresentation()` 入口加 `quizActive` 守卫、自动预设回调也加守卫。验证：人工走查（展示页在提问模式下不再自动/手动灌入整篇讲稿）

## 1b. 退出提问模式真正停止编排

- [x] 1b.1 `server/quiz_routes.py` 新增 `quiz_stop` 与路由 `POST /api/quiz/stop`（课堂面，免口令）：移除该会话编排器并 `flush_talk()` 清空待播。验证：单测 `StopQuizTestCase` 断言退出后 `get(sessionid)` 为空、`status.in_quiz=false`、`flush_talk` 被调用、其它会话不受影响
- [x] 1b.2 `web/index.html::stopQuizMode` 调用 `/api/quiz/stop`（有会话时），再销毁前端面板。

## 1c. 提问作用到展示页会话（会话对齐）

- [x] 1c.1 `knowtalk-chat.html` 在 `/offer` 成功后把真实会话 id 写入 `localStorage('knowtalk_live_sessionid')`，断开时清除。验证：人工走查（展示页显示「已连接」后 localStorage 有该键）
- [x] 1c.2 `index.html` 新增 `quizLiveSessionId`/`quizTargetSessionId`；`startQuizLecture` 的 payload 带 `sessionid=展示页会话`，`stopQuizMode` 以同一会话停止；展示页未就绪时明确提示且不退回控制台会话。验证：人工走查（提问开在展示页会话，弹窗出现在展示页）
- [x] 1c.3 `quiz-panel.js` 的 `startLecture` 支持 `payload.sessionid` 显式指定目标会话；未指定时行为不变

## 2. 展示页作答入口（对话栏内联）

- [x] 2.1 `web/knowtalk-chat.html` 引入 `/quiz-panel.js`，新增 `#quizPanelHost` 宿主与模态样式（对齐 `index.html`）。验证：未启用提问模式时页面无卡片、无遮罩
- [x] 2.2 `knowtalk-chat.html` 的 `getSessionId` 指向既有 `getSessionId()`；连接成功后 `startQuizPolling()`，断开时 `stopQuizPolling()`。验证：进入 `awaiting_answer` 时展示页能感知到当前题目
- [x] 2.3 `web/quiz-panel.js` 新增 `keepPolling`、`onStatus` 选项（默认 false/null）：`keepPolling` 开启后在 `!in_quiz`/`finished` 时降速继续轮询；`onStatus` 每次状态回调宿主。验证：node:test `展示页：keepPolling ...` 通过
- [x] 2.4 `web/quiz-panel.js` 新增 `mode: 'inline'` 与 `onQuestion`/`onResult`/`onReset` 钩子：inline 模式下不创建任何卡片、不触碰 `quiz-modal-open`；同一题（`completed + 题干` 键相同）只回调一次 `onQuestion`。验证：`tests/js/quiz-inline.test.js` 断言无卡片挂载、重复轮询只回调一次、换题再回调
- [x] 2.5 `web/quiz-panel.js` 抽出 `submitAnswer(text)` 公开方法（`submitText` 改为读卡片输入后转调），供对话栏复用。验证：单测断言请求体含 `sessionid`/`answer`，空作答不发请求
- [x] 2.6 `knowtalk-chat.html` 用 `mode:'inline'` 建面板：`onQuestion` 把「第 N/M 题 + 题干」追加到右侧对话栏（对话栏折叠时自动展开），`onResult` 追加判分与反馈，`onStatus` 在 `awaiting_answer` 时把 `#btnSend` 文案切为「回答」、输入提示切为作答提示，`onReset`/断开时恢复「提问」。验证：静态守卫测试 + 人工走查
- [x] 2.7 `knowtalk-chat.html::sendText` 在作答态分流到 `quizPanel.submitAnswer`（不走 `postHuman`/RAG）并立即恢复按钮文案；`finishVoice` 改为填入输入框后统一走 `sendText`，语音与文字共用同一提交路径。验证：静态守卫测试 + 人工走查

## 3. 由预设文本生成问答对

- [x] 3.1 `server/quiz_routes.py` 新增 `quiz_pairs_generate` 与路由 `POST /api/quiz/pairs/generate`：解析 `text` 分段，逐段调用生成器（默认 `utils.quiz_generate.generate_questions`），附 `slide_id` 聚合返回 `{pairs:[...]}`；不落库；缺 `text` 报错；生成器异常返回业务错误不崩溃。验证：`PairsGenerateTestCase` 覆盖分段与 `slide_id` 归属、未落库、错误透传、免口令
- [x] 3.2 `web/index.html` 新增「根据预设文本生成问答对」按钮：读取 `#presetText`，调 `POST /api/quiz/pairs/generate`，填入 `createPairEditor`。未连接数字人时也可用。

## 4. 未连接可配置

- [x] 4.1 `web/index.html` 将提问模式参数量（数量/方式/页码范围）与已填问答对持久化到 `localStorage`（`zhike_quiz_config`），页面加载回填。
- [x] 4.2 文案区分"未连接可先配置"与"连接后才能开始"；`startQuizLecture` 在无会话时给出明确提示。

## 5. 回归与校验

- [x] 5.1 `tests/test_quiz_routes.py` / `tests/test_quiz_session.py`（83 passed）与 JS（28 passed）全绿
- [x] 5.2 全量 pytest **627 passed**；quiz 相关 JS 28 passed（`hook-gate-shell.test.js` 的"在其它项目中不注入、不拦截"为**既有失败**，与本次无关，未改 `.dsh/`）
- [x] 5.3 `openspec validate --all --strict` 通过（22 passed）
- [ ] 5.4 平台内端到端手测：连接（含"连接后播放预设"）→ 开始提问式讲课 → 确认只念一遍、右侧对话栏出现题目、按钮变「回答」、文字与语音均可作答、判分结果回到对话栏

## 6. 刷新续接与连接前预配置（现场反馈三项）

- [x] 6.1 `utils/quiz_session.py::QuizOrchestrator.rebind(sessionid, speaker)`：把编排器接到新会话，保留进度/当前题目/等待作答起点。验证：`tests/test_quiz_session.py::RebindTestCase`（换通道后推进落在新会话、等待态与题目保留）
- [x] 6.2 `server/quiz_routes.py`：`QuizRegistry.migrate(old, new, speaker)` 迁移编排（新会话拿不到 speaker 时留在原处）；`tick_all` 在会话已消失时**暂停推进**而不是继续投喂（`session_alive` 可注入，注入 `session_lookup` 的单测默认不检查存活）。验证：`SessionMigrationTestCase`（迁移保留题目、作答反馈念在新会话、旧会话消失暂停）
- [x] 6.3 `server/rtc_manager.py::_migrate_quiz_session`：`/offer` 带 `carryover_sessionid` 时迁移；判定依据是**旧会话有没有活跃 PeerConnection**（不是会话对象是否存在——刷新时对象还在，只看对象会把刷新误判成"旧页面仍在用"）；旧连接尚未判定断开时退避重试（`QUIZ_CARRYOVER_RETRIES`/`DELAY`）；异常不得影响 `/offer`。验证：`tests/test_rtc_quiz_carryover.py` 8 项（含"会话对象仍在但连接已断"回归用例）
- [x] 6.4 `web/knowtalk-chat.html`：`/offer` 带 `carryover_sessionid`（来自 `localStorage.knowtalk_live_sessionid`）；续接值**只在出画面后**推进（`markVideoReady`），避免重试时拿空会话续接；自动播放预设前先确认一次提问状态（`waitQuizStatusThen`，最多 1.5s）。验证：`tests/js/quiz-refresh-and-preconfig-guard.test.js`
- [x] 6.5 `web/quiz-panel.js` / `knowtalk-chat.html`：题干交付不再限定阶段（只要有题干就显示），并兼容 `question`/`text` 两种字段名。验证：`tests/js/quiz-inline.test.js` 两条新用例
- [x] 6.6 `web/index.html`：`startQuizLecture` 未连接时登记「待启动」（`zhike_quiz_pending`）+ `watchPendingQuiz` 自动开始，`stopQuizMode` 清除；初始化移到脚本末尾（避免常量 TDZ），问答对输入即时持久化、无记录时给一行空行。验证：同上守卫测试 + 人工走查
- [x] 6.7 开课即显示：控制台开课成功后写跨标签页信号（`zhike_quiz_signal`），展示页 `storage` 监听立即查一次状态；展示页在 `in_quiz=true` 但暂无题干时角标显示「等待题目」。验证：`quiz-refresh-and-preconfig-guard.test.js` 两条守卫
- [x] 6.8 `web/index.html::launchQuiz` 与 `quizPanelInstance` 统一为 inline 模式（控制台不再弹模态框，只作状态机与进度显示），待启动自动开课复用同一 `launchQuiz` 路径（含开课信号）。验证：静态守卫 + 人工走查
- [x] 6.11 提问状态探测加守护：连接/重连路径上任一步没走通都不得让轮询永久停摆——`resetPeer` 后由 2s 守护定时器在"有会话 id 但轮询未运行"时自动重启；`storage` 信号也走同一入口。验证：`tests/js/quiz-inline-ui-guard.test.js`（守护存在 + 与 resetPeer 解耦）
- [x] 6.10 刷新迁移的"占用"判定改用**客户端主动释放**：`POST /api/quiz/release` + 展示页 `pagehide` 用 `sendBeacon` 上报；服务端 `_session_in_use` 认可释放标记，该会话重新建连时清除标记；重试窗口 3 次→10 次。验证：`tests/test_rtc_quiz_carryover.py`（释放后立即迁移、重连清标记、重试次数）
- [x] 6.9 陈旧会话 id 自愈：展示页加载时先清掉上一次公布的会话 id；`launchQuiz` 遇到「会话不存在」MUST 清掉陈旧 id、重新登记待启动并继续等待，MUST NOT 报错中断；控制台 notify 去掉 alert。验证：`tests/js/quiz-refresh-and-preconfig-guard.test.js` 新增守卫

## 7. 判分与"继续讲授剩余内容"

- [x] 7.1 `web/index.html`：新增 `currentLectureText()`（预设文本为空时回退到「连接后播放预设」选中的文本），`buildQuizPayload` 改用它；`persistQuizConfig`/`restoreQuizConfig` 一并持久化预设文本，刷新不丢
- [x] 7.2 `web/index.html`：无讲稿时状态栏明确提示"本次只提问、不讲授剩余内容"，不再静默变成纯提问
- [x] 7.3 `web/knowtalk-chat.html`：控制台已登记待启动提问（`zhike_quiz_pending`）时，展示页不得自行播放整篇预设（防与编排器重复朗读）
- [x] 7.4 验证：`tests/js/quiz-inline-ui-guard.test.js` + `tests/js/quiz-refresh-and-preconfig-guard.test.js` 新增守卫；后端 `tests/test_quiz_session.py` 的「判分后继续讲授」用例保持通过

## 8. 结束评分与自动收起对话栏

- [x] 8.1 `utils/quiz_score.py`：`summarize(records)` 按 100/60/0 口径算百分制总分与题数明细；`format_summary()` 生成一句可朗读的中文。验证：`tests/test_quiz_score.py`
- [x] 8.2 `utils/quiz_session.py::_finish`：把评分念出来，`status()` 返回 `summary`；无记录时不念不评。验证：`tests/test_quiz_session.py`
- [x] 8.3 `server/quiz_routes.py`：`GET /api/quiz/report?sessionid=`（编排器已销毁也能按历史记录算）。验证：`tests/test_quiz_routes.py`
- [x] 8.4 `web/knowtalk-chat.html`：提问结束（进行中→结束）时取评分、追加评分气泡，3 秒后收起对话栏；刷新不重复收起。验证：`tests/js/quiz-inline-ui-guard.test.js`

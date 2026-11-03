# 设计：判分提速与「提问即暂停」

## 决策 1：阻塞调用用线程池卸载，而不是改成异步 SDK

`openai` 有 `AsyncOpenAI`，但 `faster-whisper` 没有异步版本；
两者混用会让「作答」这条链路出现两套写法。统一用
`loop.run_in_executor(None, fn, *args)`（默认线程池）最省心：

```python
# server/blocking.py
async def run_blocking(fn, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(fn, *args, **kwargs))
```

- 线程池是 asyncio 默认池，答题是低频操作，不会挤占；
- 协程在此期间 `await`，事件循环照常处理 aiortc 与轮询请求。

**拒绝的方案**：把 `submit_answer` 直接改成 `async def`。
判分要跨线程回写编排器状态，`tick()` 又在另一个协程里读，
反而需要额外加锁；保持 `submit_answer` 同步、只把调用点包进线程池，
状态机一行不用改。

## 决策 2：给判分设硬上限

| 参数 | 旧值 | 新值 | 理由 |
|---|---|---|---|
| `LLM_TIMEOUT` | 45s | 12s | 实测正常 1s，12s 足够覆盖慢网络 |
| `max_retries` | 2（SDK 默认） | 1 | 最坏 12×2=24s，不再出现 135s |

超过上限抛 `QuizGradeError`，编排器照原有降级路径走（`degraded=True`），
课堂继续。**宁可降级也不要卡住**——这是提问模式的既定原则。

## 决策 3：client 复用

按 `(api_key, base_url)` 缓存一个 `OpenAI` 实例，避免每次判分重新
建连（首次实测 TLS +2s）。缓存只在模块级，不跨进程。

## 决策 4：提问即暂停的状态放在展示页

编排器只负责"不再投喂下一段"，它**不该知道浏览器有没有静音**。
所以暂停是纯前端状态：

```
quizAudioPaused = false          // 是否因提问而停住
quizSlideFrozen = false          // 冻结 /api/current_slide 的跟随
```

题目一呈现（`asking`）就停，`awaiting_answer` 当然也停：

1. `remoteAudioTrack.enabled = false` + `audio.pause()` → 立即静音；
2. `quizSlideFrozen = true` → 轮询仍跑，但不再应用新页码（停在当前页）；
3. `presentationWaitingResume = true` → 播放按钮显示「继续播放」。

**`waiting_slide` 阶段绝不暂停**：那一页是等段落音频首帧播放才切过去的，
此刻停音频 = 页码永远到不了，自己把自己等死（已实修一次，见
`fix-quiz-slide-gating` 的「等待页码期间不得暂停」）。

恢复有三条入口，都走同一个 `resumeFromQuizPause()`：

- 学员提交作答（文字/语音）→ 立即恢复，好让判分反馈被听到；
- 学员点「继续播放」→ 只在 `awaiting_answer` 暂停态才 `POST /api/quiz/skip`
  记为未作答再恢复；`asking` 暂停态点它只是"我想听念题"，直接恢复音频即可，
  跳过会让题目被记为未作答；
- 课堂结束（`finished`）→ 恢复，让结束评分被听到。

### 为什么连念题阶段也要能作答

题目已经显示在对话栏，音频却已静音——若作答入口要等"念完"才开，学员只能
对着静音画面干等，而念题进度在静音后不可见。所以 `asking` 阶段同样开放
「回答」，并由编排器在收到作答时清掉未播完的念题，让反馈紧随其后。

### 为什么不暂停 `<audio>` 而只关 track？

历史实现里 `audio.pause()` 会破坏 WebRTC 音频缓冲（见 `postHuman` 注释）。
本次两者都做，但**判定以 track 为准**：`enabled=false` 立即生效且可无损恢复，
`pause()` 只是防止元素继续解码。

## 决策 5：按钮文案统一为「暂停播放 / 继续播放」

原「暂停演讲 / 继续演讲」在提问场景里语义不准（暂停的不是演讲，是整条播放）。
统一改文案，状态机不变。

## 风险

- **线程池并发**：若学员连点两次提交，两个线程可能同时通过
  `stage == AWAITING_ANSWER` 检查 → 重复记录。修法：`submit_answer` 先置
  `Stage.GRADING` 再进线程，重入直接返回 False。
- **恢复后音画不同步**：暂停期间服务端仍在推流，恢复时音频是"实时"的，
  会跳过暂停期间的内容。这是"切断"的必然代价，且暂停期本来就没有新内容
  （编排器在等作答），可接受。

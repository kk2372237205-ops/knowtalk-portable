# Design

## Context

动机与实测证据见 `proposal.md` —— Why。

### 相关代码的现状

`llm.py` 的 `llm_response()` 里有三条与记忆相关的语句：

- 第 71-79 行：取出（或初始化）`avatar_session.chat_history`，追加本次 `user` 消息，并截断到最近 `history_max_rounds * 2` 条。
- 第 91-112 行：流式消费期间把 `result` 里已完整的句子抽走送入 TTS，`result` 只留未成句的尾巴。
- 第 115-125 行：流结束后 `take_complete_sentences(result, force=True)` 把尾巴也送走，然后再尝试把回答写进记忆。

`take_complete_sentences(buffer, force=True)` 的语义是「把剩余内容也算作一段并清空剩余」：

```python
    if force and remainder.strip():
        segments.append(remainder.strip())
        remainder = ''
    return [segment for segment in segments if segment], remainder
```

因此第 115 行执行后 `result` 必然是 `''`，第 123 行的 `if result.strip()` 恒为假 —— 第 124、125 行是死代码。

### 可用的既有数据

`avatar_session.llm_text` 在整个流式过程中被逐片累加（第 103 行 `avatar_session.llm_text += msg`），并且在函数入口被重置为空串（第 51 行）。它就是本轮回答的**完整纯文本**，也是前端气泡显示的内容。因此不需要额外变量来记录回答全文。

### 约束

- 不改变 `chat_history` 的元素结构（仍然是 `{'role', 'content'}`）、不改变它所挂载的对象、不改变 `CHAT_HISTORY_ROUNDS` 的语义与默认值。
- 不改变任何 HTTP 接口契约。
- 被打断的回答仍然不得入史（现有守卫的语义要保留）。

## Goals / Non-Goals

**Goals:**

- 让一次成功且未被中断的回答真正写入会话历史，使多轮记忆与 README 的承诺一致。
- 让「最近 N 轮」的截断逻辑恢复为有效代码。
- 用一条可执行的两轮验证把结果钉死，避免再次悄悄退化。

**Non-Goals:**

- 不改用外部存储或把记忆持久化到磁盘（仍然只活在会话对象里，随会话回收消失）。
- 不改 `CHAT_HISTORY_ROUNDS` 的实现方式（仍然是每次调用时读环境变量）。
- 不修 `clean_llm_text` 逐片清洗 Markdown 可能跨片失效的问题（属于独立问题）。
- 不调整系统提示词或回答风格。

## Decisions

### 决策 1：用 `avatar_session.llm_text` 作为回答全文来源，而不是新加变量

备选方案：

- **新加 `full_reply` 变量**：在循环里同时累加。可行，但与 `llm_text` 完全重复，多一处可能不同步的状态。
- **复用 `llm_text`**：它本来就是本轮回答的完整纯文本，且已被前端与 TTS 共同使用。采用。

依赖它是安全的：`llm_response()` 入口就把 `llm_text` 置空，且同一个 `avatar_session` 不会被两个并发的 `llm_response` 同时处理（调用方通过 `speech_request_id` 做互斥判断）。

### 决策 2：保留「被打断不入史」的守卫，并把截断改成基于历史自身

原实现的守卫与截断都挂在 `result.strip()` 上，一旦这个前提失效就一起失效。修改后：

- 守卫改为判断 `datainfo.get('_speech_request_id') == avatar_session.speech_request_id`，与流式循环里用的是同一个判据，语义一致且不依赖局部变量。
- 截断直接对 `avatar_session.chat_history` 做，写成 `avatar_session.chat_history = avatar_session.chat_history[-(history_max_rounds * 2):]`，不再依赖 `history` 这个局部别名。

### 决策 3：回答为空时不写入

如果模型没有产出任何正文（例如被上游错误中断、或全部内容都是思考过程），则不追加空白的 `assistant` 条目，避免给下一轮塞入一条空消息。

### 决策 4：规格侧收紧要求并新增三个场景

现有要求只写了「保留最近 N 轮（一问一答为一轮）」，字面上允许只存用户提问。修改后明确「历史 MUST 由用户提问与助手回答交替组成」，并新增三个场景：回答写入记忆、多轮后角色交替、被打断的回答不入史。这样这条要求可以直接对着测试写，不会再出现"实现不违反字面要求但特性其实是坏的"。

## Risks / Trade-offs

- **[历史变长导致 LLM 输入变大]** → 截断仍然是最近 `2N` 条（默认 20 条），与原来「最多 2N 条用户消息」的上限一致，token 量不会超过原先的上限。
- **[写入的记忆与朗读文本不一致]** → 两者都取自同一次流式累加的结果经 `take_complete_sentences` 切分，来源一致；验证任务里显式比对完整回答与 `llm_text`。
- **[打断时序竞态]** → 守卫与流式循环使用同一个 `speech_request_id` 判据；被中断时函数会提前 `return`，根本走不到写记忆的代码。
- **[修改核心对话文件]** → 改动被限制在 `llm.py` 末尾的 3 行内，且不引入新依赖、不改函数签名。

## Migration Plan

1. 修改 `llm.py` 的记忆写入与截断逻辑。
2. 用两轮真实调用验证历史角色序列变为 `user, assistant, user, assistant`，且 `assistant` 内容等于本轮 `llm_text`。
3. 启动主服务确认 `/api/status` 与问答链路不受影响。
4. 归档本变更，使收紧后的要求进入主规格。

回滚：改动只在 `llm.py` 的几行内，`git revert` 即可；对外行为回到「只存用户提问」。

## Open Questions

无。

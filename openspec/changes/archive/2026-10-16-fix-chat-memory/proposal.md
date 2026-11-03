# Proposal

## Why

README 把「多轮对话记忆（P1）」列为教学增强特性，但实测该特性只工作了一半：**助手的回答从未写入会话记忆**。

实测证据（直接调用 `llm.py` 的真实代码路径，连续两轮 chat 请求）：

```
第一轮后 chat_history 角色 = ["user"]
第二轮后 chat_history 角色 = ["user", "user"]
assistant 条目是否存在     = false
```

根因在 `llm.py`：

- 第 115 行 `segments, result = take_complete_sentences(result, force=True)` 会把 `result` 排空成**空串**（`take_complete_sentences` 在 `force=True` 时把剩余内容塞进 `segments` 并令 `remainder = ''`）。
- 因此第 123 行的守卫 `if result.strip() and ...` 恒为假，第 124 行真正追加 `assistant` 条目的语句**永远不会执行**，第 125 行的历史截断同样是死代码。

后果：模型只能看到用户自己之前问过什么，看不到自己回答过什么。追问诸如「刚才那个概念再展开讲讲」时，数字人无法引用自己的上一轮输出；「最近 10 轮」实际只有用户侧的 10 条提问。同时 `openspec/specs/digital-human-api/spec.md` 中「最近 N 轮（一问一答为一轮）」的描述与实现不符，属于我在建立规格基线时照着代码意图而非实测行为书写导致的偏差。

## What Changes

- 修正 `llm.py`，让一次成功且未被后续请求打断的回答真正写入会话历史，并把「最近 N 轮」的截断逻辑恢复为有效代码。
- 收紧 `digital-human-api` 能力规格中的对应要求（MODIFIED）：明确历史 MUST 由用户提问与助手回答**交替**组成，并补一条直接针对本缺陷的场景。
- **BREAKING**：无。对外接口、参数与响应结构均不变；变化只发生在会话内部的历史内容上——而这正是修复的目标。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `digital-human-api`: 「chat 模式的会话级多轮记忆」这条要求被收紧——现有描述只说了「保留最近 N 轮（一问一答为一轮）」，没有明确回答必须入史，导致实现可以在不违反字面要求的情况下只存用户提问。修改后明确要求历史由 user/assistant 交替组成，并新增场景锁定「回答写入记忆」。

## Impact

- **修改文件**：`llm.py`（仅写记忆的那几行）；`openspec/specs/digital-human-api/spec.md`（经本变更的 delta 在归档时更新）。
- **不受影响**：所有 HTTP 接口契约、TTS 与口型链路、`chat_history` 的存储位置与结构（仍然是 `[{role, content}, ...]`，仍然挂在 `avatar_session` 上）、`CHAT_HISTORY_ROUNDS` 的语义与默认值。
- **用户可见变化**：追问时数字人能引用自己上一轮的回答；送入 LLM 的历史条数从「最近 2N 条用户提问」变为「最近 N 轮问答」。
- **依赖新增**：无。

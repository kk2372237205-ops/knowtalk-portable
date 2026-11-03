# Spec Delta

## MODIFIED Requirements

### Requirement: chat 模式的会话级多轮记忆

chat 模式 MUST 按会话隔离对话记忆，不同 `sessionid` MUST NOT 共享历史。每个会话的历史 MUST 由用户提问与助手回答交替组成：一次成功且未被后续请求打断的回答，MUST 在生成结束后作为 `assistant` 条目追加到该会话历史，内容为该次回答的完整纯文本。服务 MUST 只保留每个会话最近 N 轮（一问一答为一轮，即最近 2N 条消息），N 由环境变量 `CHAT_HISTORY_ROUNDS` 控制、默认 10。历史 MUST 一并作为 LLM 输入，且 MUST NOT 被复述进回答文本。

#### Scenario: 记忆按会话隔离

- **WHEN** 会话 A 提问后再由会话 B 提问
- **THEN** 会话 B 的 LLM 输入不包含会话 A 的历史

#### Scenario: 历史轮数上限

- **WHEN** 某会话的对话轮数超过 `CHAT_HISTORY_ROUNDS` 配置值
- **THEN** 只有最近 N 轮历史被送入 LLM

#### Scenario: 回答写入会话记忆

- **WHEN** 某会话完成一次未被后续请求打断的 chat 回答
- **THEN** 该会话历史的最后一条 MUST 是 `assistant` 条目
- **AND** 该条目的内容 MUST 等于本次回答的完整纯文本，且 MUST NOT 含 Markdown 标记

#### Scenario: 多轮之后历史交替出现

- **WHEN** 同一会话连续完成两轮未被中断的问答
- **THEN** 该会话历史的角色序列 MUST 为 `user`、`assistant`、`user`、`assistant`

#### Scenario: 被打断的回答不入史

- **WHEN** 一次 chat 回答在生成过程中被同一会话的新请求打断
- **THEN** 该次回答 MUST NOT 作为 `assistant` 条目写入会话历史

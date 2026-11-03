# Tasks

## 1. 修复记忆写入

- [x] 1.1 修改 `llm.py` 流结束后的收尾逻辑：以 `avatar_session.llm_text` 作为本轮回答全文，在「未被后续请求打断」且回答非空时，把它作为 `assistant` 条目追加到 `avatar_session.chat_history`，并对历史做 `[-(CHAT_HISTORY_ROUNDS * 2):]` 截断。验证：函数末尾不再出现依赖已被排空的 `result` 的条件判断，且 `llm.py` 仍能通过 AST 语法检查
- [x] 1.2 确认「被打断不入史」语义未被破坏：守卫使用与流式循环相同的 `speech_request_id` 判据，被中断时函数会提前 `return`，不执行写记忆。验证：代码走查 + 在 2.2 的验证脚本里用一个「写入前模拟打断」的用例确认历史不含该回答

## 2. 验证修复效果

- [x] 2.1 用两轮真实调用验证角色交替：构造会话对象连续调用 `llm_response()` 两次（第二次是追问），确认历史角色序列为 `user, assistant, user, assistant`。验证：脚本输出该序列，且 `assistant` 条目数量为 2
- [x] 2.2 验证 `assistant` 条目内容正确：确认其内容等于本轮 `avatar_session.llm_text` 去除首尾空白后的结果，且不含 Markdown 标记（`*`、`#`、`` ` ``、`_`、`~`）。验证：脚本逐条比对并输出结论
- [x] 2.3 验证轮数上限仍然生效：把 `CHAT_HISTORY_ROUNDS` 设为 2 后连续进行 3 轮未被中断的问答，确认历史长度不超过 4 条且保留的是最近两轮。验证：脚本输出历史长度与角色序列
- [x] 2.4 验证被打断的回答不入史：先发起一次调用，在其写记忆之前把会话的 `speech_request_id` 改成别的值（模拟被新请求打断），确认历史中不出现该轮的 `assistant` 条目。验证：脚本输出断言结果

## 3. 回归确认

- [x] 3.1 确认对外行为未变：启动主服务，请求 `/api/status` 与 `/health`，确认返回结构与变更前一致，且 `llm_configured`、`rag_available` 均为真。验证：记录两个接口的实际响应体
- [x] 3.2 确认前端问答链路未受影响：用无头浏览器渲染 `/knowtalk-chat.html`，确认页面正常渲染（本地 Bootstrap/图标仍生效）。验证：生成截图并确认页面加载完成、无脚本错误

## 4. 规格与记录

- [x] 4.1 更新审计记录 `docs/audit/2026-10-12-project-audit.md`：把「多轮对话记忆只存了用户提问」这条从"待修复"改为"已在本变更修复"，并保留原始的实测证据与根因分析。验证：审计记录中该条目明确标注修复状态与修复该问题的变更名
- [x] 4.2 运行 `openspec validate --all --strict`，确认变更与全部规格通过严格校验。验证：命令退出码为 0
- [x] 4.3 运行 `openspec show digital-human-api --type spec` 检查主规格，确认本变更归档后「多轮记忆」要求会包含新增的三个场景（归档前在变更 delta 中核对）。验证：delta 中 MODIFIED 块的头部与该要求的现有场景与主规格完全一致，归档时不会丢内容

## Workflow follow-up

- 用户确认追问行为正常后归档本变更：`openspec archive fix-chat-memory`，使收紧后的要求进入 `openspec/specs/digital-human-api/spec.md`。
- 归档后确认该规格中「chat 模式的会话级多轮记忆」已包含「回答写入会话记忆」「多轮之后历史交替出现」「被打断的回答不入史」三个场景。

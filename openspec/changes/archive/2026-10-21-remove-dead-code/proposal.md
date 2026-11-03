# 清理：删除零引用死代码

## Why

2026-10-21 的全仓只读盘点（清单见 `.workbuddy/cleanup-candidates-2026-10-21.md`）确认了一批
**从未被任何代码引用**的文件与函数。它们不是"暂时没人用"，而是：

- `utils/audio.py`、`utils/audioutil.py`、`utils/async_utils.py` —— 全项目 0 命中
  （含 `tests/`、`web/`、`docs/`；唯一形似的 `avatars/wav2lip/audio.py` 是同名另一个文件）；
- `web/whep.js` —— 不在任何 `<script src>` 里，与 `client.js` 有 68 行逐字重复；
- `tts/indextts2.py` —— 有 `from registry import register` 却**没有 `@register` 装饰器**，
  因此 `registry.create("tts", "indextts2")` 必然抛 `ValueError`；`utils/settings_store.py`
  的 `TTS_ENGINES` 早已刻意不含它，界面上也从来选不到。它是一个**从未可用过**的插件；
- `app.py::randN`、`app.py::_llm_configured`、`config.py::str_or_int`、`registry.py::list_plugins`
  —— 全项目仅定义处出现。

留着它们的代价是持续的：每次排查都要先排除这些干扰项，而且 `indextts2` 这类"看起来能选、
选了必崩"的条目会误导下一个人再去调它。

## What Changes

1. 删除 4 个零引用 Python 模块与 1 个零引用前端脚本；
2. 从 `avatars/base_avatar.py` 的 `_tts_modules` 摘掉 `indextts2` 映射——
   **必须与文件同时删**：映射留着会在 `--tts indextts2` 时让 `importlib.import_module`
   抛 `ImportError`，把"未注册"的清晰报错换成更难懂的模块缺失；
3. 删除 4 个仅定义处出现的函数。

## Impact

- 可观察行为**唯一**的变化：`--tts indextts2` 的失败方式从「ValueError: 未注册」
  变为「logger.error: TTS module indextts2 not found」。两者都是启动失败，
  且该引擎从未出现在设置页的可选列表里（`TTS_ENGINES`），因此没有用户路径受影响。
- 不改变任何对外接口的请求/响应结构。
- 全部目标文件均在 git 跟踪内（`git ls-files` 已确认），删除后可用 `git checkout --` 完整恢复。

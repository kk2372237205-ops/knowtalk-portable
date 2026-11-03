# 设计：删除零引用死代码

## 决策 1：先证明"零引用"，再删

判定不靠"看着没用"，而是逐项 grep 全项目（排除 `.venv/`、`cosyvoice_libs/`、
`node_modules/`、`__pycache__/`），要求：

- 没有 `import xxx` / `from xxx import`；
- 没有 `importlib` / `__import__` / 插件注册表的动态加载；
- 不在 `web/*.html` 的 `<script src>` 里，也不在任何路由注册里；
- 不是测试文件正在测的对象。

四项全不满足才进删除清单。盘点阶段已经逐项跑过，本变更只负责执行与复核。

## 决策 2：`indextts2` 是删，不是修

它缺 `@register`，补一个装饰器就能注册成功——但：

1. 它要连 IndexTTS2 服务（`settings_store.TTS_ENGINES` 的注释写明刻意排除），
   修好也没有可用的后端；
2. 上游 `2026-10-16-add-offline-tts-engine` 的任务 4.3 明确写了
   「`indextts2` 保持不提供」——**不修是既定的设计决定**；
3. 留一个"能选但没有后端"的引擎，比没有这个选项更糟。

所以删文件，同时删 `_tts_modules` 里的映射条目。两者必须一起做（见 proposal 的 Impact）。

## 决策 3：删函数时保留文件，且不动调用面

`randN` / `_llm_configured` / `str_or_int` / `list_plugins` 所在的文件都还在用，
只摘函数本体与其正上方的注释/装饰器，不改动文件其余部分，避免顺手改坏相邻逻辑。

## 验证方式

删除后跑三重回归：

- 后端 `pytest tests/ -q`；
- 前端 `node --test tests/js/*.test.js`；
- `openspec validate --all --strict`。

再补一次全项目 grep，确认被删符号在源码里 0 残留（文档里"它已零引用"的历史记录不算）。

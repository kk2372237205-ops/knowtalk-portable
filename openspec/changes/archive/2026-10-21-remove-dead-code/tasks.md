# 任务：删除零引用死代码

## 1. 删除前复核

- [x] 1.1 `git ls-files --error-unmatch` 确认 5 个目标文件都在版本跟踪内（删错可恢复）
- [x] 1.2 全项目 grep 复核零引用（含 tests/ 与动态加载路径）

## 2. 删除零引用模块

- [x] 2.1 删除 `utils/audio.py`
- [x] 2.2 删除 `utils/audioutil.py`
- [x] 2.3 删除 `utils/async_utils.py`
- [x] 2.4 删除 `tts/indextts2.py`
- [x] 2.5 删除 `web/whep.js`

## 3. 摘掉连带引用

- [x] 3.1 `avatars/base_avatar.py` 的 `_tts_modules` 移除 `'indextts2': 'tts.indextts2'`
      （必须与 2.4 同时做，否则 `--tts indextts2` 会变成 ImportError）

## 4. 删除死函数（文件保留）

- [x] 4.1 `app.py::randN`
- [x] 4.2 `app.py::_llm_configured`
- [x] 4.3 `config.py::str_or_int`
- [x] 4.4 `registry.py::list_plugins`

## 5. 回归验证

- [x] 5.1 后端 `pytest tests/ -q` 全绿（747 passed，与删除前基线一致）
- [x] 5.2 前端 `node --test tests/js/*.test.js` 无新增失败（187 pass / 1 fail，唯一失败为预存的 `hook-gate-shell.test.js`，与本次无关）
- [x] 5.3 `openspec validate --all --strict` 通过（27 passed, 0 failed）
- [x] 5.4 全项目 grep：被删符号在源码中 0 残留（`randN`/`_llm_configured`/`str_or_int`/`list_plugins` 仅出现在 docs/openspec 文档；`import random` 已随 `randN` 一并清除；`registry.py` 的 `Dict` 仍在使用故保留）
- [ ] 5.5 更新 `.workbuddy/cleanup-candidates-2026-10-21.md`：已删项标记完成

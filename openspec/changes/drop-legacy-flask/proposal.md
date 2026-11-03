# 去掉 Flask 时代的死依赖

## 为什么

`app.py` 的运行期早就迁到 aiohttp，但头部还留着

```python
from flask import Flask, render_template, send_from_directory, request, jsonify
from flask_sockets import Sockets
app = Flask(__name__)
```

这三行在运行时**一次都没被用到**（`app` 这个 Flask 实例只被赋值、从未被读取，
`Sockets` 连实例化都被注释掉了）。后果是：在没装 flask 的解释器里 `python app.py`
第一屏就 `ModuleNotFoundError: No module named 'flask'`，而真正缺的依赖完全没被报出来。
2026-10-26 用户就撞上了这一条——他用的 `python` 是 PATH 里的另一个解释器。

## 做什么

- 删掉这两行 import 与 `app = Flask(__name__)`（含其上面的注释）。
- 从 `requirements.txt` 移除 `flask`、`flask_sockets`。
- 新增 `tests/test_boot_dependencies.py`：锁住「入口脚本与依赖清单里不再出现 flask」，
  同时确认 aiohttp 入口装配还在。

## 不做什么

- 不动 aiohttp 的任何路由装配顺序。
- 不改 `requirements.txt` 里其它条目。
- 不引入「检查全部未使用依赖」的通用工具（这次只清 flask 这一处确认的死依赖）。

## 影响

- 少了两个 pip 依赖；启动不再可能因为 flask 缺失而失败。
- 若解释器选错，报错会落在真正用到的库（aiohttp / aiortc / torch）上，指向更准。

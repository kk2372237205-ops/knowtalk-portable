# 任务

## 1. 定位

- [x] 1.1 复现：`python app.py` 在 PATH 默认解释器（WorkBuddy 托管 Python 3.13）上报 `No module named 'flask'`
- [x] 1.2 确认真因：PATH 里的 `python` ≠ 项目环境 `C:\Users\24306\.conda\envs\livetalking\python.exe`（后者 flask 3.1.3 是有的）
- [x] 1.3 确认 flask 是死依赖：全仓库除 `app.py:29-30` 的导入与 `app.py:74` 的 `Flask(...)` 外，`Flask`/`Sockets`/`render_template`/`send_from_directory`/`jsonify` 零使用；`sockets` 的实例化早被注释掉

## 2. 变更

- [x] 2.1 建 `drop-legacy-flask`，`validate --strict` 通过
- [x] 2.2 `app.py` 删两行 flask 导入 + `app = Flask(__name__)` 与其注释，原地写清为什么删
- [x] 2.3 `requirements.txt` 移除 `flask`、`flask_sockets`
- [x] 2.4 新增 `tests/test_boot_dependencies.py`（9 项）：AST 层断言不再导入/构造 flask、依赖清单无 flask 条目、核心依赖仍在、装配顺序不变、aiohttp 入口还在、`start.bat` 的解释器选择与编码
- [x] 2.5 `start.bat` 不再用裸 `python`：按「已激活的 conda 环境 → 项目内 `.venv`（必须有 `pyvenv.cfg`）→ PATH 的 python」选一次并打印，启动也用 `"%PY%" app.py`
- [x] 2.6 `start.bat` 存为 **GBK(936) + CRLF**：cmd 用启动时的控制台代码页解析批处理，原来存 UTF-8 会让中文 `rem` / `echo` 被拆错行、报「不是内部或外部命令」
- [x] 2.7 缺依赖时不再直接 `pip install`：先打解释器路径、提示可能选错环境，再用 `choice` 确认，避免把 requirements 装进系统/托管 Python
- [x] 2.8 批处理里改用 `goto` 而不是「括号块 + `command && set`」：实测 936 代码页下后者会吃掉开头引号
- [x] 2.9 候选解释器**先启动一次再采纳**：`.venv` 的 `pyvenv.cfg` 在、但解释器照样起不来，
      原写法只看文件存在，会先选中它（实测 `[提示] 选中的解释器无法启动：.venv\Scripts\python.exe`）
- [x] 2.10 新增钉死入口：项目根 `.python-path.txt`（首行为解释器路径，已加 `.gitignore`），
      优先级最高；已写入 `C:\Users\24306\.conda\envs\livetalking\python.exe`


## 3. 验证

- [x] 3.1 `tests/test_boot_dependencies.py` 9 passed
- [x] 3.2 正确解释器下 `import app` → `import ok`（真机确认，不是 mock）
- [x] 3.3 错误解释器下报错点从 flask 前移到 `torch`（真正缺的依赖），指向变准
- [x] 3.4 `start.bat` 试运行（把启动行换成 echo、`choice` 换成 echo）：CONDA_PREFIX 存在时选中 conda 解释器、依赖判定 OK、RAG 环境识别正常、最终启动命令正确
- [x] 3.5 后端全量测试与基线一致
- [x] 3.6 顺带核实：静态挂载在 `setup_routes` 内、素材路由在其之后注册，aiohttp 3.14 下后注册的 API 仍可命中（实测 200），无需调整顺序
- [x] 3.7 试运行两条路径都选中目标解释器：
      - 有 `.python-path.txt`：`[环境] 解释器 C:\Users\24306\.conda\envs\livetalking\python.exe`
      - 没有该文件：conda 未激活 → 坏 `.venv` 被拒 → 扫描 `%USERPROFILE%\.conda\envs` 命中同一个解释器
- [x] 3.8 试运行必须把 `pip install` 那行也换掉：`choice` 换成 echo 会把 errorlevel 归零，
      于是脚本会**真的**开始装 requirements——第一次探测到的"卡住"就是这个，不是脚本的 bug

## 4. 遗留（本次不做）

- 2026-10-12 审计还列了 `numpy`、`BytesIO`、部分 `re` 等未使用导入，本次只清 flask 这一处确认的死依赖。

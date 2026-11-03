# Spec Delta

## ADDED Requirements

### Requirement: 依赖缺失时启动必须给出可执行提示

`app.py` MUST 在导入 `numpy`、`aiohttp`、`aiortc`、`torch` 之前探测它们是否存在，探测 MUST NOT 真的导入（`torch` 导入耗时数秒）。

任一缺失时 MUST 以非零退出码结束，并打印：当前解释器的绝对路径、缺失的模块名、本机可用的项目解释器（若找到）、以及应当执行的启动方式。MUST NOT 只抛一条 `ModuleNotFoundError` 了事。

依赖齐全时 MUST 不输出任何额外内容，行为与未加自检时一致。

#### Scenario: 用没装依赖的解释器启动

- **WHEN** 在一个缺 `numpy` 或 `torch` 的 Python 上运行 `python app.py`
- **THEN** 进程 MUST 以非零码退出
- **AND** 输出 MUST 含该解释器的绝对路径与缺失的模块名
- **AND** 若本机存在装了全部依赖的解释器，输出 MUST 给出它的路径与启动方式
- **AND** 输出 MUST 提醒不要在当前解释器里安装 `requirements.txt`

#### Scenario: 依赖齐全时不打扰

- **WHEN** 用项目环境启动
- **THEN** 自检 MUST 静默通过
- **AND** 服务启动日志 MUST 与加自检之前一致

### Requirement: 一键启动脚本必须自己找到项目环境

`start.bat` MUST 按顺序挑选解释器：已激活的 conda 环境 → 项目内 `.venv`（必须存在 `pyvenv.cfg`）→ `%USERPROFILE%\.conda\envs\*` 与 `%USERPROFILE%\anaconda3\envs\*` 下能导入 `aiohttp, aiortc, numpy, torch` 的环境 → PATH 里的 `python`。

候选解释器 MUST 先能启动（`python -c "import sys"`）才被采纳。选中后 MUST 打印实际使用的路径。一、二、四个候选都不可用时 MUST 落到提示并退出，MUST NOT 直接开始安装依赖。

#### Scenario: 未激活任何环境时双击启动

- **WHEN** 在普通资源管理器里双击 `start.bat`，未执行过 `conda activate`
- **THEN** 脚本 MUST 从 conda envs 目录里选中装了全部依赖的那一个
- **AND** 打印的解释器路径 MUST 是那个 conda 环境的 `python.exe`
- **AND** MUST NOT 在 PATH 里的其它 Python 上安装依赖

#### Scenario: 扫描不到可用环境

- **WHEN** 本机没有任何装了依赖的解释器
- **THEN** 脚本 MUST 打印提示并退出
- **AND** MUST NOT 静默地用错误解释器启动 `app.py`

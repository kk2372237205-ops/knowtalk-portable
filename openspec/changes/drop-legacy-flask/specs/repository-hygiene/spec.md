# Spec Delta

## ADDED Requirements

### Requirement: 启动入口必须能用本机指定的解释器

`start.bat` MUST 支持从项目根的 `.python-path.txt`（首行为解释器的绝对路径）取解释器；该文件存在且解释器可启动时 MUST 优先使用，并 MUST 打印所用路径。因为它是本机配置，`.python-path.txt` MUST 被 `.gitignore` 忽略，MUST NOT 进入版本库。

优先级 MUST 为：`.python-path.txt` → 已激活的 conda 环境 → 项目内 `.venv`（必须有 `pyvenv.cfg`）→ PATH 里的 python。

#### Scenario: 本机指定了解释器

- **WHEN** 项目根存在 `.python-path.txt`，首行路径可启动
- **THEN** 启动脚本 MUST 用它启动 `app.py`
- **AND** MUST 打印这条路径，使人一眼确认用的是哪个环境

#### Scenario: 指定文件里的路径已失效

- **WHEN** `.python-path.txt` 里的路径不存在或无法启动
- **THEN** MUST 继续按 conda → `.venv` → PATH 的顺序往下找
- **AND** MUST NOT 因此报错退出

### Requirement: 启动入口不得依赖运行时未使用的库

入口脚本 `app.py` 的运行期是 aiohttp，MUST NOT 引入运行时未使用的第三方框架，MUST NOT 为它们构造实例；`requirements.txt` MUST NOT 声明这类库。

判定标准：全仓库除该行导入与实例构造外，不再出现该库的任何符号。满足此标准的库 MUST 从入口脚本与依赖清单中删除。

#### Scenario: 在未安装该库的环境里启动

- **WHEN** 解释器里没有安装 flask
- **THEN** `python app.py` MUST NOT 因为 flask 缺失而报 `ModuleNotFoundError`
- **AND** 一旦真的缺依赖，报错 MUST 落在实际使用、确实缺失的库上

#### Scenario: 依赖清单与代码一致

- **WHEN** 检查 `requirements.txt`
- **THEN** MUST NOT 出现入口脚本从未使用的 Web 框架
- **AND** 删除后 MUST 仍覆盖 `app.py`、`server/`、`utils/` 里的全部实际 import

#### Scenario: 启动装配不被顺手改坏

- **WHEN** 删除死导入后启动服务
- **THEN** 各路由装配函数的调用顺序 MUST 与改动前一致（设置 → 登录 → 提问 → 转写 → 画布 → 入口页 → 通用路由 → 素材 → CORS）
- **AND** 首页、工作台、展示页 MUST 仍可正常打开

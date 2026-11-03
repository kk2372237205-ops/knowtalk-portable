# Spec Delta

## Purpose

定义 KnowTalk 运行期设置的访问口令机制：口令从何而来、哪些接口必须携带口令、未通过校验时如何拒绝，以及口令在前端如何存放与修改，使管理类与配置类接口不再对局域网匿名开放，任何人在口令之后才能读取或覆盖 API Key。

## ADDED Requirements

### Requirement: 访问口令按固定优先级确定

服务启动时 MUST 按固定优先级确定唯一访问口令：环境变量 `ADMIN_TOKEN` 优先；该变量不存在时取设置文件 `data/settings/settings.json` 的 `access_token`；两者都不存在时按"自动生成"处理。口令确定后 MUST 在整个服务生命周期内保持稳定，MUST NOT 随请求变化。

#### Scenario: 环境变量与设置文件同时存在

- **WHEN** 环境变量 `ADMIN_TOKEN` 与设置文件中的 `access_token` 同时存在且取值不同
- **THEN** 校验使用 `ADMIN_TOKEN` 的值
- **AND** 设置文件中的 `access_token` 在 `ADMIN_TOKEN` 被移除前 MUST 不生效

#### Scenario: 仅设置文件存在

- **WHEN** 环境变量 `ADMIN_TOKEN` 不存在，而设置文件中存在 `access_token`
- **THEN** 校验使用设置文件中的 `access_token`
- **AND** 用该值可以访问受保护接口

### Requirement: 缺失口令时必须自动生成并打印一次

当 `ADMIN_TOKEN` 与设置文件的 `access_token` 都不存在时，服务 MUST 自动生成一个口令，所用随机源 MUST 提供不少于 32 字节的熵；生成结果 MUST 写入设置文件，并 MUST 在控制台与日志中各打印一次且带有"首次生成的访问口令"提示。

#### Scenario: 首次启动且无任何口令配置

- **WHEN** 在既没有 `ADMIN_TOKEN` 也没有 `access_token` 的环境下首次启动
- **THEN** 控制台与日志各出现一条包含新口令与首次生成提示的记录
- **AND** 该口令被写入 `data/settings/settings.json`
- **AND** 使用该口令可以访问受保护接口

#### Scenario: 自动生成口令的随机性

- **WHEN** 在不同机器或不同时间重复触发自动生成
- **THEN** 生成结果 MUST NOT 相同
- **AND** 生成所用的随机熵 MUST NOT 少于 32 字节

### Requirement: 受保护路径的请求必须携带口令

以下路径属于受保护范围，请求 MUST 通过口令校验后才可被处理：`/api/settings/`、`/api/control/`、`/api/admin/`、`/rag/admin/`、`/api/avatar/task`（含 `/{task_id}` 与 `/api/avatar/tasks`）、`/api/voice/upload`、`/api/voice/delete`。这些路径上的所有 HTTP 方法 MUST 同等受保护。划分依据是"配置与凭据面 + 管理面"；课堂运行面见下一条要求。

#### Scenario: 读取设置

- **WHEN** 客户端不带口令请求 `GET /api/settings`
- **THEN** 服务拒绝该请求且不返回任何设置内容

#### Scenario: 写入设置

- **WHEN** 客户端携带正确口令请求 `PUT /api/settings`
- **THEN** 请求被正常处理

#### Scenario: 声音样本与形象任务接口

- **WHEN** 客户端分别不带口令请求 `/api/voice/upload` 与 `/api/avatar/task`
- **THEN** 两个请求都被拒绝

### Requirement: 明确的豁免路径不受口令拦截

静态页面与静态资源、`/offer`、`/human`、`/humanaudio`、`/interrupt_talk`、`/is_speaking`、`/llm_status`、`/record`、`/api/status`、`/api/voices`、`/api/voice/apply`、`/api/slides*`、`/api/current_slide`、`/api/materials/*` MUST NOT 要求口令。这些接口服务于课堂现场操作，页面在尚未输入口令时也必须可用。列入受保护集合之外的其它路径 MUST 保持变更前的行为不变。

#### Scenario: 课堂现场操作无需口令

- **WHEN** 未输入口令的页面上传课件、增删幻灯片、列举音色或把音色应用到会话
- **THEN** 这些请求 MUST 正常完成，MUST NOT 因缺少口令被拒绝

#### Scenario: 课堂上直接打开页面

- **WHEN** 课堂上在一台没有录入口令的机器上直接打开主控台页面，并调用 `/api/status` 与 `/api/slides`
- **THEN** 页面与两个请求均正常返回，MUST NOT 出现 401
- **AND** 讲解、打断与幻灯片展示链路 MUST 不需要事先输入口令

#### Scenario: 静态资源

- **WHEN** 浏览器不带口令请求页面引用的样式与脚本
- **THEN** 服务返回 200 与对应文件内容

### Requirement: 口令经 X-Admin-Token 请求头传递且拒绝结构固定

受保护接口的口令 MUST 通过请求头 `X-Admin-Token` 传递。缺少该请求头或取值错误时，服务 MUST 返回 HTTP 401 与固定结构的 JSON 错误体，且响应 MUST NOT 包含任何配置内容、密钥、密钥掩码或文件路径；"未携带"与"口令错误"两种情形的响应 MUST 完全一致。

#### Scenario: 未携带口令

- **WHEN** 客户端请求 `GET /api/admin/config` 且不带 `X-Admin-Token`
- **THEN** 返回 401 与固定结构的错误体
- **AND** 响应体 MUST NOT 出现任何启动参数值、密钥、掩码或文件路径

#### Scenario: 口令错误

- **WHEN** 客户端携带错误的 `X-Admin-Token` 请求同一受保护接口
- **THEN** 返回 401
- **AND** 响应状态码与响应体与"未携带口令"的情形完全一致

### Requirement: 口令比较必须为常量时间

口令校验 MUST 使用常量时间比较。校验耗时 MUST NOT 随匹配前缀长度、口令长度差异或匹配失败位置发生可测量的变化。

#### Scenario: 错误口令具有不同匹配前缀

- **WHEN** 分别用"首字符即错误"与"仅末位错误"的两个口令请求同一受保护接口
- **THEN** 两次请求均返回 401
- **AND** 两次请求的响应时间 MUST NOT 呈现与匹配位置相关的稳定差异

### Requirement: 口令可在设置中修改且立即生效

访问口令 MUST 支持在设置页修改，修改请求 MUST 先通过当前口令校验；校验不通过时 MUST NOT 修改口令。修改成功后，新口令 MUST 立即对此后的请求生效，MUST NOT 需要重启服务，且旧口令 MUST 立即失效。

#### Scenario: 用当前口令修改口令

- **WHEN** 客户端携带当前口令提交新口令
- **THEN** 修改成功
- **AND** 新口令可立即通过校验
- **AND** 旧口令随后请求受保护接口时返回 401

#### Scenario: 未通过当前口令校验的修改

- **WHEN** 客户端携带错误口令提交新口令
- **THEN** 返回 401 且口令保持原值
- **AND** 原口令仍然有效

### Requirement: 前端只在 sessionStorage 保存口令

管理类页面 MUST 将口令保存在浏览器的 `sessionStorage` 中，并 MUST NOT 使用 `localStorage`、Cookie 或其它长期存储保留口令。关闭标签页后 MUST 视为口令已过期，需重新输入。

#### Scenario: 关闭标签页后重新访问

- **WHEN** 用户输入口令后关闭标签页，再新开页面访问设置页
- **THEN** 页面重新提示输入口令
- **AND** 浏览器本地存储中 MUST NOT 残留该口令

#### Scenario: 页面发送受保护请求

- **WHEN** 已输入口令的页面调用受保护接口
- **THEN** 请求 MUST 在 `X-Admin-Token` 请求头中携带该口令

### Requirement: 未通过校验的请求不产生任何副作用

未通过口令校验的请求 MUST NOT 造成任何可观察的状态变化：MUST NOT 修改设置文件、MUST NOT 读写或删除任何文件、MUST NOT 启动或停止任何进程、MUST NOT 建立或结束任何会话。

#### Scenario: 未授权删除声音样本

- **WHEN** 不带口令请求 `DELETE /api/voice/delete`
- **THEN** 返回 401
- **AND** 目标声音样本文件仍然存在

#### Scenario: 未授权重启 RAG 子进程

- **WHEN** 不带口令请求 `POST /api/control/rag/restart`
- **THEN** 返回 401
- **AND** RAG 子进程的进程标识与运行状态 MUST 保持不变

#### Scenario: 未授权写入设置

- **WHEN** 不带口令请求 `PUT /api/settings`
- **THEN** 返回 401
- **AND** `data/settings/settings.json` 的内容 MUST 逐字节保持不变

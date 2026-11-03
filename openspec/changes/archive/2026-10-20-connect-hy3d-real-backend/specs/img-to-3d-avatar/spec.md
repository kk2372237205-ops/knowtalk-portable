# img-to-3d-avatar Specification (delta)

## MODIFIED Requirements

### Requirement: 管线以可插拔后端组织且默认可无模型运行

系统 MUST 按 `validate → threeview(可选) → generate → rig(可选) → animate` 顺序执行，
每个阶段的后端由环境变量选择，且 `generate` 与 `animate` 的默认后端 MUST 为 `mock`。

#### Scenario: 默认配置端到端可运行

- **WHEN** 未配置任何外部模型环境变量
- **THEN** 系统在 `data/img3d/results/<job>/` 产出 `model.glb`（最小合法 glTF 2.0）与 `animation_manifest.json`，不依赖任何外部服务

#### Scenario: 后端由环境变量切换

- **WHEN** 设置 `IMG3D_GENERATE_BACKEND=hy3d_api` 且提供 `HY3D_API_KEY`
- **THEN** 生成阶段改为调用混元3D 官方异步接口（`api.ai3d.cloud.tencent.com` 的 `/v1/ai3d/submit` + `/v1/ai3d/query` 轮询 + 下载），产出由上传图片重建的 3D 网格，而非占位立方体
- **AND** 系统对提交返回的 JobID、查询返回的 status 与模型文件 URL 做多路径容错提取，不因单个字段命名差异而失败
- **AND** 未提供 Key 时返回明确错误而非静默降级

#### Scenario: 真实生成有上限

- **WHEN** 混元3D 任务在最长 300 秒内未完成
- **THEN** 系统抛出明确超时错误，不无限等待

## ADDED Requirements

### Requirement: 真实生成不得阻塞请求线程

当 `generate` 后端为真实外部服务（分钟级耗时）时，HTTP 处理协程 MUST 把同步管线放入
线程池执行（`run_in_executor`），不得直接调用阻塞式 `run_pipeline` 以免卡住 aiohttp
事件循环、影响同一进程内的其他请求。

#### Scenario: 事件循环不被长任务阻塞

- **WHEN** 一次真实混元3D 生成耗时数分钟
- **THEN** 该请求在 executor 线程中等待，aiohttp 事件循环仍可响应 `/api/img3d/status` 等其它请求

#### Scenario: 仍同步返回结果

- **WHEN** 真实生成在超时内完成
- **THEN** `POST /api/img3d/generate` 仍同步返回结果元数据，前端协议不变

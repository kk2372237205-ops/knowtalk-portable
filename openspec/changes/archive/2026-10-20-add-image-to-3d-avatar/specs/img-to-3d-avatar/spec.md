# img-to-3d-avatar Spec

## Overview

图片一键生成可驱动的三维数字人：上传一张图片，经（可选）三视图增强、3D 生成、绑骨、
（可选）口型/手势动画清单，产出可在浏览器预览的 GLB 模型与动画清单。管线以可插拔后端
组织，默认 `mock` 使全流程无需外部模型即可运行；真实模型（混元3D / AccuRig / Audio2Face）
通过环境变量接入。

## ADDED Requirements

### Requirement: 图片上传必须走路径守卫

系统 MUST 把上传的图片保存到 `data/img3d/inbox/` 下，且任何由请求参数拼出的落盘路径
MUST 先经 `utils.path_guard` 校验，不得越出允许目录。

#### Scenario: 合法图片写入 inbox

- **WHEN** 收到一张后缀在允许清单内的图片上传
- **THEN** 系统返回 `code=0` 与 `job_id`，文件落在 `data/img3d/inbox/` 之内

#### Scenario: 非法后缀被拒绝

- **WHEN** 上传的后缀不在允许清单（如 `.py`、`.exe`、无后缀）
- **THEN** 系统返回非 0 错误，且 `inbox/` 下不出现任何文件

#### Scenario: 路径穿越写不出去

- **WHEN** 请求参数试图把文件落到 `data/img3d/` 之外（含 `../`、绝对路径）
- **THEN** 系统拒绝该请求，越界目录不被写入任何内容

### Requirement: 管线以可插拔后端组织且默认可无模型运行

系统 MUST 按 `validate → threeview(可选) → generate → rig(可选) → animate` 顺序执行，
每个阶段的后端由环境变量选择，且 `generate` 与 `animate` 的默认后端 MUST 为 `mock`。

#### Scenario: 默认配置端到端可运行

- **WHEN** 未配置任何外部模型环境变量
- **THEN** 系统在 `data/img3d/results/<job>/` 产出 `model.glb`（最小合法 glTF 2.0）与 `animation_manifest.json`，不依赖任何外部服务

#### Scenario: 后端由环境变量切换

- **WHEN** 设置 `IMG3D_GENERATE_BACKEND=hy3d_api` 且提供 `HY3D_API_KEY`
- **THEN** 生成阶段改为调用混元3D API；未提供 Key 时返回明确错误而非静默降级

### Requirement: 生成结果可下载且可预览

系统 MUST 提供按 `job_id` 拉取 GLB 与动画清单的接口，且 GLB MUST 可被浏览器端
three.js 的 GLTFLoader 解析（满足 glTF 2.0 基本结构）。

#### Scenario: 拉取 GLB

- **WHEN** 生成完成后请求 `GET /api/img3d/result/<job_id>`
- **THEN** 返回 `model.glb` 的二进制，Content-Type 为 `model/gltf-binary`

#### Scenario: 拉取动画清单

- **WHEN** 请求 `GET /api/img3d/manifest/<job_id>`
- **THEN** 返回 JSON，含 `mouth_openness` 轨迹与 `gestures` 时间轴

### Requirement: 后端可用性必须显式暴露

系统 MUST 在 `GET /api/img3d/status` 返回各阶段后端是否就绪，前端据此区分"演示模式"
与"已接入真实模型"。

#### Scenario: 演示模式提示

- **WHEN** 所有真实后端均未配置
- **THEN** 响应 `demo_mode=true`，并列出每个阶段当前选用的后端

#### Scenario: 真实后端就绪

- **WHEN** 已配置 `HY3D_API_KEY`
- **THEN** 响应中 `generate` 阶段标记 `ready=true`

### Requirement: 前端预览禁依赖外部 CDN

three.js 与 GLTFLoader MUST 由主服务同源的 `web/vendor/` 提供，页面不得引用任何外网地址。

#### Scenario: 离线预览

- **WHEN** 浏览器在离线机器打开预览页
- **THEN** three.js 与 GLTFLoader 从 `/vendor/...` 加载成功，模型正常渲染

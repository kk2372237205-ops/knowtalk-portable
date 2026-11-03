# Design

## Context

链路目标：图片 →（可选）三视图 → 3D 生成 →（可选）绑骨 → 口型/手势动画清单。
用户原始设想是"先调模型生成三视图，再喂混元3D"。但已核实 2026-10 现状：

- **混元3D 3.1 已原生支持图生3D、最多 8 视图输入、3D 人物生成**，单图直接出模型；
- "自己生成三视图"本质是手工造一组多视图输入，单图背面靠模型想象、是质量最弱一环，
  且与混元3D 自带多视图能力重叠。

结论（已在对话中告知用户并获认可）：把"自生成三视图"降级为**可选增强**，主路径走
混元3D 图生3D；绑骨 + 面部口型作为重点，手势后置。

## Decision 1：阶段化 + 可插拔后端，默认 mock

编排器 `run_pipeline` 顺序执行阶段：`validate → threeview(可选) → generate → rig(可选) → animate`。
每个阶段选后端由环境变量决定，**默认 `mock`**，使全流程在无外部模型时可端到端跑通与测试。

| 阶段 | 可选 | 后端 | 默认 | 真实接入点 |
|---|---|---|---|---|
| threeview | 是 | `none`/`mock` | `none` | 多视图模型（Wonder3D/TripoSR） |
| generate | 否 | `mock`/`hy3d_api`/`hy3d_local` | `mock` | 混元3D API / 本地 CLI |
| rig | 是 | `none`/`mock`/`accurig` | `none` | AccuRig / RigNet |
| animate | 否 | `none`/`mock` | `mock` | Audio2Face / blendshape 求解 |

`mock` 生成写入**最小合法 GLB**（一个带 PBR 材质的立方体，满足 glTF 2.0），
`animate` 写入 `animation_manifest.json`（嘴唇开合轨 + 手势时间轴），供前端时间轴展示；
真实 rigged 模型由上游求解器消费该清单驱动 morph target。

## Decision 2：后端可用性显式暴露

`GET /api/img3d/status` 返回各后端是否就绪（环境变量是否配置）。前端据此提示用户
"当前为演示模式（mock）"还是"已接入真实模型"，缺配置时绝不假装成功。

## Decision 3：落盘全部走 path_guard

上传图片、生成的 GLB、清单都落在 `data/img3d/` 下；任何由请求参数拼出的路径都先过
`path_guard.resolve_within`，沿用 voice/material 的既有加固口径（见 `test_voice_guard.py`）。

## Decision 4：生成同步执行（本变更）

`mock` 后端即时完成；真实 `hy3d_api` 可能耗时数十秒。本变更在请求内同步等待（简单、
可测），并在文档注明"生产应改为后台任务 + 轮询"，留作后续变更。

## Migration Plan

1. 写 `utils/img_to_3d.py`（编排器 + mock GLB）。
2. 写 `server/img_to_3d_routes.py` 并接入 `app.py`。
3. 下载 three.js / GLTFLoader 到 `web/vendor/`，更新 README 清单。
4. 写 `web/img-to-3d.html` + `index.html` 入口。
5. 写 `tests/test_img_to_3d.py` 与 `tests/test_img_to_3d_routes.py`。
6. `openspec validate --all --strict` + `pytest` 全绿后归档。

回滚：删除上述新增文件与 `app.py` 中一行注册调用即可；不影响任何运行时行为。

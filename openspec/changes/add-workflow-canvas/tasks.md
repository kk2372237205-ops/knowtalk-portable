# 任务

## 1. 存储与校验

- [x] 1.1 `utils/workflow_store.py`：`list_workflows` / `load` / `save` / `delete`，原子写（临时文件 + `os.replace` + fsync）
- [x] 1.2 图校验：单一 avatar 根、非 avatar 必须且只能挂在 avatar 上、禁自环与重复边、禁未知类型与悬空端点
- [x] 1.3 校验失败抛出 `WorkflowError(status)` 带中文原因，由路由映射成 4xx

## 2. 接口

- [x] 2.1 `GET /api/workflows` 列表（含节点数、连线数、更新时间，按更新时间倒序）
- [x] 2.2 `POST /api/workflows` 新建，返回 201 与新 id
- [x] 2.3 `GET /api/workflows/{id}` 读整图
- [x] 2.4 `PUT /api/workflows/{id}` 覆盖保存（含校验）
- [x] 2.5 `DELETE /api/workflows/{id}`
- [x] 2.6 `GET /api/workflows/{id}/plan` 解析开播参数并标注 `warnings`
- [x] 2.7 `app.py` 注册路由，位置在 `add_static('/')` 之前（与 auth / quiz / stt 同批）

## 3. 画布页

- [x] 3.1 `web/workflow.html` 骨架：`.site-bar`、节点库、画布、属性面板
- [x] 3.2 `web/workflow.css` 纸面令牌 + 节点/连线/端点样式 + 四档断点
- [x] 3.3 `web/workflow.js`：拖拽摆位、拖端点连线、删节点带走连线、选中高亮、点线选中下游节点
- [x] 3.4 属性面板按节点类型渲染下拉（形象 / 课件 / 课程 / 手势），数据取自既有接口
- [x] 3.5 自动保存（600ms debounce）与保存状态提示（待保存 / 保存中 / 已保存 / 保存失败：原因）
- [x] 3.6 流程名可改、可新建、可删除；左栏流程列表可切换

## 4. 开播接入

- [x] 4.1 工作台左栏新增「流程画布」入口
- [x] 4.2 工作台读 `?workflow=<id>`，拉 plan 灌入 `offerAvatar` / `offerRefAudio` / `offerRefText` / `offerPpt`（带重试，避开异步填词时序）
- [x] 4.3 资源缺失时在开播面板顶部逐项提示，并把 plan 的 `warnings` 一并列出
- [x] 4.4 左栏「当前流程」显示流程名，点它回到该流程的画布

## 5. 节点可视化（素材卡）

- [x] 5.1 `web/workflow.js`：`NODE_SIZE` 按类型定尺寸；`mediaSrc()` 取形象缩略图 / PPT 首页 / 手势循环预览
- [x] 5.2 节点结构改成「媒体区 + 文字区」，取不到素材退回同类型线描图标，不显示破图
- [x] 5.3 副信息：形象「可用 · 音色」、PPT「N 页」、知识库「N 份资料 · 索引状态」、动作 `[GESTURE:id]`
- [x] 5.4 属性面板顶部同步展示同一份素材预览（含空态说明）
- [x] 5.5 新增节点从数字人右侧按已有节点高度往下排，排满换列；拖动边界按节点自身尺寸收
- [x] 5.6 修 `loadGestures`：接口返回 `{gestures: [...]}`，此前当数组用会在 `.map` 处抛错
- [x] 5.7 打开流程默认选中数字人，右栏不留空
- [x] 5.8 资源版本号 `workflow.css?v=2` / `workflow.js?v=2`

## 6. 验证

- [x] 6.1 `tests/test_workflow_canvas.py`：33 项（存储 / 校验 / 悬空边 / 自环 / 重复边 / 计划 / http CRUD / 页面资源 / 素材卡断言）
- [x] 6.2 后端全量 **820 passed**（787 基线 + 29 + 4 新增）
- [x] 6.3 前端 `node --test tests/js/*.test.js` **187/188**（唯一失败仍是既有的 `hook-gate-shell`）
- [x] 6.4 设计门禁 `web/workflow.css` **0 FAIL / 7 WARN**
- [x] 6.5 五档视口截图与溢出探针

## 实测记录

### 跨页溢出（同源 iframe 探针，7 页 × 5 档）

| 视口 | index | workbench | workflow | avatar | knowledge-base | settings | admin |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1440 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 1280 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 1024 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 768 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 500 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

表中为 `scrollWidth - clientWidth`；`workflow.html` 五档 `bar=y`（顶栏在）。

### 交互探针（无头浏览器驱动真实页面）

- 新增节点：4 → 5 个节点，`save=已保存`，`GET /api/workflows/{id}` 里节点数一致。
- 拖动节点：`top 260 → 220`，落盘 `persisted-y=220`。
- 改文本属性：参考文本写入后 `persisted reftext=测试参考文本`。
- 点连线：`line-click selects=PPT`（选中下游节点，属性面板切到 PPT）。
- 保存失败路径（临时构造孤边）：`GET` 仍是旧内容，页面显示失败原因且画布保留改动。

### 节点素材卡（真实素材渲染，无头浏览器核对）

| 节点 | 素材来源 | 实测 |
| --- | --- | --- |
| 数字人 | `/api/avatars/{id}/thumbnail`（`full_imgs` 首帧，538×1080 竖版） | 节点 176×252，媒体区 196 高，`object-position: center 14%` 保住脸 |
| PPT | 组内首页 `slide.image` | 节点 224×182，媒体区 126（16:9），`object-fit: contain` 不裁页面 |
| 知识库 | 无图片素材 | 书册线描图标 + 「N 份资料 · 已索引」；课程为空时只留图标与「未选课程」 |
| 动作 | `/api/avatars/{id}/gestures/{gid}/preview`（循环 WebP） | 节点 176×184，右下角标「循环」区分静态帧 |

属性面板预览实测：选中 PPT → `insp-img` 指向 `..._page_001.png`，副信息「12 页」。

### 待观察

- 动作节点的「手势」下拉依赖数字人已选形象；形象下的手势清单在选中形象后按需拉取。
- 知识库课程清单来自 RAG 旁挂服务 `/rag/admin/courses`，服务未起时下拉为空、属性面板会说明原因。
- 手势循环预览由 Pillow 现算并缓存（107 帧约 2.5s），首次进画布时该节点会先露图标再淡出图片。

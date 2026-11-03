# Spec Delta

## REMOVED Requirements

### Requirement: 图片上传必须走路径守卫

**Reason**: 用户决定下线「图片转 3D 数字人」功能，上传入口随功能一并移除。

**Migration**: 无替代路径。上传守卫能力由 `utils.path_guard` 保留，其他需要上传的功能（如 Seedance 曾复用的机制）不受影响；`data/img3d/inbox/` 下的历史数据由用户自行清理。

### Requirement: 管线以可插拔后端组织且默认可无模型运行

**Reason**: 用户决定下线「图片转 3D 数字人」功能，`validate → threeview → generate → rig → animate` 管线（含 mock 后端与混元3D hy3d_api 后端）整体移除。

**Migration**: 无。混元3D 接入方案记录在归档变更 `2026-10-20-connect-hy3d-real-backend` 与 `docs/single-image-motion-research.md` 中，如需恢复可参考；`IMG3D_*` / `HY3D_*` 环境变量不再被读取。

### Requirement: 生成结果可下载且可预览

**Reason**: 用户决定下线「图片转 3D 数字人」功能，`/api/img3d/result/<job_id>` 与 `/api/img3d/manifest/<job_id>` 接口移除。

**Migration**: 无。`data/img3d/results/` 下已生成的 GLB 文件保留在磁盘上，用户可自行下载备份。

### Requirement: 后端可用性必须显式暴露

**Reason**: 用户决定下线「图片转 3D 数字人」功能，`/api/img3d/status` 接口与「演示模式」提示移除。

**Migration**: 无。

### Requirement: 前端预览禁依赖外部 CDN

**Reason**: 用户决定下线「图片转 3D 数字人」功能，three.js 预览页删除，同源 vendor 文件 `web/vendor/three.min.js`、`web/vendor/GLTFLoader.js` 一并移除。

**Migration**: 无。`web/vendor/README.md` 资产清单同步更新；其余页面仍只引用同源 vendor 资产（Bootstrap/jQuery），本约束的其余部分继续成立。

### Requirement: 真实生成不得阻塞请求线程

**Reason**: 用户决定下线「图片转 3D 数字人」功能，同步管线与 `run_in_executor` 调用点移除。

**Migration**: 无。`server/blocking.py::run_blocking` 线程池机制本身保留，提问模式判分等其他同步调用继续复用它。

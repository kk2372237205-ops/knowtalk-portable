# Spec Delta

## REMOVED Requirements

### Requirement: 访问口令按固定优先级确定

**Reason**: 用户决定移除访问口令机制，服务不再持有口令，也就没有"按优先级确定口令"这件事。

**Migration**: 无需迁移动作。原受保护接口现在匿名可用；如需对外暴露服务，应在反向代理层（Nginx 等）加认证，或在后续变更中引入真正的账号体系。

### Requirement: 缺失口令时必须自动生成并打印一次

**Reason**: 不再生成口令：启动时不写 `access_token`、不在控制台打印，避免让用户误以为存在待输入的凭据。

**Migration**: 无需迁移动作。原受保护接口现在匿名可用；如需对外暴露服务，应在反向代理层（Nginx 等）加认证，或在后续变更中引入真正的账号体系。

### Requirement: 受保护路径的请求必须携带口令

**Reason**: 口令校验中间件整体摘除，`/api/settings*`、`/api/control*`、`/api/admin/*`、`/rag/admin/*`、`/api/avatar/task*`、`/api/voice/upload`、`/api/voice/delete`、`/api/quiz/bank*`、`/api/quiz/records*` 恢复为免口令，与其余课堂面接口一致。

**Migration**: 无需迁移动作。原受保护接口现在匿名可用；如需对外暴露服务，应在反向代理层（Nginx 等）加认证，或在后续变更中引入真正的账号体系。

**已知残留**：`digital-human-api` 的「访问口令校验失败不套用业务信封」场景随本能力退役而成为不可达的历史场景。openspec 严格校验不允许在 MODIFIED 块中改名或删除基线场景（改名会被判为"丢弃原场景"），故该场景原样保留，待该需求下次整体调整时一并清理。

### Requirement: 明确的豁免路径不受口令拦截

**Reason**: 豁免清单失去意义：所有路径都不再做口令校验。原清单中各接口的行为保持不变。

**Migration**: 无需迁移动作。原受保护接口现在匿名可用；如需对外暴露服务，应在反向代理层（Nginx 等）加认证，或在后续变更中引入真正的账号体系。

### Requirement: 口令经 X-Admin-Token 请求头传递且拒绝结构固定

**Reason**: 不再有请求头约定，也不再返回 401 拒绝体；服务端删除了 `server/auth.py` 与固定结构的 `unauthorized` 响应。

**Migration**: 无需迁移动作。原受保护接口现在匿名可用；如需对外暴露服务，应在反向代理层（Nginx 等）加认证，或在后续变更中引入真正的账号体系。

### Requirement: 口令比较必须为常量时间

**Reason**: 不存在口令比较，常量时间比较的实现随 `server/auth.py` 一并删除。

**Migration**: 无需迁移动作。原受保护接口现在匿名可用；如需对外暴露服务，应在反向代理层（Nginx 等）加认证，或在后续变更中引入真正的账号体系。

### Requirement: 口令可在设置中修改且立即生效

**Reason**: 设置页的「修改访问口令」「清除口令」区块与 `PUT /api/settings/access-token` 接口一并删除。

**Migration**: 无需迁移动作。原受保护接口现在匿名可用；如需对外暴露服务，应在反向代理层（Nginx 等）加认证，或在后续变更中引入真正的账号体系。

### Requirement: 前端只在 sessionStorage 保存口令

**Reason**: 前端不再持有口令：`sessionStorage` 中的 `knowtalk_admin_token` 读写逻辑删除，设置页打开即用。

**Migration**: 无需迁移动作。原受保护接口现在匿名可用；如需对外暴露服务，应在反向代理层（Nginx 等）加认证，或在后续变更中引入真正的账号体系。

### Requirement: 未通过校验的请求不产生任何副作用

**Reason**: 校验环节不存在，该保证不再适用。各写接口自身的参数校验与路径守卫保持不变。

**Migration**: 无需迁移动作。原受保护接口现在匿名可用；如需对外暴露服务，应在反向代理层（Nginx 等）加认证，或在后续变更中引入真正的账号体系。

# Spec Delta

## REMOVED Requirements

### Requirement: RAG 控制接口受访问口令约束

**Reason**: `access-control` 能力退役后，本能力引用的口令约束不再存在。RAG 控制接口恢复为免口令，与设置页其它接口一致。

**Migration**: 无需迁移。RAG 参数读写与 sidecar 重启的行为、参数校验与错误契约全部保持不变，变化的只是"是否需要先通过口令校验"。

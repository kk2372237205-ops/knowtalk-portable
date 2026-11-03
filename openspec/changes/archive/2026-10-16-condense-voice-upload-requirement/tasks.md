# Tasks

## 1. 篇幅收敛

- [x] 1.1 把主规格 `openspec/specs/digital-human-api/spec.md` 的 `声音克隆样音管理` 描述改为本变更 delta 中的压缩版本，逐条比对确认未丢失任何要求：三个接口契约（`POST /api/voice/upload`、`GET /api/voices`、`POST /api/voice/apply`）、名称合法性校验、扩展名允许集合、落盘路径限定在 `data/voices/` 之内。验证：逐句对照改动前的需求正文，列出的每一条 MUST 都能在新正文中找到对应表述
- [x] 1.2 确认该需求的 5 个场景一个不少。验证：`openspec validate condense-voice-upload-requirement --type change --strict` 通过（校验器会在 MODIFIED 丢场景时报错）

## 2. 校验基线复核

- [x] 2.1 归档本变更。验证：`openspec archive condense-voice-upload-requirement --yes` 退出码为 0
- [x] 2.2 确认 `digital-human-api` 的篇幅告警消失且未引入新问题。验证：`openspec validate digital-human-api --type spec --strict` 通过
- [x] 2.3 确认全仓 strict 校验回到"仅剩未完成变更的既有失败项"，没有新增回归。验证：`openspec validate --all --strict` 的失败项集合与本次收敛前相比，只少了 `spec/digital-human-api`
- [x] 2.4 确认本次为纯规格措辞改动，未触碰实现。验证：`git status` 中本变更的改动只落在 `openspec/` 下

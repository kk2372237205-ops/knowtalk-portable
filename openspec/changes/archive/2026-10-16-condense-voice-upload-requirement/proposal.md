# Proposal

## Why

变更 `harden-file-paths-and-cors` 归档后，`digital-human-api` 主规格里的 `声音克隆样音管理` 需求描述超过了 500 字符的篇幅上限：

```
⚠ [WARNING] requirements[18]: Requirement text is very long (>500 characters).
```

这使 `openspec validate --all --strict` 从 1 项失败变成 2 项——本仓库此前是 strict 全绿，这条回归是本次引入的。非 strict 的 `openspec validate` 仍判定通过（`failed: 0`），因此不影响功能，但它破坏了仓库既有的校验基线。

起因很具体：该需求原本 389 字符（合规），描述 `POST /api/voice/upload`、`GET /api/voices`、`POST /api/voice/apply` 三个接口的契约；本次为上传接口补上名称与扩展名校验时，把校验细节原位追加进了同一段描述，于是越界。

## What Changes

- 把 `声音克隆样音管理` 的描述压缩回 500 字符以内（实测 477），**不改变任何一条已声明的要求**：三个接口的契约、名称合法性校验、扩展名允许集合、落盘路径必须位于 `data/voices/` 之内，全部逐条保留，只是改用更紧凑的措辞。
- 该需求的全部 5 个场景（上传并应用音色、列出声音、未指定声音文件、名称含路径分隔符、扩展名不在允许集合内）原样保留。
- 不新增、不删除、不改写任何行为；不触碰实现代码与测试。

**为什么不做更彻底的拆分**：这条需求真正的结构问题是"一条需求覆盖三个接口"，理想做法是拆成 `声音样本上传` 与`声音列表与音色应用` 两条。但 OpenSpec 的 `MODIFIED` 会用整块替换原需求，且校验器要求 MODIFIED 块不得丢弃原规格已有的场景，因此无法把场景"搬"到新需求下；真正的拆分需要走 `REMOVED` + `ADDED`（把未废弃的需求标成移除），代价与审阅面都大得多，超出"修回校验基线"的目的。本次只做篇幅收敛，结构问题另行登记（见 Impact）。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `digital-human-api`：`声音克隆样音管理` 的描述篇幅收敛到 500 字符以内，要求与场景集合不变。

## Impact

- **修改文件**：`openspec/specs/digital-human-api/spec.md`（经本变更归档写入）。
- **不影响**：任何实现代码、测试、接口行为、前端、依赖清单、部署方式。
- **遗留（未在本变更处理）**：`声音克隆样音管理` 一条需求覆盖三个接口的结构问题。要做真正的拆分，需要另开一个以 `REMOVED` + `ADDED` 为基础的重构变更，并逐条给出 `Reason` 与 `Migration`。
- **风险**：极低。纯措辞压缩，校验器会逐场景比对，丢弃任何场景都会直接报错。

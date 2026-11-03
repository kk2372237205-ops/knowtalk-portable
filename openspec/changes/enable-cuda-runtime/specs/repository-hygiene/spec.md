# Spec Delta

## ADDED Requirements

### Requirement: GPU 加速依赖的构建变体必须被声明并可校验

需要 GPU 加速的依赖 MUST 在依赖清单与文档中声明其构建变体（例如 CUDA 版本）与可复现的安装来源；MUST NOT 只声明包名而使安装结果取决于源站默认值。安装后 MUST 校验实际可用设备，与声明不一致时 MUST 明确报告，MUST NOT 静默退回 CPU 运行。

#### Scenario: 只声明包名而得到与预期不符的构建

- **WHEN** 清单只写了包名（如 `torch`）而未指明构建变体，且从默认源安装得到的是 CPU 构建
- **THEN** 设备校验 MUST 发现实际设备与文档声明不一致
- **AND** MUST 明确报告该情况，MUST NOT 视为正常

#### Scenario: 按文档命令可复现地安装

- **WHEN** 按文档给出的安装来源与版本安装依赖
- **THEN** 安装 MUST 得到所声明的构建变体
- **AND** 设备校验 MUST 通过并报告实际使用的设备

#### Scenario: 声明了 GPU 构建但设备不可用

- **WHEN** 已安装 GPU 构建的依赖，但设备不可用（驱动缺失、显存被占用或被显式禁用）
- **THEN** 服务 MUST 明确报告「已声明 GPU 但当前落到 CPU」这一事实
- **AND** MUST NOT 让使用者在不知情的情况下以为在用 GPU

#### Scenario: 设备选择集中在单一入口

- **WHEN** 审查各推理模块的设备获取方式
- **THEN** 设备 MUST 由统一的入口决定，MUST NOT 在各模块中散落硬编码的设备调用
- **AND** 例外（确有必要的硬编码）MUST 被记录并说明原因

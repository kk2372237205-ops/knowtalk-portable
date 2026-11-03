# 抠像与背景合成

人像 alpha 素材的生成口径，以及运行时把单帧人像合成到自定义背景上的约束。

## ADDED Requirements

### Requirement: 背景合成必须按动作自己的 alpha 进行

合成一帧时，MUST 使用「该帧所属帧源」对应的 alpha（`matte/base/` 或
`matte/gestures/<gid>/`）。该动作没有 alpha 时 MUST NOT 回退使用 `base` 的 alpha——
不同动作的人体轮廓不同，套错 alpha 会把背景当人体保留，合成出白色残影。

缺素材时 MUST 返回原帧（保持原背景）并记录一次显式 warning；MUST NOT 静默忽略。

#### Scenario: 动作自带 alpha
- **WHEN** 帧源为 `gestures.wave_one` 且 `matte/gestures/wave_one/` 存在
- **THEN** 合成 MUST 使用该目录下的 alpha，MUST NOT 使用 `matte/base/`

#### Scenario: 动作为新生成、尚无 alpha
- **WHEN** 帧源为 `gestures.idle_sway` 而 `matte/gestures/idle_sway/` 不存在
- **THEN** 合成 MUST 返回原帧（不换背景）
- **AND** MUST 记录 warning 指明缺失目录
- **AND** MUST NOT 使用 `matte/base/` 的 alpha

### Requirement: 合成结果缓存必须按真实输入区分

合成结果的缓存键 MUST 唯一标识「实际使用的 alpha 素材 + 该素材内的帧号 + 输出尺寸」。
MUST NOT 用「请求的帧源」做键后再回退素材——回退会让不同帧算出同一个键，
导致整个动作返回同一张图（画面冻结）。

#### Scenario: 连续多帧合成
- **WHEN** 对同一动作的第 0、20、60 帧依次调用合成
- **THEN** 三帧输出 MUST 互不相同（源帧不同）

#### Scenario: 静默循环复用
- **WHEN** 同一帧被反复合成（源帧会循环复用）
- **THEN** 第二次起 MUST 命中缓存，MUST NOT 重复计算

### Requirement: 单帧合成必须控制在实时预算内

1080×538 的单帧合成 MUST 在 8ms 以内完成（20fps 时每帧预算 50ms）。
颜色反解（去溢色）MUST 与 alpha 混合合并成同一趟定点运算，
MUST NOT 逐像素走 numpy float32 的多次全图乘法。

#### Scenario: 合成耗时
- **WHEN** 在 `musetalk_ke`（538×1080）上合成一帧带背景的帧
- **THEN** 单帧耗时 MUST ≤ 8ms

### Requirement: 动作生成必须自带抠像

生成一个动作后，流程 MUST 自动为该动作生成 alpha 素材（`matte/gestures/<gid>/`）。
这一步失败 MUST NOT 让整次生成判失败，但 MUST 记 warning。

#### Scenario: 生成完成
- **WHEN** 动作制作作业进入 `converting` 阶段并完成拆帧
- **THEN** MUST 为该 `gesture_id` 生成 `matte/gestures/<gid>/` 下的逐帧 alpha
- **AND** `matte/manifest.json` MUST 增加该帧源条目

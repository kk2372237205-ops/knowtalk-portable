# avatar-compositing Specification — Delta

## ADDED Requirements

### Requirement: 接地阴影必须由该帧自己的 alpha 求接触带

抠像时脚下的接触阴影 alpha 低于外推下限，会被当作背景丢弃，人像贴到新背景上
脚下是一条齐刷刷的切口、有悬浮感。补接地阴影时 MUST 从**当前帧自己的 alpha**
求接触带：取实心像素（alpha ≥ 200）的最低行作为触底 y，在它上方一小段内求水平
跨度作为接触宽度，中心 x 取跨度的中点。MUST NOT 用一套固定的画面底部椭圆——
动作里有抬腿、侧身、重心转移，接触点会左右移动，固定椭圆会在人动起来时脱脚。

#### Scenario: 正常站姿

- **WHEN** 一帧 alpha 的实心区底部横跨 x∈[420, 660]、最低行 y=1010
- **THEN** 接触中心 MUST 落在 x≈540
- **AND** 接触宽度 MUST 约为 241

#### Scenario: 动作中重心移到左脚

- **WHEN** 同一形象另一帧的实心区底部整体左移
- **THEN** 阴影中心 MUST 跟着左移
- **AND** MUST NOT 沿用其它帧算出的位置

### Requirement: 接地阴影只作用于背景层，不得改变人体像素

阴影 MUST 以"遮挡环境光"的方式乘在背景上：`bg' = bg · (1 - s·strength·(1-shade))`，
再走原有的 `out = a·F + (1-a)·bg'`。MUST NOT 把阴影叠在合成之后的结果上——
那样等于给人像的鞋面和裤脚糊上一层灰，抠像辛苦保住的边缘又被涂回去。

#### Scenario: 人像躯干与头部区域

- **WHEN** 开启阴影后合成同一帧
- **THEN** alpha ≥ 250 的实心人体像素 MUST 与关闭阴影时逐像素一致

#### Scenario: 脚下的背景

- **WHEN** 开启阴影后合成同一帧
- **THEN** 接触带正下方的背景像素 MUST 比关闭阴影时更暗
- **AND** 阴影中心 MUST 是最暗处，向外单调变淡

### Requirement: 阴影不得就地修改缓存的背景图

背景图按尺寸缓存在 `_bg_cache` 里被所有帧共用，叠阴影时 MUST 先复制再改。
MUST NOT 就地写回缓存——那会让阴影一帧一帧累积，几秒内把脚下压成一块黑。

#### Scenario: 连续合成 100 帧

- **WHEN** 连续对同一尺寸的 100 帧做合成（缓存背景命中）
- **THEN** 第 100 帧脚下阴影的暗度 MUST 与第 1 帧一致
- **AND** 缓存中的背景图 MUST 保持未被叠过阴影的状态

### Requirement: 帧内没有可靠脚部时不得画阴影

alpha 全空（还没推上来的帧）或接触带像素太少（人像被画面下边缘裁掉、只剩一条边）
时 MUST 跳过阴影。MUST NOT 在这时退化成"画面底部居中来一片"——被裁掉的画面里
硬画一片椭圆，会在人像脚下凭空多出一团与姿态无关的暗斑。

#### Scenario: 空帧

- **WHEN** alpha 全为 0
- **THEN** 合成结果 MUST 严格等于背景图
- **AND** MUST NOT 出现任何阴影

### Requirement: 接地阴影必须在实时预算内

脚部求解与阴影图 MUST 按「帧源 + 帧号 + 尺寸 + 参数签名」缓存，热路径上只保留
一次小范围的乘法。1080×538 单帧合成（含外推与阴影）的中位耗时 MUST 保持在
26 ms 以内——阴影带来的增量 MUST 低于 3 ms。

#### Scenario: 连续合成一个动作序列

- **WHEN** 逐帧合成 107 帧
- **THEN** 单帧耗时中位数 MUST 低于 26 ms
- **AND** 阴影缓存的键 MUST 包含参数签名（改参数后旧缓存不得再命中）

### Requirement: 阴影配置必须与背景配置一样可热重载

阴影参数落 `data/settings/shadow.json`，检测方式沿用背景那套「修改时间 + 大小」戳。
`AvatarBackground` 的戳 MUST 同时包含两个配置文件，任一变化都触发重载。
MUST NOT 要求用户重连数字人才能看到参数变化。

#### Scenario: 界面里拖动强度滑块

- **WHEN** 写入 shadow.json 后合成下一帧
- **THEN** 该帧 MUST 使用新的强度值
- **AND** 旧的阴影缓存 MUST 全部作废

### Requirement: 阴影默认关闭时不得改变既有合成结果

`shadow.enabled` 为 false 时，合成 MUST 走原路径、结果与改动前逐像素一致。
MUST NOT 因为引入阴影而在关闭态下多一次复制或改变像素舍入。

#### Scenario: 关闭阴影

- **WHEN** 配置为 `{"enabled": false}` 并合成一帧
- **THEN** 输出 MUST 与不传任何阴影配置时逐像素相同

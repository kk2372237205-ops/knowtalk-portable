# Spec Delta

## ADDED Requirements

### Requirement: 音频特征写入 MUST 有界

`--model musetalk` 下，音频特征线程向输出队列（音频帧）与特征队列写入时 MUST 使用**有界等待**；目标队列已满时 MUST 丢弃最旧项后继续写入，MUST NOT 无限期阻塞。单次写入的等待上限 MUST 有界且可预期。

连接断开或会话被踢出后，渲染主循环 MUST 能回到退出判断并释放该会话资源，MUST NOT 因队列写入阻塞而无法退出。

#### Scenario: 消费者停止取出时不阻塞

- **WHEN** 推理线程停止消费，输出队列或特征队列已满，特征线程继续写入
- **THEN** 每次写入 MUST 在有限时间内返回
- **AND** 队列长度 MUST NOT 超过其容量
- **AND** 特征线程 MUST NOT 永久挂起

#### Scenario: 断线后可退出

- **WHEN** 连接断开后推理线程不再消费
- **THEN** 渲染主循环 MUST 能在有限时间内走到退出判断
- **AND** 该会话的资源 MUST 可被释放（含踢出路径）

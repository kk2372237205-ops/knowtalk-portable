# 全息舱（含退出资源释放）

## MODIFIED Requirements

### Requirement: 摄像头接近必须能触发迎宾

`scripts/proximity_detect.py` MUST 作为守护进程运行，基于摄像头检测人体接近，并在检测到接近时写 proximity 状态并触发迎宾问候。接近阈值与开关 MUST 可通过 `get_proximity/set_proximity` 读写。

桌面端退出时 MUST 主动结束该守护进程并释放摄像头，MUST NOT 依赖用户先点「停止感应」。

#### Scenario: 检测到接近

- **WHEN** 摄像头检测到人体进入接近范围
- **THEN** 系统 MUST 更新 proximity 状态并触发一次迎宾问候，MUST NOT 在连续帧中重复触发

#### Scenario: 接近开关可配置

- **WHEN** 运营关闭接近迎宾
- **THEN** `set_proximity(enabled=False)` 后守护进程 MUST 停止触发问候，已存状态 MUST 保持可查

#### Scenario: 退出桌面端
- **WHEN** 感应正在运行（`enabled=true`）时用户关闭桌面端窗口
- **THEN** 桌面端 MUST 结束 `proximity_detect.py` 子进程
- **AND** MUST 删除 `data/kiosk/proximity.pid`
- **AND** 摄像头 MUST 在 3 秒内可被重新打开

#### Scenario: 进程已被外部杀掉
- **WHEN** 退出时 `proximity.pid` 指向的进程已不存在
- **THEN** 退出流程 MUST NOT 报错，MUST 照常清理 pid 文件

# Spec Delta

## Purpose

把课堂上学员的一段语音在本地转写成文字，供提问模式的作答使用；本机只有 CPU，因此必须在可预期的延迟与资源占用下工作，并在模型或依赖不可用时给出明确失败而不是长时间挂起。

## ADDED Requirements

### Requirement: 语音转写接口受理音频并返回文本

服务 MUST 提供一个接受音频并返回转写文本的接口。成功时 MUST 返回识别出的文本，MUST NOT 返回任何临时文件的绝对路径。

#### Scenario: 成功转写

- **WHEN** 提交一段含清晰中文语音的音频
- **THEN** 响应 MUST 为成功并给出识别文本
- **AND** 文本 MUST 非空

#### Scenario: 响应不含临时路径

- **WHEN** 转写成功
- **THEN** 响应体 MUST NOT 包含服务器文件系统路径

### Requirement: 音频格式与体积约束

接口 MUST 明确声明接受的音频格式与体积上限；超出上限 MUST 在上限处拒绝并给出业务错误，MUST NOT 在任何情况下把完整音频一次性读入内存而不设上限。

#### Scenario: 超出体积上限

- **WHEN** 提交的音频大于声明的上限
- **THEN** 服务 MUST 返回业务错误并说明上限
- **AND** MUST NOT 进入识别流程

#### Scenario: 不受支持的格式

- **WHEN** 提交的音频格式不在声明范围内
- **THEN** 服务 MUST 返回业务错误并说明支持的格式

### Requirement: 识别语言可指定且默认中文

接口 MUST 支持指定识别语言，未指定时 MUST 以中文为默认语言。

#### Scenario: 默认中文

- **WHEN** 请求未指定语言并提交一段中文语音
- **THEN** 识别 MUST 按中文进行

#### Scenario: 指定其它语言

- **WHEN** 请求指定了另一种受支持的语言
- **THEN** 识别 MUST 按该语言进行

### Requirement: 识别模型可配置且支持本地模型目录

模型规格 MUST 可配置，MUST 支持直接指向本地模型目录以用于离线部署。配置缺失时 MUST 回退到内置默认规格。

#### Scenario: 使用本地模型目录

- **WHEN** 配置指向一个本地模型目录
- **THEN** 识别 MUST 使用该目录中的模型，MUST NOT 触网下载

#### Scenario: 配置缺失时回退默认

- **WHEN** 未配置模型规格
- **THEN** 服务 MUST 使用内置默认规格
- **AND** MUST NOT 因缺少配置而失败

### Requirement: 模型按需加载且只加载一次

模型 MUST 在首次转写时加载，MUST NOT 在服务启动时加载；加载后 MUST 在进程内复用，同一次运行中 MUST NOT 重复加载同一规格的模型。

#### Scenario: 启动不加载模型

- **WHEN** 主服务启动完成但尚未有任何转写请求
- **THEN** 识别模型 MUST NOT 已被加载

#### Scenario: 复用已加载模型

- **WHEN** 连续提交两次转写请求且规格未变
- **THEN** 模型 MUST 只被加载一次

### Requirement: CPU 环境下的延迟必须有界并如实暴露

接口 MUST 受一个可配置的超时约束；超时后 MUST 返回业务错误而不是无限等待。服务 MUST 在文档中如实说明 CPU 环境下的典型延迟与推荐模型规格。

#### Scenario: 超时返回错误

- **WHEN** 一次转写超过配置的超时
- **THEN** 服务 MUST 返回业务错误并说明超时
- **AND** MUST NOT 让请求永久挂起

#### Scenario: 延迟与推荐规格已成文

- **WHEN** 查阅项目文档
- **THEN** MUST 能查到 CPU 下的延迟量级与推荐模型规格

### Requirement: 并发转写必须受限以免占满 CPU

服务 MUST 限制同时进行的转写数量；并发超出限制时 MUST 以业务错误立即拒绝，MUST NOT 排队到超时。

#### Scenario: 并发超出限制

- **WHEN** 同时提交的转写请求超过并发上限
- **THEN** 超出的请求 MUST 立即收到业务错误并说明正在忙
- **AND** MUST NOT 阻塞等待到超时

#### Scenario: 拒绝后仍可继续使用

- **WHEN** 一次并发拒绝发生后再次提交请求
- **THEN** 请求 MUST 能被正常处理

### Requirement: 识别不到语音内容时返回明确失败

音频可解析但不含可识别的语音内容时，服务 MUST 返回业务错误并说明未识别到语音，MUST NOT 返回空文本当作成功。

#### Scenario: 静音音频

- **WHEN** 提交一段静音音频
- **THEN** 服务 MUST 返回业务错误并说明未识别到语音

#### Scenario: 音频无法解析

- **WHEN** 提交的文件不是可解析的音频
- **THEN** 服务 MUST 返回业务错误并说明无法解析

### Requirement: 依赖或模型不可用时的失败必须可预期

当转写依赖未安装或模型文件缺失时，服务 MUST 返回业务错误并指明缺失项，MUST NOT 抛出未捕获异常导致连接中断。该失败 MUST NOT 影响主服务的其它功能。

#### Scenario: 依赖未安装

- **WHEN** 转写依赖未安装时调用接口
- **THEN** 服务 MUST 返回业务错误并指明缺失的依赖
- **AND** 主服务的对话、朗读与幻灯片接口 MUST 仍可正常使用

#### Scenario: 模型文件缺失

- **WHEN** 配置指向的本地模型目录不存在或不完整
- **THEN** 服务 MUST 返回业务错误并指明该路径不可用

### Requirement: 临时文件必须清理

转写过程中产生的临时文件 MUST 在请求结束时被删除，无论成功或失败。

#### Scenario: 成功路径清理

- **WHEN** 一次转写成功完成
- **THEN** 该次请求产生的临时文件 MUST 已被删除

#### Scenario: 失败路径清理

- **WHEN** 一次转写在识别阶段失败
- **THEN** 该次请求产生的临时文件 MUST 仍被删除

### Requirement: 中文识别必须输出简体中文

识别中文时 MUST 输出简体中文，MUST NOT 返回繁体字形。该行为 MUST 由服务端默认保证，MUST NOT 依赖调用方每次显式指定。

#### Scenario: 中文语音输出简体

- **WHEN** 提交一段中文普通话语音
- **THEN** 识别文本 MUST 为简体中文
- **AND** MUST NOT 出现繁体字形

#### Scenario: 调用方未指定任何字形参数

- **WHEN** 请求未携带任何与字形或初始提示有关的参数
- **THEN** 输出 MUST 仍为简体中文

### Requirement: 转写引擎所需的 CUDA 运行库不得成为隐式前提

当转写引擎声明支持 GPU 但其所需运行库未就绪时，服务 MUST 以可预期的方式失败或明确回退到 CPU 并报告，MUST NOT 在运行中抛出未捕获的库加载错误。

#### Scenario: GPU 声明可用但运行库缺失

- **WHEN** 转写引擎报告存在 CUDA 设备，但实际执行时缺少所需运行库
- **THEN** 服务 MUST 明确报告该情况并回退到 CPU
- **AND** MUST NOT 让请求以未捕获异常结束

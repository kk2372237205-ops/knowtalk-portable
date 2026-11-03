# Tasks

## 1. 默认兜底

- [x] 1.1 `config.py`：`--TTS_FALLBACK` 的默认值改为「在提供系统离线语音能力的平台上为 `sapi`，其它平台为空」，平台判据与 `tts/sapi.py` 的 `IS_WINDOWS` 一致（不新造判据）。验证：Windows 上不带该参数启动时解析出的 `opt.TTS_FALLBACK` 等于 `sapi`；打桩为非 Windows 时为空
- [x] 1.2 `utils/settings_store.py`：`tts.fallback` 的内置默认值使用与 1.1 **同一个**判据来源。验证：单元测试断言未显式配置时 `resolve('tts.fallback')` 在 Windows 为 `sapi`、非 Windows 为空，且该值来自共享常量而非各写一份（防止两处漂移）
- [x] 1.3 显式配置仍然优先，且必须能关闭。验证：`tests/test_settings_store.py` 既有断言全部通过（含「设置页写入 sapi」「显式留空不得残留 sapi」「环境变量属于显式配置」「`--TTS_FALLBACK` 命令行优先」），并新增一条「未显式配置时取到默认值」的断言
- [x] 1.4 新默认值带来的两处措辞必须同步，否则使用者会被误导：`tts/edge.py` 的失败提示不再要求"去配置回退"（默认已有）；`tts/base_tts.py` 中「备用引擎与主引擎同名」不再报 ERROR——默认兜底生效后 `--tts sapi` 属于正常配置，报 ERROR 会让人以为配错了。验证：`tts="sapi"` 且默认兜底为 `sapi` 时只产生 WARNING 级日志，且提示语说明无需额外配置

## 2. 会话内短路

- [x] 2.1 `tts/base_tts.py`：记录主引擎的连续失败次数，主引擎成功时归零。验证：单元测试断言一次成功后计数归零
- [x] 2.2 短路条件：连续失败达到阈值、且备用引擎已成功接管时，本会话后续合成 MUST 直接使用备用引擎。验证：单元测试断言达到阈值后不再调用主引擎，而备用引擎继续产出
- [x] 2.3 单次失败 MUST NOT 短路。验证：单元测试断言「失败一次 → 主引擎恢复正常」时后续仍优先调用主引擎
- [x] 2.4 短路状态下文本 MUST 仍被合成：不得被拒绝、跳过或丢弃。验证：单元测试断言短路期间提交的文本仍产出音频帧
- [x] 2.5 没有可用备用引擎时 MUST NOT 短路。验证：单元测试断言未配置备用引擎时连续失败不进入短路，后续请求仍尝试主引擎
- [x] 2.6 备用引擎失效时 MUST 解除短路。验证：单元测试断言短路期间备用引擎失败后，下一次合成重新调用主引擎
- [x] 2.7 新会话 MUST 重新尝试主引擎（不沿用上一会话的短路状态）。验证：单元测试断言新建的 TTS 实例首次合成调用主引擎
- [x] 2.8 短路与解除都必须留下日志，使故障区间可追。验证：单元测试断言进入短路与解除短路各产生一条 WARNING 级日志，且进入的日志说明「已由备用引擎接管」

## 3. 文档

- [x] 3.1 `README.md`：说明开箱默认兜底、如何关闭、音质取舍，并说明会话内短路行为（首次失败仍会等待约数秒，之后不再重复等待）。验证：文档含默认值、关闭方法、音质取舍与短路说明，且延迟数字与实测一致

## 4. 验证

- [x] 4.1 全部单元测试通过，且前端测试一并通过。验证：`python -m unittest` 退出码为 0（314 tests OK）；`node --test tests/js/quiz-panel.test.js tests/js/settings-collect.test.js` 退出码为 0（16 tests pass）
- [x] 4.2 `openspec validate --all --strict` 通过。验证：命令退出码为 0（15 passed, 0 failed）
- [x] 4.3 端到端真机验证（不依赖替身）：在 `edgetts` 真实返回 403 的情况下，主引擎失败后自动落到 `sapi` 并产出音频帧；从进入短路的那一段起，不再出现新的 edgetts 请求失败。验证：实测第 1 段 5.33s / 250 帧、第 2 段 3.64s / 250 帧，之后 `skip_primary=True`，第 3、4 段各 0.10s / 250 帧；事件点首帧 `status=start`、末帧 `status=end` 且合并了 `slide_id`
- [x] 4.4 变异检查：把实现逐处故意改坏，确认每处都有测试抓住。验证：`tests/_mutation_check_tts_fallback.py` 输出「抓住 12，存活 0，跳过 0」
- [x] 4.5 复核仓库状态：无未跟踪文件、无临时脚本残留。验证：`git status` 干净

## Workflow follow-up

- 本变更依赖 `add-offline-tts-engine` 引入的 `speech-synthesis` 能力，归档顺序上应先归档后者，使该能力已有基线；否则基线 Purpose 会来自本变更的 delta。
- 归档后复核 `openspec/specs/speech-synthesis/spec.md` 同时包含两个变更的要求。

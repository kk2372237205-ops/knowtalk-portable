# 任务

## 1. 门禁与规格

- [x] 1.1 新建变更 `add-lecture-recording`（`validate --strict` 通过）
- [x] 1.2 在 `unify-site-visual` 追加「沉浸式展示页必须共用纸面皮肤」（3 场景）
- [x] 1.3 在 `add-workbench-shell` 追加「左栏必须呈现导航层级」（3 场景）

## 2. 展示页皮肤

- [x] 2.1 `web/knowtalk-chat.html` 接 `body.site`，按 内联样式 → `/shell.css?v=3` → `/display.css?v=2` 的顺序引入。验证：`test_lecture_recording.py` 断言加载顺序
- [x] 2.2 顶栏由蓝紫渐变改为纸面单条页头（课程名 / 切换课程 / 暂停播放 / 状态 / 返回工作台），控件靠右。验证：截图 `docs/design/display-recording.png`
- [x] 2.3 三栏、字幕条、对话气泡、引用标签、提问面板、按钮统一到纸面令牌。验证：`body.site` 上把 `--brand/--bg/--text/--line` 重定向，页面 500 行旧规则整体落到新皮肤
- [x] 2.4 窄视口：≤720px 暂停按钮只留图标、返回工作台收起；≤1023px 折叠开关钉到「课件 / 对话」分隔线上。验证：`display-900.png`、`display-600.png`

## 3. 录制

- [x] 3.1 `web/recorder.js`：1280×720 合成画布（人像左 34% / 课件右 / 字幕底）+ 借来的 TTS 音轨 + `MediaRecorder`。验证：`docs/design/rec-frame.png`（`ktRecorder.snapshot()` 取到的真实合成帧）
- [x] 3.2 入口叠在画面右上角，空闲「录制」/ 录制中「红点 + 停止 + 00:0X」两态。验证：截图 `display-recording.png` 显示「● 停止 00:06」
- [x] 3.3 停止即下载 `<课程名>_<YYYYMMDD-HHmmss>.webm`。验证：探针实测文件名 `Python基础知识——铁路客流量统计_20261025-235508.webm`，5 秒 281,089 字节
- [x] 3.4 断线自动停止（连续 5 拍 + 已录 >3s 才算断），结束后提示「断线前的内容已保存」。验证：`dropTicks` 断言
- [x] 3.5 浏览器不支持时入口禁用并写清原因；取不到音轨时当场提示「这次会录成无声视频」。验证：`test_lecture_recording.py` 三条断言
- [x] 3.6 全程在浏览器内：录制路径上无 `fetch` / `XMLHttpRequest`，服务端也没有新增录制接口。验证：`NoServerSideRecordingTestCase`

## 4. 工作台导航层级

- [x] 4.1 左栏改成「分组标题 + 缩进子项」的两级结构，组标题不可点。验证：`tests/test_workbench_nav.py` 10 条
- [x] 4.2 形象 / 课件 / 讲稿收进「本次素材」父组，成为第三层
- [x] 4.3 面板项带 `01/02/03` 序号；选中态只在子项上
- [x] 4.4 窄屏横排下仍分层（组标题隐藏、组间留竖线、收起的分组不留孤立线）。验证：`navhier-500.png`

## 5. 验证

- [x] 5.1 `tests/test_lecture_recording.py` 19 项 + `tests/test_workbench_nav.py` 10 项
- [x] 5.2 后端全量 **849 passed**（820 基线 + 29 新增）；前端 **187/188**（唯一失败仍是既有的 `hook-gate-shell`）
- [x] 5.3 门禁：`display.css` **0 FAIL / 1 WARN**、`workbench.css` **0 FAIL / 7 WARN**
- [x] 5.4 8 页 × 5 档视口 `scrollWidth == clientWidth`，越界全 0
- [x] 5.5 截图：`display-recording.png`、`display-900.png`、`display-600.png`、`navhier-1440.png`、`navhier-500.png`、`rec-frame.png`

## 6. 已知边界

- 录制产物的**编码**没能在本机无头环境里跑通：`--headless=new` 下 `MediaRecorder` 能构造、能 start、
  `ondataavailable` 会触发，但数据始终 0 字节；用纯画布 + 定时器画的独立探针同样 0 字节，
  说明是这台机器无头模式的编码器限制，不是录制逻辑。**需要在真实浏览器窗口里点一次「录制」确认**
  （代码里 `dataavailable` 走的是标准分片路径，失败时会落到「这次没录到画面」的提示，不会静默）。
- 合成帧本身已用 `ktRecorder.snapshot()` 验证过：幻灯片在右、字幕在底，人像槽位在左（探针环境
  无法喂真实视频轨，`video.videoWidth` 恒 0）。

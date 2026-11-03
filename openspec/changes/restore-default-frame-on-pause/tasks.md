# 任务清单

## 1. 服务端

- [x] 1.1 `BaseAvatar` 增加 `playback_paused` 标志（默认 False，`__init__` 里与手势状态同处初始化）
- [x] 1.2 `process_frames`：暂停态下输出画帧换成 `self._frame_at(...)`（基础素材）；
      两个出口都覆盖了——主路径（推理帧落地前）与 `queue.Empty` 补帧分支
      （后者原来会一直重复 `_last_out_frame`，暂停时表现为张嘴定格）
- [x] 1.3 新增 `POST /api/playback`：`sessionid` 不存在 → 404；`paused` 非布尔 → 400（且不改状态）；
      设置后幂等
- [x] 1.4 路由注册在 `setup_routes` 里，早于 `add_static('/')`

## 2. 前端

- [x] 2.1 `web/knowtalk-chat.html::togglePlayback` 的暂停/恢复两支各调一次 `/api/playback`
- [x] 2.2 调用失败只 `console.warn`，不影响本地静音/恢复

## 3. 验证

- [x] 3.1 单元：`tests/test_playback_pause.py` 7 项（404 / 400×3 / 幂等 / 设置与清除）
- [x] 3.2 真机部分验证：暂停后画面「活动量」降到 0.02（基本静止），恢复后升到 0.33
- [ ] 3.2b 未完成：用浏览器点「暂停播放」肉眼确认画面回到默认闭嘴帧
      （aiortc 探针在**同一服务实例上只接受一次 offer**，第二次必 500
      `ValueError: None is not in list`，无法连续多次采样，改由人工确认）
- [x] 3.3 回归：后端全量 **922 passed**（915 + 新增 7）
- [x] 3.4 `openspec validate --all --strict` **38/38 通过**

## 4. 补齐所有暂停入口（2026-10-27）

> 用户反馈：提问模式「已暂停播放」时嘴还在动。

- [x] 4.1 定位：quiz 自动暂停走 `pauseForQuiz → cutAudioForQuiz`，只静音本地音轨 /
      `audio.pause()`，**从未**调 `notifyPlaybackPaused` → 服务端 `playback_paused`
      始终为 false，照旧推理并推送说话帧
- [x] 4.2 前端新增唯一出口 `setPlaybackPaused(paused)`（变量 + 按钮 + 服务端一起改）；
      覆盖 `togglePlayback` / `cutAudioForQuiz` / `resumeFromQuizPause` /
      `resumeAudioForAnswer` / `pausePresentation` / `resumePresentation` /
      `startPresentation` / `postHuman`
- [x] 4.3 服务端暂停帧改取**形象自身的**基础素材：`base_avatar._base_frame_at()`，
      固定第 0 帧（多帧形象若按 idx 取会继续循环：嘴闭着身体却在动）；
      补帧分支同步改用
- [x] 4.4 规格：新增 Requirement「所有暂停入口都同步服务端暂停态」+ 3 场景；
      「暂停回落默认帧」补「动作播放期间暂停」场景与"不取当前帧源"约束
- [x] 4.5 测试：新增 `tests/test_playback_pause_frame.py` **9 项**（真跑
      `process_frames`：暂停帧恒为默认帧、不含动作帧、多帧形象不漂移、
      恢复后回到说话帧、进度冻结、素材不被改脏、`_base_frame_at` 无视帧源）；
      `tests/js/quiz-pause-on-question.test.js` 改断言 + 新增 4 项守卫
- [x] 4.6 回归：后端全量 **996 passed**；前端 199/200（`hook-gate-shell`
      为预存在失败）；`openspec validate --all --strict` **38/38**
- [ ] 4.7 人工确认（T11）：提问抛出题目后嘴必须停住；动作播放中暂停应回到默认姿态

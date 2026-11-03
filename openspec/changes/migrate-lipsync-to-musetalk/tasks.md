# 任务清单

## 1. 权重与环境

- [x] 1.1 `models/musetalkV15/unet.pth`（3.17GiB）+ `musetalk.json` —— 经 hf-mirror + Range 断点续传落地，zip 完整性校验 690 条目无损坏
- [x] 1.2 `models/sd-vae-ft-mse/`（VAE）
- [x] 1.3 `models/whisper/`（whisper-tiny，音频特征）
- [x] 1.4 `models/face-parse-bisent/`（`79999_iter.pth` + `resnet18-5c106cde.pth`）
- [x] 1.5 人脸框检测权重 `s3fd.pth`：复用 `avatars/wav2lip/face_detection/detection/sfd/s3fd.pth`（字节数与上游一致），复制到 musetalk 的 `face_detection/detection/sfd/` 与 torch hub 缓存
- [x] 1.6 68 点权重 2DFAN4：取 hf-mirror 的 TorchScript 版 `2DFAN4-cd938726ad.zip`（96MB），放 `<torch.hub>/checkpoints/`
- [x] 1.7 `torchvision==0.29.0`（与 torch 2.14.0+cu126 配对，`--no-deps` 安装，未改动 torch）
- [x] 1.8 `diffusers` 0.39.0 → 0.31.0（0.39 需要 transformers≥4.47 的 `Dinov2WithRegistersConfig`，本机 4.46.2）
- [x] 1.9 VAE 目录名必须是 `models/sd-vae`（`load_all_model` 用 `vae_type="sd-vae"` 拼路径），已从 `sd-vae-ft-mse` 改名
- [x] 1.10 `models/face-parse-bisent/resnet18-5c106cde.pth` 是老式 tar 格式，torch 2.6+ 默认 `weights_only=True` 拒绝 → 重存为新 zip 格式（不改上游代码）

## 2. 代码

- [x] 2.1 `avatars/musetalk/utils/preprocessing.py`：mmpose 导入与 `init_model` 改为可失败；新增 `get_face_landmarks()`，mmpose 缺失时回退 pip `face_alignment`（iBUG 68）
- [x] 2.2 同上：TorchScript 归档的 FAN 权重走 `torch.jit.load`（子类覆写 `_load_native`），绕过 `load_state_dict`
- [x] 2.3 同上：`from face_detection import ...` 加 sys.path 兜底，使 `python -m avatars.musetalk.genavatar` 可直接运行
- [x] 2.4 `avatars/audio_features/whisper.py`：`output_queue` / `feat_queue` 改用 `utils.queue_guard.put_capped`（有界等待 + 丢最旧 + DropReporter），对齐 `mel.py`
- [x] 2.5 上游 `get_landmark_and_bbox` 的裁切框算法**未改动**，只替换 68 点来源

## 3. 验证

- [x] 3.1 离线 S3FD 出框：`ke01` 80×108、`Mr.Chen` 75×100（无需联网）
- [x] 3.2 离线 68 点：`ke01` 鼻尖 (275,173)、下巴 (275,220)，裁切框 (231,126,315,220) = 84×94
- [x] 3.3 端到端素材生成（单帧，产物落 `D:/tmp/mt_probe`）：产出 `full_imgs/`、`mask/`、`coords.pkl=[[231,126,315,230]]`、`mask_coords.pkl`、`latents.pt`（`(1,8,32,32)` fp16）
- [x] 3.3b 推理链路验证：`load_all_model` → unet 前向 → vae 解码 `(1,256,256,3)` → `get_image_blending` 贴回 `(1080,540,3)`，贴回位置精准落在人脸
- [x] 3.3c **不换构图**（用户明确「不要近景的」）：直接用 `ke01` 原图生成正式形象 `data/avatars/musetalk_ke`
- [x] 3.3d 真实语音渲染：SAPI 离线合成 → whisper 特征 → 75 帧/3s，嘴型张/闭/露齿有明确区分
- [x] 3.4 素材形态与模型匹配：`ready` 按当前模型判定（`ACTIVE_MODEL`），前端下拉框不会再放出不兼容形象
- [x] 3.5 起服务实测：默认配置启动，日志 `Registered avatar/musetalk: MuseReal` +
  `Namespace(... model='musetalk', avatar_id='musetalk_ke')`，8010 监听正常；
  `GET /api/avatars` 里只有 `musetalk_ke` 为 `ready`，另三个 wav2lip 形象自动 `incomplete`
- [x] 3.5b 运行时加载器：`load_model()` + `load_avatar("musetalk_ke")` 读出
  `frames(1,1080,540,3)`、`masks(1,156,156,3)`、`coords`、`latents(1,8,32,32) fp16 cuda`
- [ ] 3.6 浏览器实测一次真实会话（朗读 + 嘴型），并按需微调
- [x] 3.6b 回归：`avatars.wav2lip_avatar` 导入正常、素材与配置未动、后端全量测试全绿
- [x] 3.7 测试：后端 **915 passed**；前端 `node --test tests/js/*.test.js` 195/196
  （唯一失败 `hook-gate-shell` 是预存在失败）；`openspec validate --all --strict` 36/36

## 4. 文档与归档

- [x] 4.1 本变更 proposal / specs
- [ ] 4.2 权重获取流程写入 `docs/`（镜像 + 断点续传 + 代理清空 + jit 权重）
- [ ] 4.3 `openspec validate --all --strict` 通过
- [ ] 4.4 归档

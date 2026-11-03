# 嘴型同步从 wav2lip 迁移到 MuseTalk v1.5

## 问题

现场反馈「现在的嘴型做得很烂」。逐层排查后，是三个原因叠在一起。

### 根因一：素材是小脸（最主要）

当前所有形象的人脸在源画幅里都极小：

| 形象 | 源画幅 | 检测到的人脸框 | 占画幅宽 |
|---|---|---|---|
| `ke01`（最后一次运行用的就是它） | 540×1080 | 80×108 | 14.8% |
| `ke` | 540×1080 | 80×108 | 14.8% |
| `Mr.Chen` | 538×1080 | 75×100 | 14.0% |

这些框被 `cv2.resize` 上采样到 256×256 再喂给模型——等于拿一张糊的小图去驱动口型。
wav2lip 对「小脸」极度敏感（项目此前已记录：1080p 里 170px 小脸必失败）。
三个形象同一口径，说明这是**系统性素材问题**，不是偶发。

### 根因二：嘴部幅度主旋钮是死的（代码缺陷）

`avatars/wav2lip_avatar.py:255` 调用 `self.model(audiofeat_batch, img_batch)`，
**没有传第三个位置参数 `a_alpha`**。而 `wav2lip_v2.py` 的 `forward(audio, face, a_alpha)`
里 `a_alpha` 是嘴部开口幅度的主旋钮（设计上 1.2~2.0 用于放大），默认 1.0 等于完全没调。
幅度只剩 `paste_back_frame` 里 `mouth_alpha` 的局部混合兜底，想让嘴张得更大根本调不动。

### 根因三：算法天花板

wav2lip 是 2020/2022 年的 GAN 方案，生成的嘴偏软、糊、缺牙齿与上唇细节，近景下明显「假」。
这是模型能力上限，调参改不了。

## 方案

迁移到项目里**已内置但缺权重**的 **MuseTalk v1.5**（扩散式口型，清晰度与真实感显著优于 wav2lip）。

1. **权重离线化**。本机网络环境苛刻，需要一套能复现的获取方式：
   - HuggingFace 直连被墙、公司代理回 502，只有 `hf-mirror.com` 可用；
   - 但 `huggingface_hub` 的 LFS 逻辑会把大文件重定向回 `huggingface.co` → 必失败，
     只能用裸 `urllib` GET 打镜像的 `/resolve/main/<path>`，并**清掉代理环境变量**；
   - 镜像对部分路径回 308（urllib 默认不跟随），大文件约每 500MB 断流 → 必须 `Range` 断点续传。
   - `TMElyralab/MuseTalk` 仓库里同时有 v1（`musetalk/pytorch_model.bin` 3.4GB）与 v1.5，
     本项目只认 `musetalkV15/unet.pth`，不要下错。
   - **素材生成**还需要 face-parsing（`79999_iter.pth` + `resnet18`）与**人脸框检测**：
     `s3fd.pth` 项目里 `avatars/wav2lip/face_detection` 已有一份（字节数与上游完全一致），直接复用。

2. **68 点关键点改用离线等价实现**。上游 `preprocessing.get_landmark_and_bbox` 依赖
   mmpose/dwpose，而这套东西在 Windows 上极难安装。但上游只用 68 点里的面部点算人脸框
   （x 取极值、上边界取「鼻尖上下对称」），因此**任何 iBUG 68 点实现都可等价替换**：

   - 优先 mmpose（保持上游行为不变）；
   - mmpose 缺失时回退 pip `face_alignment` 的 2DFAN-4（同为 iBUG 68 序）；
   - 该包要求的 state_dict 权重在 `adrianbulat.com`，国内直连/代理都只有 ~10KB/s；
     改用 hf-mirror 上的 **TorchScript 热力图版** `2DFAN4-cd938726ad.zip`，
     其输出 `(1,68,64,64)` 正是 `face_alignment>=1.5` 的 `get_preds_fromhm` 所期望的格式，
     只需绕过 `load_state_dict` 改用 `torch.jit.load`。

3. **素材重做**。MuseTalk 素材格式（`full_imgs/` + `mask/` + `latents.pt` + `coords.pkl`
   + `mask_coords.pkl`）与 wav2lip 完全不兼容，必须从源素材重新生成；
   同时要求**近景源素材**（脸宽 ≥300px）才能真正解决「小脸」。

4. **顺带加固 `WhisperASR` 的队列写入**。wav2lip 的 `mel.py` 早已改成
   `utils.queue_guard.put_capped`（有界等待 + 丢最旧），而 MuseTalk 的
   `avatars/audio_features/whisper.py` 仍是裸 `.put()`：
   `output_queue` 200 格、`feat_queue` 5 格，断线后推理线程不再消费 →
   `render()` 主循环会**永久阻塞**，再也回不到退出判断。

5. **保留回退路径**。wav2lip 与其形象素材一律不动，新形象用独立 id 承载，
   `--model wav2lip` 随时可切回。

## 影响

- 嘴型清晰度与真实感取决于源素材；换成近景大头照后会有质变，小脸素材则仍受限（但不再比现在差）。
- 首次生成素材需要一次性下载约 2.4GB 权重（已落盘到 `models/`），之后完全离线。
- MuseTalk 推理显存约 1.7GB（unet fp16）+ VAE，与数字人同机运行需注意显存余量。
- wav2lip 路径的所有既有行为、素材与配置保持不变。

# MuseTalk 形象支持动作（手势）

## 问题

手势模块的实现是**通用的帧源替换**：`base_avatar` 里 `_activate()` 把
`_active_frames / _active_faces / _active_coords` 指向手势素材，`_frame_at()`、`_face_at()`
都走这套抽象。看起来模型无关，但实际上**只有 wav2lip 能用**：

1. `avatars/musetalk_avatar.py::paste_back_frame()` 硬编码读
   `self.coord_list_cycle[idx]` / `self.frame_list_cycle[idx]` / `self.mask_list_cycle[idx]`
   —— 绕过了 `_active_*`，所以讲话时输出**永远是原形象**。
2. `inference_batch()` 只用 `self.input_latent_list_cycle`（原形象的 `latents.pt`），
   手势帧即便被取到，也没有与之匹配的 latent。
3. 手势素材目前只有 wav2lip 的**三件套**（`full_imgs/` + `face_imgs/` + `coords.pkl`），
   而 MuseTalk 推理需要 `latents.pt` + `mask/` + `mask_coords.pkl`（且 `coords.pkl`
   的元素序也不同：wav2lip 是 `(y1,y2,x1,x2)`，MuseTalk 是 `(x1,y1,x2,y2)`）。

结果：默认模型切到 `musetalk` 之后，讲稿里的 `[GESTURE:x]` 实际不起作用——
要么被摘除（形象上该手势不存在），要么只在静音补帧时一闪而过。

## 方案

### 1. 手势素材新增「MuseTalk 侧资产」

手势目录保持 wav2lip 三件套不动，另外加一层模型专属目录：

```
data/avatars/<aid>/gestures/<gid>/
├── full_imgs/            原图序列（两模型共用）
├── face_imgs/            wav2lip 输入裁剪
├── coords.pkl            wav2lip 人脸框 (y1,y2,x1,x2)
├── gesture.json          显示名等元数据
└── musetalk/             ← 新增
    ├── latents.pt        [1 份] 形象的 VAE latent（(1,8,32,32) fp16）
    ├── mask/             [1 张] 形象的遮罩
    ├── mask_coords.pkl   [1 份] 遮罩框 (x1,y1,x2,y2)
    ├── coords.pkl        [1 份] 人脸框 (x1,y1,x2,y2)
    └── asset.json        {"mode": "shared", ...}
```

**这一层是形象自身人脸资产的一份副本，长度恒为 1，不是逐帧编码动作帧。** 原因见
「后续调整」一节：生成出来的动作素材脸型会漂移，用它自己的脸做参考会让动作期间
"换一张脸"。

人脸框取形象基础素材的框，且整段恒定。动作素材的硬约束本来就是「头部零位移、首尾同姿态」，
框不会动；这样补资产时**既不需要逐帧跑关键点检测**（当前检测要 30~40 秒/帧，
100 帧就是 1 小时以上），也不需要逐帧跑 VAE 编码与人脸解析——纯文件拷贝，毫秒级。

### 2. 生成入口

动作素材制作（MiniMax 出片 → `generate_avatar` 转素材）完成后，为手势补 MuseTalk 资产。
单独成步骤而不是塞进主流程，便于「已有手势补算」和失败重试。

### 3. 运行期改为走 `_active_*`

- `_activate()` 除帧源外，一并记录手势的 MuseTalk 资产（latents / masks / mask_coords）；
- `inference_batch()` 的 latent 改取「当前帧源」的；
- `paste_back_frame()` 的 bbox / 原帧 / mask / mask_coords 全部改取当前帧源；
- 手势播完一遍后回到基础素材，行为与 wav2lip 一致。

### 4. 缺资产时降级而不是报错

手势存在但没补 MuseTalk 资产时，**按「该手势不可用」处理**：该段照常朗读、动作跳过，
并返回 warning（沿用现有「只摘除、不整篇报错」的口径）。绝不能因为缺资产让整场讲不出声。

## 后续调整：动作期间的人脸恒等于形象（2026-10-27）

最初按上面第 1 节实现为「逐帧编码动作帧的脸」，真机一跑就发现**动作期间脸会变胖**：
生成模型出的动作素材即使同一个人、同一构图，脸型也会漂移。实测同一形象下六套动作与
形象的人脸框几乎一致（宽高差 1%~5%、位移不到 4px），但框内的脸明显更宽——下颌更宽、
两颊更满、脸更圆。而 MuseTalk 输出的人脸形状跟随参考 latent，用动作帧自己的脸编码，
等于把这张"胖脸"喂给了模型。

因此资产改为**取自形象自身的一份副本**（长度 1）：动作只贡献身体与姿态，人脸永远由
形象驱动。连带好处：补资产不再需要 VAE 与 face parsing，从数十秒降到毫秒级，也不再
占 GPU；磁盘占用反而下降。旧版逐帧资产没有 `asset.json`，会被判为未就绪并自动迁移。

## 影响

- 默认的 MuseTalk 形象终于能播动作；wav2lip 路径行为完全不变。
- 每个手势补资产是纯文件拷贝（毫秒级），且动作期间脸型与形象一致，不再胖瘦漂移。
- 手势素材体积基本不变（`musetalk/` 下只多出一份几 KB 的 latent 与一张遮罩）。
- 4 秒的动作素材在 20fps 输出下原本会播约 5 秒（比原速慢 20%）；已按输出帧率重采样
  （`utils/gestures.py::playback_indices`，素材帧率记在 `gesture.json` 的 `fps`），
  播放时长与素材一致。

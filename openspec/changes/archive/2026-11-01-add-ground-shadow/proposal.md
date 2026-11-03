# 虚拟投影：给抠像后的人像补一圈脚下接地阴影

## 为什么

抠像把人从棚拍素材里挖出来，脚下的接触阴影 alpha 很低，被当作背景一起丢掉了
（外推下限 `EDGE_MIN=25` 以下不参与前景外推，这是对的——那是阴影不是人体）。
于是人像贴到新背景上，脚下是一条齐刷刷的切口，人"浮"在背景前面，没有落地感。
全息舱是 9:16 近距离观看的大屏，这个悬浮感一眼就能看出来，比抠像白边更伤观感。

素材里本来那片阴影也不能留：它是白棚地面上的阴影，换到深色/实景背景上会变成
一团说不清的灰，所以正确做法是**丢掉源阴影、在新背景上重新画一片**。

## 做什么

1. **求脚部接触带**：从该帧自己的 alpha 里取实心像素的最低行，在它上方一小段内
   求水平跨度，得到接触点（中心 x / 触底 y / 宽度）。各帧独立算，动作里抬腿、
   转身时阴影跟着走。
2. **画椭圆接地阴影**：径向渐变 + 纵向压扁的椭圆，中心落在接触点，软边用
   smoothstep，边缘过渡宽度由柔和度控制。
3. **以"遮挡环境光"的方式作用于背景层**：`bg' = bg · (1 - s·strength·(1-shade))`，
   再走原有的 `out = a·F + (1-a)·bg'`。人像实心区被 `a·F` 覆盖不受影响，
   只压暗脚下的背景——保持背景自身的色相，所以纯色底和实景底都成立。
4. **可配并热生效**：开关 / 强度 / 展开度 / 扁平度 / 柔和度 / 纵向偏移，
   落 `data/settings/shadow.json`，沿用背景那套 mtime 热重载，换参数下一帧就生效。
5. **界面与接口**：「背景设置」页新增「虚拟投影」卡片；`GET /api/avatar/background`
   回报当前阴影配置，新增 `PUT /api/avatar/shadow`。

## 影响

- `utils/avatar_background.py`：新增 `load_shadow_config` / `save_shadow_config` /
  `DEFAULT_SHADOW`；`AvatarBackground` 增加脚部求解、阴影图生成与缓存；
  `compose` 在背景层上叠阴影后再混合；热重载签名同时盯两个配置文件。
- `server/avatar_routes.py`：`get_background` 回报 `shadow`；新增 `put_shadow`
  并注册 `PUT /api/avatar/shadow`。
- `desktop_app.py`：`BackgroundPage` 新增「虚拟投影」卡片（自绘开关 + 滑块）。
- 新增 `tests/test_ground_shadow.py`。
- **不改已有接口语义**：`/api/avatar/background` 只**新增** `shadow` 字段；
  阴影默认关闭时合成结果 MUST 与改动前逐像素一致。

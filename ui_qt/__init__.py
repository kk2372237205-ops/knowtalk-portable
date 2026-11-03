# -*- coding: utf-8 -*-
"""Qt 版界面：PySide6 重写 Tkinter 桌面端。

与 Tk 版的根本差别（也是这次重写的动机）：

- **样式表集中管理**：Qt 的 QSS 是运行期生效的，换主题只需重新 `setStyleSheet`，
  不用像 Tk 那样重启整个界面进程。
- **布局是约束式的**：QHBoxLayout/QVBoxLayout 按可用空间分配，不会出现 Tk `pack`
  把控件塞到别人父容器里那种错位。
- **渲染与业务线程分离**：网络请求走 QThreadPool，界面线程永远不会被同步 HTTP 堵住，
  这正是旧版「启动时一片黑块」的根因。
- **DPR 自动缩放**：Qt6 默认按显示器 DPR 渲染，不需要 Tk 版那套 `S(px)` 手工换算。

本包只负责界面层，业务接口（主服务 REST / WebRTC）沿用 `desktop_app.py` 里已有的实现。
"""

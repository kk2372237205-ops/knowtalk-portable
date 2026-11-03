# -*- coding: utf-8 -*-
"""Qt 版页面。

开播页已接真链路（形象列表 → 主服务设置 → aiortc 收流 → 音频出声）；
待机与迎宾还是布局原型，数据未接。
"""

import os
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QScrollArea,
                               QSlider, QSpinBox, QVBoxLayout, QWidget)

from ui_qt import theme as ui_theme
from ui_qt import widgets as w

# 与 desktop_app.BASE 保持一致；不从 desktop_app 取，是为了不让开播页把 tkinter 拖进来
BASE = os.environ.get("KNOWTALK_BASE", "http://127.0.0.1:8010")


def local_session():
    """访问本机主服务的会话：**必须 trust_env=False**。

    机器上存在 HTTP_PROXY（开发机挂了本地代理，CI/办公网也常见）时，requests
    会把 127.0.0.1 也丢给代理，代理再回 502「upstream connect failed」——看起来
    像主服务挂了，其实是被代理挡了。
    """
    import requests
    sess = requests.Session()
    sess.trust_env = False
    return sess


class BasePage(QWidget):
    """页面骨架：大标题 + 说明 + 可滚动内容。"""

    TITLE = ""
    DESC = ""

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.setSpacing(14)
        head = QVBoxLayout()
        head.setSpacing(4)
        if self.TITLE:
            title = QLabel(self.TITLE)
            title.setObjectName("pageTitle")
            head.addWidget(title)
        if self.DESC:
            desc = QLabel(self.DESC)
            desc.setObjectName("muted")
            desc.setWordWrap(True)
            head.addWidget(desc)
        outer.addLayout(head)
        self.content = QHBoxLayout()
        self.content.setSpacing(14)
        outer.addLayout(self.content, 1)


class FullscreenBroadcast(QDialog):
    """Qt 全屏演示窗口：只放数字人与固定的字幕、菜单，不复制主窗口布局。"""

    def __init__(self, page):
        super().__init__(page.window())
        self.page = page
        self.setWindowTitle("知课 KnowTalking · 全屏演示")
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setModal(False)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(8)
        controls = QHBoxLayout()
        controls.addStretch(1)
        controls.addWidget(w.button("退出全屏", self.close, "ghost"))
        root.addLayout(controls)

        self.stage = w.VideoStage()
        self.stage.setMinimumSize(480, 540)
        root.addWidget(self.stage, 1)
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("subtitle")
        self.subtitle.setAlignment(Qt.AlignCenter)
        self.subtitle.setWordWrap(True)
        self.subtitle.hide()
        root.addWidget(self.subtitle)

        self.menu_host = QWidget()
        self.menu_layout = QHBoxLayout(self.menu_host)
        self.menu_layout.setContentsMargins(0, 0, 0, 0)
        self.menu_layout.setSpacing(8)
        self.menu_layout.setAlignment(Qt.AlignCenter)
        root.addWidget(self.menu_host)
        self.tip = QLabel("")
        self.tip.setObjectName("menuTip")
        self.tip.setAlignment(Qt.AlignCenter)
        root.addWidget(self.tip)
        self.sync_from_page()

    def sync_from_page(self):
        self.subtitle.setText(self.page.subtitle.text())
        self.subtitle.setVisible(not self.page.subtitle.text().isspace() and bool(self.page.subtitle.text()))
        self.tip.setText(self.page.menu_tip.text())
        self.page._build_menu_buttons(self.menu_layout)

    def closeEvent(self, event):  # noqa: N802
        self.page._fullscreen = None
        event.accept()


class BroadcastPage(BasePage):
    TITLE = "开播"
    DESC = "选形象后建立数字人连接；Qt 直接绘制视频帧，菜单和字幕布局固定。"

    avatarsLoaded = Signal(object, str)
    menusLoaded = Signal(object)
    statusChanged = Signal(str, bool)
    captionChanged = Signal(str)
    menuTipChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bridge = None
        self._connected = False
        self._sessionid = ""
        self._menus = []
        self._fullscreen = None

        left = QVBoxLayout()
        left.setSpacing(12)
        card = w.Card("数字人连接", "未连接时可以更换形象；连接后需要重新开播才生效。")
        self.avatar_combo = QComboBox()
        self.avatar_combo.setMinimumWidth(240)
        self.avatar_combo.addItem("正在读取形象列表…", "")
        card.body.addWidget(self.avatar_combo)
        self.voice_label = QLabel("音色可在「形象与设置」页调整")
        self.voice_label.setObjectName("muted")
        card.body.addWidget(self.voice_label)
        left.addWidget(card)

        action = w.Card("开播", "菜单固定在画面下方；回答以字幕形式显示在菜单上方。")
        row = QHBoxLayout()
        row.setSpacing(10)
        self.connect_btn = w.button("开始连接", self._toggle, "primary")
        row.addWidget(self.connect_btn)
        row.addWidget(w.button("打断朗读", self._interrupt, "ghost"))
        row.addStretch(1)
        action.body.addLayout(row)
        full_row = QHBoxLayout()
        full_row.addWidget(w.button("全屏演示", self._show_fullscreen, "ghost"))
        full_row.addStretch(1)
        action.body.addLayout(full_row)
        self.state = QLabel("等待主服务…")
        self.state.setObjectName("muted")
        action.body.addWidget(self.state)
        left.addWidget(action)
        left.addStretch(1)

        holder = QWidget()
        holder.setLayout(left)
        holder.setFixedWidth(380)
        self.content.addWidget(holder)

        display = QWidget()
        display_layout = QVBoxLayout(display)
        display_layout.setContentsMargins(0, 0, 0, 0)
        display_layout.setSpacing(8)
        self.stage = w.VideoStage()
        display_layout.addWidget(self.stage, 1)
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("subtitle")
        self.subtitle.setAlignment(Qt.AlignCenter)
        self.subtitle.setWordWrap(True)
        self.subtitle.hide()
        display_layout.addWidget(self.subtitle)
        self.menu_host = QWidget()
        self.menu_layout = QHBoxLayout(self.menu_host)
        self.menu_layout.setContentsMargins(0, 0, 0, 0)
        self.menu_layout.setSpacing(8)
        self.menu_layout.setAlignment(Qt.AlignCenter)
        display_layout.addWidget(self.menu_host)
        self.menu_tip = QLabel("")
        self.menu_tip.setObjectName("menuTip")
        self.menu_tip.setAlignment(Qt.AlignCenter)
        display_layout.addWidget(self.menu_tip)
        self.content.addWidget(display, 1)

        # 帧从 aiortc 线程来，只存最新一张；Qt 主线程按显示节奏取帧，不积压。
        self._frame_timer = QTimer(self)
        self._frame_timer.timeout.connect(self._pull_frame)
        self._menu_timer = QTimer(self)
        self._menu_timer.setInterval(15000)
        self._menu_timer.timeout.connect(self._load_menus)
        self._menu_timer.start()

        self.avatarsLoaded.connect(self._fill_avatars)
        self.menusLoaded.connect(self._fill_menus)
        self.statusChanged.connect(self._set_status)
        self.captionChanged.connect(self._set_caption)
        self.menuTipChanged.connect(self._set_menu_tip)
        self._load_avatars()
        self._load_menus()

    # ── 后端数据 ──────────────────────────────────────────────────
    def reload_avatars(self):
        """主服务就绪后由主窗口调用，刷新启动期间读失败的形象列表。"""
        self._load_avatars()

    def show_backend_error(self, text):
        self.state.setText(text or "主服务启动失败")

    def _load_avatars(self):
        def work():
            items, err = [], ""
            try:
                resp = local_session().get(BASE + "/api/avatars", timeout=6)
                payload = resp.json() or {}
                if resp.status_code != 200 or payload.get("code") not in (0, None):
                    err = payload.get("msg") or "接口返回 %d" % resp.status_code
                else:
                    data = payload.get("data")
                    items = data if isinstance(data, list) else []
            except Exception:  # noqa: BLE001
                err = "读不到形象列表：主服务可能仍在加载"
            self.avatarsLoaded.emit(items, err)

        threading.Thread(target=work, daemon=True).start()

    def _fill_avatars(self, items, err):
        self.avatar_combo.clear()
        if err or not items:
            self.avatar_combo.addItem("（没有可用形象）", "")
            self.state.setText(err or "形象库是空的，先去「形象与设置」生成一个")
            return
        for item in items:
            label = item.get("name") or item.get("id")
            if item.get("status") != "ready":
                label += "（素材不完整）"
            self.avatar_combo.addItem(label, item.get("id"))
        if not self._connected:
            self.state.setText("未连接")

    def _load_menus(self):
        def work():
            items = []
            try:
                resp = local_session().get(BASE + "/api/kiosk/menus/enabled", timeout=6)
                payload = resp.json() or {}
                items = (payload.get("data") or {}).get("items", []) or []
            except Exception:  # noqa: BLE001
                items = []
            self.menusLoaded.emit(items)

        threading.Thread(target=work, daemon=True).start()

    def _fill_menus(self, items):
        self._menus = items
        self._build_menu_buttons(self.menu_layout)
        if self._fullscreen is not None:
            self._fullscreen.sync_from_page()

    def _build_menu_buttons(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not self._menus:
            empty = QLabel("暂无启用菜单")
            empty.setObjectName("muted")
            layout.addWidget(empty)
            return
        for item in self._menus:
            title = str(item.get("title") or "菜单")
            btn = w.button(title, lambda _=False, value=item: self._activate_menu(value), "ghost")
            btn.setEnabled(bool(self._sessionid))
            layout.addWidget(btn)

    # ── 连接与播放控制 ────────────────────────────────────────────
    def _toggle(self):
        if self._connected:
            self._teardown()
            return
        avatar_id = self.avatar_combo.currentData()
        if not avatar_id:
            self.state.setText("先等待主服务就绪并选择形象")
            return
        self._ensure_bridge()
        self.connect_btn.setEnabled(False)
        self.state.setText("正在连接…")
        self._set_avatar(avatar_id)

    def _set_avatar(self, avatar_id):
        def work():
            try:
                local_session().put(BASE + "/api/settings",
                                    json={"avatar": {"id": avatar_id}}, timeout=8)
            except Exception:  # noqa: BLE001 - 设置失败时服务端沿用原形象
                pass
            self._bridge.open()

        threading.Thread(target=work, daemon=True).start()

    def _ensure_bridge(self):
        if self._bridge is not None:
            return
        from ui_qt.rtc import RtcBridge
        self._bridge = RtcBridge(BASE, parent=self)
        self._bridge.statusChanged.connect(self._on_status)
        self._bridge.sessionChanged.connect(self._on_session)

    def _on_status(self, text):
        self.statusChanged.emit(text, text == "已连接")

    def _on_session(self, sessionid):
        self._sessionid = sessionid or ""
        self._connected = bool(self._sessionid)
        if self._connected:
            self._frame_timer.start(33)
            self._retitle("断开连接", primary=False)
        else:
            self._frame_timer.stop()
            self._retitle("开始连接", primary=True)
            self._set_caption("")
        self._build_menu_buttons(self.menu_layout)
        if self._fullscreen is not None:
            self._fullscreen.sync_from_page()

    def _retitle(self, text, primary):
        self.connect_btn.setText(text)
        self.connect_btn.setEnabled(True)
        self.connect_btn.setProperty("primary", "true" if primary else "false")
        self.connect_btn.style().unpolish(self.connect_btn)
        self.connect_btn.style().polish(self.connect_btn)
        if primary:
            self.stage.clear()

    def _set_status(self, text, ok):
        self.state.setText(text or "未连接")
        if not ok:
            self.connect_btn.setEnabled(True)

    def _pull_frame(self):
        if self._bridge is None:
            return
        frame = self._bridge.take_frame()
        if frame is not None:
            self.stage.set_frame(frame, rgb=True)
            if self._fullscreen is not None:
                self._fullscreen.stage.set_frame(frame, rgb=True)

    def _teardown(self):
        sid = self._sessionid
        self._frame_timer.stop()
        self.state.setText("正在断开…")
        if self._bridge is not None:
            try:
                self._bridge.player.clear_pending()
            except Exception:  # noqa: BLE001
                pass
            self._bridge.close()
        self._connected = False
        self._sessionid = ""
        self._retitle("开始连接", primary=True)
        self._set_menu_tip("")
        if sid:
            threading.Thread(target=self._interrupt_request, args=(sid,), daemon=True).start()

    def _interrupt(self):
        if not self._sessionid:
            self.state.setText("请先连接数字人")
            return
        self.state.setText("正在打断朗读…")
        threading.Thread(target=self._interrupt_request, args=(self._sessionid,), daemon=True).start()

    def _interrupt_request(self, sessionid):
        try:
            response = local_session().post(BASE + "/interrupt_talk",
                                            json={"sessionid": sessionid}, timeout=5)
            data = response.json() or {}
            text = "已打断朗读" if data.get("code", -1) == 0 else (
                "打断失败：%s" % (data.get("msg") or "服务未确认"))
        except Exception as exc:  # noqa: BLE001
            text = "打断失败：%s" % exc
        self.statusChanged.emit(text, False)

    # ── 字幕、菜单与全屏 ──────────────────────────────────────────
    def _activate_menu(self, item):
        if not self._sessionid:
            self._set_menu_tip("请先连接数字人")
            return
        title = str(item.get("title") or "菜单")
        self._set_menu_tip("正在介绍：%s …" % title)

        def work():
            try:
                response = local_session().post(
                    BASE + "/api/kiosk/menus/%s/activate" % item["id"],
                    json={"sessionid": self._sessionid, "speak": True}, timeout=90)
                data = response.json() or {}
                if data.get("code", -1) != 0:
                    self.menuTipChanged.emit("介绍失败：%s" % (data.get("msg") or "未知错误"))
                    return
                answer = str((data.get("data") or {}).get("answer") or "")
                self.captionChanged.emit(answer or "已开始朗读")
                self.menuTipChanged.emit("正在朗读：%s" % title)
            except Exception as exc:  # noqa: BLE001
                self.menuTipChanged.emit("请求失败：%s" % exc)

        threading.Thread(target=work, daemon=True).start()

    def _set_caption(self, text):
        text = str(text or "").strip()
        self.subtitle.setText(text)
        self.subtitle.setVisible(bool(text))
        if self._fullscreen is not None:
            self._fullscreen.subtitle.setText(text)
            self._fullscreen.subtitle.setVisible(bool(text))

    def _set_menu_tip(self, text):
        self.menu_tip.setText(str(text or ""))
        if self._fullscreen is not None:
            self._fullscreen.tip.setText(str(text or ""))

    def _show_fullscreen(self):
        if self._fullscreen is not None:
            self._fullscreen.raise_()
            self._fullscreen.activateWindow()
            return
        self._fullscreen = FullscreenBroadcast(self)
        self._fullscreen.showFullScreen()

    def shutdown(self):
        self._menu_timer.stop()
        self._frame_timer.stop()
        if self._fullscreen is not None:
            self._fullscreen.close()
        if self._bridge is not None:
            self._bridge.stop()
            self._bridge = None


class IdlePage(BasePage):
    TITLE = "待机与迎宾"
    DESC = "没人时循环播放指定动作；摄像头检测到人，先起动作再念迎宾语。"

    def __init__(self, parent=None):
        super().__init__(parent)
        column = QVBoxLayout()
        column.setSpacing(12)

        idle = w.Card("闲时动作与待机提示",
                      "动作按形象分开制作，先选数字人再选它的动作。")
        self.idle_switch = w.Switch(True)
        idle.body.addWidget(w.SwitchRow("启用闲时与待机", self.idle_switch,
                                        "关掉后没人时一直停在实时画面"))

        avatar_row = QWidget()
        row = QHBoxLayout(avatar_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        avatar = QComboBox()
        avatar.addItems(["musetalk_ke"])
        gesture = QComboBox()
        gesture.addItems(["待机微摆", "单手打招呼", "不选动作"])
        row.addWidget(avatar)
        row.addWidget(gesture)
        row.addStretch(1)
        idle.body.addWidget(w.FormRow("闲时动作", avatar_row))

        self.tip_edit = w.line_edit("您可以站在我的前方进行语音交流", width=440)
        idle.body.addWidget(w.FormRow("待机提示", self.tip_edit))
        self.welcome_edit = w.line_edit("您好，欢迎来到知课数字人展区，请问有什么可以帮您？",
                                        width=440)
        idle.body.addWidget(w.FormRow("迎宾语", self.welcome_edit))

        self.after_spin = _seconds(30, 5, 600)
        spin_row = QWidget()
        spin_layout = QHBoxLayout(spin_row)
        spin_layout.setContentsMargins(0, 0, 0, 0)
        spin_layout.setSpacing(10)
        spin_layout.addWidget(self.after_spin)
        note = QLabel("无人无交互后切到闲时动作")
        note.setObjectName("muted")
        spin_layout.addWidget(note)
        spin_layout.addStretch(1)
        idle.body.addWidget(w.FormRow("进入闲时", spin_row))

        idle_row = QHBoxLayout()
        idle_row.addWidget(w.button("保存闲时设置", None, "primary"))
        idle_row.addStretch(1)
        idle.body.addLayout(idle_row)
        column.addWidget(idle)

        sensor = w.Card("人体接近检测（摄像头）",
                        "独立进程读摄像头，只把「有人 / 无人」报给主服务。")
        self.sensor_switch = w.Switch(False)
        sensor.body.addWidget(w.SwitchRow("启用人体接近检测", self.sensor_switch,
                                          "拨开即启动检测进程，拨关即停止"))

        self.cam_combo = QComboBox()
        self.cam_combo.addItems(["摄像头 0", "摄像头 1", "摄像头 2"])
        sensor.body.addWidget(w.FormRow("摄像头", self.cam_combo))

        self.mode_seg = w.Segmented([("motion", "帧差（快）"),
                                     ("hog", "行人检测（准）")], "motion")
        sensor.body.addWidget(w.FormRow("检测方式", self.mode_seg, label_width=88))

        leave_row = QWidget()
        leave_layout = QHBoxLayout(leave_row)
        leave_layout.setContentsMargins(0, 0, 0, 0)
        leave_layout.setSpacing(10)
        self.leave_spin = _seconds(8, 1, 120)
        leave_layout.addWidget(self.leave_spin)
        leave_note = QLabel("没动静这么久才算离开")
        leave_note.setObjectName("muted")
        leave_layout.addWidget(leave_note)
        leave_layout.addStretch(1)
        sensor.body.addWidget(w.FormRow("无人判定", leave_row))

        self.sens_slider = QSlider(Qt.Horizontal)
        self.sens_slider.setRange(5, 100)
        self.sens_slider.setValue(50)
        self.sens_slider.setFixedWidth(280)
        self.sens_value = QLabel("0.50 · 适中")
        self.sens_value.setObjectName("hint")
        self.sens_value.setFixedWidth(96)
        self.sens_slider.valueChanged.connect(
            lambda v: self.sens_value.setText("%.2f · %s" % (v / 100.0, _sens_word(v / 100.0))))
        sens_row = QWidget()
        sens_layout = QHBoxLayout(sens_row)
        sens_layout.setContentsMargins(0, 0, 0, 0)
        sens_layout.setSpacing(12)
        sens_layout.addWidget(self.sens_slider)
        sens_layout.addWidget(self.sens_value)
        sens_layout.addStretch(1)
        sensor.body.addWidget(w.FormRow("灵敏度", sens_row))

        self.trigger_combo = QComboBox()
        self.trigger_combo.addItems(["单手打招呼", "不触发动作"])
        sensor.body.addWidget(w.FormRow("触发动作", self.trigger_combo))

        bar = QHBoxLayout()
        bar.setSpacing(10)
        bar.addWidget(w.button("启动感应", None, "primary"))
        bar.addWidget(w.button("停止感应"))
        bar.addWidget(w.button("探测摄像头", None, "ghost"))
        bar.addStretch(1)
        sensor.body.addLayout(bar)
        column.addWidget(sensor)
        column.addStretch(1)

        holder = QWidget()
        holder.setLayout(column)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(holder)
        self.content.addWidget(scroll, 1)


def _seconds(value, low, high):
    """秒数输入框。

    用 QSpinBox 但不画上下按钮：Fusion 样式下这两个按钮按原生像素绘制，
    套上圆角边框后会溢出到框外（截图里那根竖条），而 QSS 的 border 三角
    在 Qt 里不生效、又没法跟随主题换色。去掉按钮后仍可滚轮和上下键调节。
    """
    box = QSpinBox()
    box.setRange(low, high)
    box.setValue(value)
    box.setSuffix(" 秒")
    box.setButtonSymbols(QSpinBox.NoButtons)
    box.setFixedWidth(96)
    box.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    return box


def _sens_word(value):
    if value < 0.25:
        return "迟钝"
    if value < 0.45:
        return "偏钝"
    if value < 0.7:
        return "适中"
    if value < 0.9:
        return "灵敏"
    return "很灵敏"


class AppearancePage(BasePage):
    TITLE = "外观"
    DESC = "换主题即时生效——这是 Qt 相对 Tk 最直接的一处改善（Tk 需要重启界面进程）。"

    def __init__(self, on_pick=None, parent=None):
        super().__init__(parent)
        self._on_pick = on_pick
        self._cards = {}
        column = QVBoxLayout()
        column.setSpacing(10)
        for theme_id, label, hint in ui_theme.theme_choices():
            column.addWidget(self._make_card(theme_id, label, hint))
        column.addStretch(1)
        holder = QWidget()
        holder.setLayout(column)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(holder)
        self.content.addWidget(scroll, 1)
        self.mark(ui_theme.current_theme_id())

    def _make_card(self, theme_id, label, hint):
        palette = ui_theme.palette(theme_id)
        card = w.Card(label, hint)
        swatches = QHBoxLayout()
        swatches.setSpacing(8)
        for key in ("bg", "card_alt", "accent", "ink", "muted"):
            chip = QLabel()
            chip.setFixedSize(30, 30)
            chip.setStyleSheet("background:%s;border-radius:8px;border:1px solid %s;"
                               % (palette[key], palette["line"]))
            swatches.addWidget(chip)
        swatches.addStretch(1)
        card.body.addLayout(swatches)
        pick = w.button("使用", lambda: self._pick(theme_id), "primary")
        card.body.addWidget(pick, 0, Qt.AlignLeft)
        card.setProperty("themeId", theme_id)
        self._cards[theme_id] = pick
        return card

    def _pick(self, theme_id):
        self.mark(theme_id)
        if self._on_pick:
            self._on_pick(theme_id)

    def mark(self, theme_id):
        for tid, btn in self._cards.items():
            btn.setText("当前使用" if tid == theme_id else "使用")
            btn.setEnabled(tid != theme_id)

# -*- coding: utf-8 -*-
"""人体接近检测：摄像头 → 有人/无人 → 通知主服务进入迎宾态。

独立进程，不在主服务里跑，原因有两个：
1. 摄像头读帧是死循环，放主服务里会和 torch 抢 GIL、抢内存；
2. 检测脚本崩了不该带崩数字人。

与主服务的唯一耦合是 ``POST /api/kiosk/proximity/event``（本机 8010）。
配置从 ``data/kiosk/proximity.json`` 直接读（和主服务同一个文件），
每轮重新载入 —— 界面上改了灵敏度、把开关关掉， detector 不用重启就生效：
开关一关它自己退出。

用法::

    python scripts/proximity_detect.py            # 常驻（受 enabled 开关控制）
    python scripts/proximity_detect.py --once     # 只跑 3 秒，打印结果后退出（自检用）
    python scripts/proximity_detect.py --debug    # 额外画出判定过程（需显示环境）
"""

import argparse
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils import kiosk_store as store  # noqa: E402

SERVICE = os.getenv("KIOSK_SERVICE", "http://127.0.0.1:8010").rstrip("/")
EVENT_URL = SERVICE + "/api/kiosk/proximity/event"
PREVIEW_PATH = os.path.join(store.KIOSK_DIR, "preview.jpg")

CONFIG_RELOAD_EVERY = 2.0   # 秒：多久重读一次配置
BEAT_EVERY = 4.0            # 秒：心跳上报间隔
PID_PATH = os.path.join(store.KIOSK_DIR, "proximity.pid")


def _write_pid():
    """写下 pid，界面「停止感应」时兜底用它杀进程。"""
    try:
        os.makedirs(store.KIOSK_DIR, exist_ok=True)
        with open(PID_PATH, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except Exception:  # noqa: BLE001
        pass


def _remove_pid():
    try:
        if os.path.isfile(PID_PATH):
            os.remove(PID_PATH)
    except Exception:  # noqa: BLE001
        pass


def _post(payload, timeout=5.0):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        EVENT_URL, data=data, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        # 显式绕过代理：机器上有 HTTP_PROXY 时，urllib 会把 127.0.0.1
        # 也发给代理，拿到 502，看起来像"主服务挂了"，其实只是绕远路了。
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception as exc:  # noqa: BLE001
        # 主服务没起来时不要把进程跑死，只是这次上报丢了
        print("[感应] 上报失败: %r" % (exc,), flush=True)
        return False


def _parse_roi(text, width, height):
    """把 "x,y,w,h" 解析成裁剪框；空值或非法值返回 None（=整幅画面）。"""
    if not text:
        return None
    try:
        parts = [int(float(p)) for p in str(text).split(",")]
    except (TypeError, ValueError):
        return None
    if len(parts) != 4:
        return None
    x, y, w, h = parts
    x = max(0, min(width - 1, x))
    y = max(0, min(height - 1, y))
    w = max(1, min(width - x, w))
    h = max(1, min(height - y, h))
    return (x, y, w, h)


class Detector:
    """两种检测方式：帧差（快）与 HOG 行人检测（准）。"""

    def __init__(self, cfg):
        import cv2
        self.cv2 = cv2
        self.cfg = cfg
        self.width = int(cfg.get("width") or 640)
        self.height = int(cfg.get("height") or 480)
        self.mode = cfg.get("mode") or "motion"
        self.cap = None
        self.bg = None
        self.hog = None
        self.note = ""          # 降级说明，随心跳报给界面
        self._open()

    def _open(self):
        cv2 = self.cv2
        index = int(self.cfg.get("camera_index") or 0)
        # 优先用 MSMF（Windows 上 DirectShow 对 5MP 摄像头有时会卡在首帧），
        # 失败再回落默认后端。
        for backend in (getattr(cv2, "CAP_MSMF", 0), getattr(cv2, "CAP_DSHOW", 0), 0):
            cap = cv2.VideoCapture(index, backend) if backend else cv2.VideoCapture(index)
            if cap is not None and cap.isOpened():
                ok, _frame = cap.read()
                if ok:
                    self.cap = cap
                    return True
                cap.release()
        self.cap = None
        return False

    def _setup(self):
        cv2 = self.cv2
        if self.mode == "hog":
            # HOG 行人检测器属于 objdetect，OpenCV 5 的部分构建里已经拿掉了
            # （本机 5.0.0 就没有 cv2.HOGDescriptor / CascadeClassifier）。
            # 拿不到就降级成帧差，并把降级原因报给界面，别让它静默变成"永远没人"。
            if not hasattr(cv2, "HOGDescriptor"):
                if self.mode != "motion":
                    print("[感应] 当前 OpenCV 没有 HOGDescriptor，自动改用帧差模式",
                          flush=True)
                self.mode = "motion"
                self.note = "HOG 不可用（OpenCV %s），已降级为帧差" % cv2.__version__
            elif self.hog is None:
                self.hog = cv2.HOGDescriptor()
                self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
                self.note = ""
        if self.mode != "hog" and self.bg is None:
            self.bg = cv2.createBackgroundSubtractorMOG2(
                history=300, varThreshold=36, detectShadows=True)

    def grab(self):
        """读一帧并缩到检测分辨率。返回 (frame, roi_frame)。"""
        if self.cap is None:
            return None, None
        ok, frame = self.cap.read()
        if not ok or frame is None:
            return None, None
        frame = self.cv2.resize(frame, (self.width, self.height))
        roi = _parse_roi(self.cfg.get("roi"), self.width, self.height)
        if roi:
            x, y, w, h = roi
            frame = frame[y:y + h, x:x + w]
        return frame, frame

    def score(self, frame):
        """返回 (是否有人, 置信度 0~1, 说明)。"""
        if frame is None:
            return False, 0.0, "无画面"
        self._setup()
        cv2 = self.cv2
        sens = float(self.cfg.get("sensitivity") or 0.5)

        if self.mode == "hog":
            found, weights = self.hog.detectMultiScale(
                frame, winStride=(8, 8), padding=(8, 8), scale=1.05)
            if len(found) == 0:
                return False, 0.0, "未检出人体"
            conf = float(max(float(w) for w in weights)) if len(weights) else 0.0
            # 灵敏度越高，判定阈值越低
            thresh = 0.6 - 0.4 * sens
            return conf >= thresh, min(1.0, conf), "HOG 置信度 %.2f" % conf

        fg = self.bg.apply(frame)
        _, mask = cv2.threshold(fg, 200, 255, cv2.THRESH_BINARY)
        mask = cv2.erode(mask, None, iterations=1)
        mask = cv2.dilate(mask, None, iterations=2)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        area = 0.0
        for c in contours:
            area = max(area, float(cv2.contourArea(c)))
        total = float(frame.shape[0] * frame.shape[1])
        ratio = area / total if total else 0.0
        base = float(self.cfg.get("min_area_ratio") or 0.02)
        thresh = base * (1.0 - 0.9 * max(0.05, min(1.0, sens)))
        return ratio >= thresh, min(1.0, ratio / max(base, 1e-6) / 4.0), \
            "运动占比 %.3f%%（阈值 %.3f%%）" % (ratio * 100, thresh * 100)

    def release(self):
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:  # noqa: BLE001
                pass
            self.cap = None


def _write_preview(frame, hit, note):
    """把当前画面写成预览图，桌面端直接读这个文件显示。

    比起再开一个视频流，这个做法在展项现场最省心：没有端口、没有编解码，
    文件不存在就是"没画面"。
    """
    if frame is None:
        return
    try:
        import cv2
        os.makedirs(store.KIOSK_DIR, exist_ok=True)
        img = frame.copy()
        color = (60, 200, 90) if hit else (90, 90, 220)
        cv2.putText(img, "%s | %s" % ("有人" if hit else "无人", note),
                    (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        cv2.imwrite(PREVIEW_PATH, img, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
    except Exception:  # noqa: BLE001
        pass


def run(once=False, debug=False):
    cfg = store.get_proximity()
    if not cfg.get("enabled") and not once:
        print("[感应] 开关未打开（enabled=false），退出", flush=True)
        return 0

    try:
        import cv2  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        print("[感应] 未安装 OpenCV，无法启动: %r" % (exc,), flush=True)
        _post({"present": False, "error": "未安装 OpenCV"})
        return 2

    det = Detector(cfg)
    if det.cap is None:
        print("[感应] 打不开摄像头 index=%s" % cfg.get("camera_index"), flush=True)
        _post({"present": False, "error": "打不开摄像头 %s" % cfg.get("camera_index")})
        return 3

    print("[感应] 已启动：摄像头 %s，模式 %s，%dx%d，%.0f fps"
          % (cfg.get("camera_index"), cfg.get("mode"), det.width, det.height,
             float(cfg.get("fps") or 5)), flush=True)
    _write_pid()

    hit_streak = 0
    last_hit_ts = 0.0
    present = False
    last_beat = 0.0
    cfg_ts = time.time()
    deadline = time.time() + 3.0 if once else None

    try:
        while True:
            now = time.time()
            if deadline is not None and now > deadline:
                break

            # 配置热重载：界面关掉开关后本轮结束就退出
            if now - cfg_ts > CONFIG_RELOAD_EVERY:
                cfg_ts = now
                fresh = store.get_proximity()
                if not once and not fresh.get("enabled"):
                    print("[感应] 开关已关闭，退出", flush=True)
                    break
                if fresh != cfg:
                    cfg = fresh
                    det.cfg = cfg
                    det.mode = cfg.get("mode") or "motion"
                    new_size = (int(cfg.get("width") or 640), int(cfg.get("height") or 480))
                    if new_size != (det.width, det.height):
                        det.width, det.height = new_size
                        det.bg = None
                    if int(cfg.get("camera_index") or 0) != int(
                            getattr(det, "_cam_index", cfg.get("camera_index") or 0)):
                        det.release()
                        det = Detector(cfg) if det.cap is None else det
                        if det.cap is None:
                            det.release()
                            det = Detector(cfg)
                        det._cam_index = int(cfg.get("camera_index") or 0)

            frame, roi = det.grab()
            hit, conf, note = det.score(roi)
            if hit:
                hit_streak += 1
                last_hit_ts = now
            else:
                hit_streak = 0

            need_enter = int(cfg.get("enter_frames") or 3)
            leave_s = float(cfg.get("leave_seconds") or 8)
            new_present = present
            if not present and hit_streak >= need_enter:
                new_present = True
            elif present and (now - last_hit_ts) > leave_s:
                new_present = False

            if new_present != present:
                present = new_present
                print("[感应] 状态翻转 → %s（%s）" % ("有人" if present else "无人", note),
                      flush=True)
                _post({"present": present, "confidence": round(conf, 3),
                       "note": det.note})
                last_beat = now
            elif now - last_beat > BEAT_EVERY:
                last_beat = now
                _post({"present": present, "confidence": round(conf, 3),
                       "note": det.note})

            if debug or cfg.get("preview", True):
                _write_preview(roi, hit or present, note)

            if debug:
                import cv2
                cv2.imshow("proximity", roi if roi is not None else frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

            if once:
                print("[感应] 自检：hit=%s conf=%.2f %s" % (hit, conf, note), flush=True)
            time.sleep(1.0 / max(1.0, float(cfg.get("fps") or 5)))
    except KeyboardInterrupt:
        print("[感应] 已停止", flush=True)
    finally:
        det.release()
        _remove_pid()
        if not once:
            _post({"present": False, "beat": True})
    return 0


def main():
    parser = argparse.ArgumentParser(description="人体接近检测（全息舱迎宾）")
    parser.add_argument("--once", action="store_true", help="只跑 3 秒自检后退出")
    parser.add_argument("--debug", action="store_true", help="弹出画面查看判定过程")
    args = parser.parse_args()
    return run(once=args.once, debug=args.debug)


if __name__ == "__main__":
    sys.exit(main())

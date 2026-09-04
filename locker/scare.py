"""突脸惩罚：拦截到非赦免应用时，全屏弹出你的头像并播放 no 音频。

素材约定（放在项目根目录）：
  chra/   —— 人物图片（jpg/png 等，多张则随机选）
  sound/  —— 拦截音效（mp3/wav，多个则随机选）
没有素材时静默跳过，不影响锁定功能本身。
"""
from __future__ import annotations

import ctypes
import os
import random
import time
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QPixmap
from PySide6.QtWidgets import QLabel, QWidget

from locker.config import app_dir

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a"}

_mci = ctypes.windll.winmm.mciSendStringW
_mci.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
_mci.restype = ctypes.c_uint
_MCI_ALIAS = "slscare"


def _find_files(folder_name: str, exts: set) -> list:
    folder = app_dir() / folder_name   # 素材在 exe/项目目录旁的 chra/、sound/
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in exts)


def scare_images() -> list:
    return _find_files("chra", IMAGE_EXTS)


def scare_sounds() -> list:
    return _find_files("sound", AUDIO_EXTS)


def play_no(path) -> bool:
    """用 Windows MCI 播放音效（先关掉上一次的，避免叠音）。"""
    _mci(f"close {_MCI_ALIAS}", None, 0, None)
    quoted = f'"{path}"'
    opened = False
    for typ in ("mpegvideo", "waveaudio", ""):   # mp3 走 mpegvideo，wav 走 waveaudio
        suffix = f" type {typ}" if typ else ""
        if _mci(f"open {quoted} alias {_MCI_ALIAS}{suffix}", None, 0, None) == 0:
            opened = True
            break
    if not opened:
        return False
    return _mci(f"play {_MCI_ALIAS}", None, 0, None) == 0


def stop_no() -> None:
    _mci(f"close {_MCI_ALIAS}", None, 0, None)


class ScareWindow(QWidget):
    """全屏人脸：扑脸缩放冲入 → 停留 → 淡出。点击任意处立即关闭。"""

    closed = Signal()   # 真正关闭（无论自动淡出还是点击）时发出，供控制器复位

    ZOOM_MS = 260       # 从半屏大小扑向全屏的时长
    HOLD_MS = 800       # 全屏停留时长
    FADE_MS = 260       # 淡出时长

    def __init__(self, pixmap: QPixmap, screen=None, parent=None):
        super().__init__(parent,
                         Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint)
        if screen is None:
            screen = (QGuiApplication.screenAt(QCursor.pos())
                      or QGuiApplication.primaryScreen())
        geo = screen.geometry()
        self.setGeometry(geo)
        w, h = geo.width(), geo.height()

        # 封面式裁剪：图片铺满屏幕但不拉伸变形
        scaled = pixmap.scaled(w, h,
                               Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                               Qt.TransformationMode.SmoothTransformation)
        crop = scaled.copy((scaled.width() - w) // 2, (scaled.height() - h) // 2, w, h)

        self._label = QLabel(self)
        self._label.setPixmap(crop)
        self._label.setScaledContents(True)

        # 突脸动画：从屏幕中央一小块瞬间放大扑满 + 快速淡入
        cw, ch = int(w * 0.45), int(h * 0.45)
        start_rect = QRect((w - cw) // 2, (h - ch) // 2, cw, ch)
        self._zoom = QPropertyAnimation(self._label, b"geometry", self)
        self._zoom.setStartValue(start_rect)
        self._zoom.setEndValue(QRect(0, 0, w, h))
        self._zoom.setDuration(self.ZOOM_MS)
        self._zoom.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._fade_in = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setDuration(90)

        self._fade_out = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_out.setStartValue(1.0)
        self._fade_out.setEndValue(0.0)
        self._fade_out.setDuration(self.FADE_MS)
        self._fade_out.finished.connect(self.close)

    def start(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self._zoom.start()
        self._fade_in.start()
        QTimer.singleShot(self.ZOOM_MS + self.HOLD_MS, self._fade_out.start)

    def mousePressEvent(self, event):
        self.close()

    def closeEvent(self, event):
        stop_no()
        self.closed.emit()
        super().closeEvent(event)


class Scare:
    """突脸调度：节流 + 同一时间只弹一个窗口。"""

    MIN_GAP = 1.3   # 两次突脸最小间隔（秒），防止狂点刷屏

    def __init__(self):
        self._last_ts = 0.0
        self._window = None
        self._images = None
        self._sounds = None

    def ready(self) -> bool:
        if self._images is None:
            self._images = scare_images()
            self._sounds = scare_sounds()
        return bool(self._images and self._sounds)

    def maybe_trigger(self) -> bool:
        """有素材且距上次足够久且没有未关闭的窗口时，来一发。"""
        now = time.time()
        if not self.ready():
            return False
        if now - self._last_ts < self.MIN_GAP:
            return False
        if self._window is not None:
            if self._window.isVisible():
                return False
            self._window = None   # 窗口已关闭但引用未清：直接接管
        self._last_ts = now
        image = QPixmap(str(random.choice(self._images)))
        if image.isNull():
            return False
        play_no(str(random.choice(self._sounds)))
        win = ScareWindow(image)
        win.closed.connect(lambda: setattr(self, "_window", None))
        self._window = win
        win.start()
        return True


SCARE = Scare()

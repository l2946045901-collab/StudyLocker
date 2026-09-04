"""StudyLocker 配置界面（PySide6）。

职责：设置倒计时与赦免名单、以 UAC 提权拉起引擎、展示剩余时间/拦截统计。
界面进程与引擎进程相互独立：关掉本窗口不影响锁定；引擎意外退出则自动解锁
（fail-open），并在重启后恢复时看到“上次会话中断”的提示。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import psutil

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSystemTrayIcon,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from locker import config as C
from locker.scare import SCARE
from locker.theme import (COLOR_ACTIVE, COLOR_ERROR, COLOR_HERO, COLOR_IDLE,
                          COLOR_WARN, apply_theme, make_app_icon)

# 视为“用户主动打开程序”的父进程（双击桌面图标/任务栏/开始菜单都经资源管理器
# 创建进程）。其余父进程产生的拦截（后台自启、应用自拉子进程）只静默拦截。
SHELL_PARENTS = {"explorer.exe", "startmenuexperiencehost.exe",
                 "searchhost.exe", "shellhost.exe"}

# 与引擎一致的“永远放行”系统目录（前台窗口判定用）
_SYSTEM_PREFIXES = (
    r"c:\windows",
    r"c:\program files\windowsapps",
    r"c:\program files\windows defender",
    r"c:\programdata\microsoft\windows defender",
    r"c:\program files\microsoft security client",
)

TYPE_LABELS = {
    "block_new": "拦截新启动",
    "close_existing": "会话开始清理",
    "kill_denied": "拒绝终止(受保护进程)",
    "session_finished": "会话自然结束",
    "unlocked_by_user": "用户提前解锁",
    "engine_died": "引擎异常退出(已解锁)",
    "engine_failed": "引擎启动失败",
    "session_abandoned": "会话因重启/关机中断",
    "hero_start": "勇士之路开启",
    "hero_finished": "勇士之路到期释放",
    "hero_ui_request": "勇士开启请求",
    "hero_engine_failed": "勇士引擎启动失败",
    "hero_autostart_task": "注册登录自启(任务)",
    "hero_autostart_runkey": "注册登录自启(Run键)",
    "hero_autostart_removed": "清理登录自启",
    "hero_guardian_started": "守护进程已拉起",
    "hero_guardian_failed": "守护进程启动失败",
    "hero_expired_at_boot": "开机时勇士已到期(清理)",
    "hero_noop": "勇士引擎空转(无状态)",
    "hero_exit": "勇士引擎退出",
}


def fmt_remain(secs: float) -> str:
    secs = max(0, int(secs))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class StatsBars(QWidget):
    """自绘分类统计条：每类一行（名称 + 按最大值比例的圆角条 + 计数）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[tuple[str, int, str]] = []   # (标签, 次数, 颜色)
        self.setMinimumHeight(8)

    def set_data(self, items: list[tuple[str, int, str]]):
        self._items = items
        self.setMinimumHeight(max(8, len(items) * 30 + 6))
        self.update()

    def paintEvent(self, event):
        from PySide6.QtGui import QColor, QPainter
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        items = [it for it in self._items if it[1] > 0]
        if not items:
            return
        w = self.width()
        row_h = 26
        bar_x = 86
        max_n = max(n for _, n, _ in items)
        for i, (label, n, color) in enumerate(items):
            y = i * row_h
            p.setPen(QColor("#9AA4B5"))
            p.drawText(0, y, bar_x - 8, row_h,
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)
            p.setPen(QColor("#5C6775"))
            p.drawText(w - 52, y, 52, row_h,
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, str(n))
            bar_w = w - bar_x - 60
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#1D242E"))
            p.drawRoundedRect(bar_x, y + 7, int(bar_w), 12, 6, 6)
            fill = int(bar_w * n / max_n) if max_n else 0
            if fill >= 3:
                p.setBrush(QColor(color))
                p.drawRoundedRect(bar_x, y + 7, fill, 12, 6, 6)
        p.end()


class RunningAppsDialog(QDialog):
    """从当前运行的程序里挑选赦免应用（添加其可执行文件）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择正在运行的程序")
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("输入关键字过滤…")
        lay.addWidget(self.filter)
        self.listw = QListWidget(self)
        self.listw.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.listw.itemDoubleClicked.connect(self.accept)   # 双击单项 = 立即添加
        lay.addWidget(self.listw)
        hint = QLabel("按住 Ctrl / Shift 点选可一次添加多个程序；双击单项立即添加")
        hint.setStyleSheet("color:#5C6775; font-size:12px;")
        lay.addWidget(hint)
        btns = QHBoxLayout()
        ok = QPushButton("添加选中程序", self)
        ok.setObjectName("accentBtn")
        cancel = QPushButton("取消", self)
        cancel.setObjectName("ghostBtn")
        btns.addStretch(1)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        lay.addLayout(btns)
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        self.filter.textChanged.connect(self._filter)
        self._reload()
        self._filter("")

    def _reload(self):
        seen = {}
        for proc in psutil.process_iter():
            try:
                exe = proc.exe()
                name = proc.name()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            if exe:
                seen.setdefault(os.path.normpath(exe), (name, exe))
        self._items = sorted(seen.values(), key=lambda x: x[0].casefold())
        self._fill(self._items)

    def _fill(self, items):
        keep = {self.listw.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.listw.count())
                if self.listw.item(i).isSelected()}   # 过滤/刷新时保留已勾选
        self.listw.clear()
        for name, exe in items:
            it = QListWidgetItem(f"{name}\n{exe}")
            it.setData(Qt.ItemDataRole.UserRole, exe)
            it.setToolTip(exe)
            self.listw.addItem(it)
            if exe in keep:                           # 须先加入列表再标记选中
                it.setSelected(True)

    def _filter(self, text: str):
        t = text.strip().casefold()
        self._fill([x for x in self._items if t in x[0].casefold() or t in x[1].casefold()])

    def selected_paths(self) -> list:
        return [self.listw.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.listw.count()) if self.listw.item(i).isSelected()]


class HeroDialog(QDialog):
    """勇士之路开启对话框：时长选择 + 逐字输入确认语（含时长），防误开。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("开启勇士之路")
        self.resize(540, 460)
        lay = QVBoxLayout(self)
        intro = QLabel(
            "勇士之路开启后：\n"
            "· 倒计时结束前【无法以任何方式提前解除】，界面无解锁按钮；\n"
            "· 重启/关机电脑也会在登录后【自动续锁】；\n"
            "· 结束引擎进程会被守护进程在几秒内自动拉起；\n"
            "· 赦免名单在开启瞬间冻结，中途修改无效；\n"
            "· 到期自动释放并清理全部自启动项，不留后患；\n"
            "· 【最终保险】无论设定多长，最迟次日早上 6:00 自动解除。\n\n"
            "说明：系统级手段（安全模式启动、管理员命令行、删除程序文件、\n"
            "系统还原等）理论上仍可绕过——本模式对抗的是冲动，不是系统管理员。"
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#5d4037;")
        lay.addWidget(intro)

        row = QHBoxLayout()
        row.addStretch(1)
        for m in (1, 60, 120, 240, 480, 720):
            b = QPushButton(f"{m // 60} 小时" if m % 60 == 0 else f"{m} 分钟")
            b.setProperty("chip", True)
            b.clicked.connect(lambda _=False, mm=m: self.spin.setValue(mm))
            row.addWidget(b)
        self.spin = QSpinBox()
        self.spin.setRange(1, 4320)      # 1 分钟起，最多 72 小时
        self.spin.setValue(240)
        self.spin.setSuffix(" 分钟")
        row.addWidget(self.spin)
        row.addStretch(1)
        lay.addLayout(row)
        self.spin.valueChanged.connect(self._sync_target)

        self.lbl_dur = QLabel("")
        self.lbl_dur.setWordWrap(True)
        self.lbl_dur.setStyleSheet("color:#1b5e20;font-weight:bold;")
        lay.addWidget(self.lbl_dur)

        self.lbl_target = QLabel("")
        self.lbl_target.setWordWrap(True)
        self.lbl_target.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = self.lbl_target.font()
        font.setPointSize(11)
        font.setBold(True)
        self.lbl_target.setFont(font)
        lay.addWidget(self.lbl_target)

        self.confirm_edit = QLineEdit()
        self.confirm_edit.setPlaceholderText("请在上面逐字输入确认语…")
        lay.addWidget(self.confirm_edit)
        self.lbl_hint = QLabel("输入与上方确认语完全一致（含时长数字）后，按钮才会亮起")
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet("color:#b71c1c;")
        lay.addWidget(self.lbl_hint)
        self.confirm_edit.textChanged.connect(self._check_confirm)

        btns = QHBoxLayout()
        self.ok = QPushButton("开启勇士之路")
        self.ok.setObjectName("accentBtn")
        self.ok.setMinimumHeight(38)
        cancel = QPushButton("我再想想")
        cancel.setObjectName("ghostBtn")
        btns.addStretch(1)
        btns.addWidget(cancel)
        btns.addWidget(self.ok)
        lay.addLayout(btns)
        self.ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        self.ok.setEnabled(False)
        self._sync_target()

    def confirm_text(self) -> str:
        return f"我确认开启勇士之路 {self.spin.value()} 分钟"

    def _sync_target(self, *_):
        from locker.config import hero_fuse_seconds
        minutes = self.spin.value()
        h, m = divmod(minutes, 60)
        dur_txt = f"{h} 小时" + (f" {m} 分钟" if m else "")
        # 保险丝截断提示（保险丝 = 到次日 06:00 的剩余秒数）
        fuse = hero_fuse_seconds()
        if minutes * 60 > fuse:
            extra = f"\n注：设定超过次日 06:00，将按最终保险在次日 06:00 解除（实际约 {max(1, fuse // 60)} 分钟）"
        else:
            extra = ""
        self.lbl_dur.setText(f"即将锁定：{dur_txt}（{minutes} 分钟），期间无法以任何方式提前解除。{extra}")
        self.lbl_target.setText("请逐字输入以下确认语：\n" + self.confirm_text())
        self._check_confirm()

    def _check_confirm(self, *_):
        text = self.confirm_edit.text().strip()
        target = self.confirm_text()
        match = text == target
        self.ok.setEnabled(match)
        if match:
            self.confirm_edit.setStyleSheet("border:1px solid #34D399;border-radius:8px;")
        elif text:
            self.confirm_edit.setStyleSheet("border:1px solid #F87171;border-radius:8px;")
            self.lbl_hint.setText(f"输入不一致：还差 {len(target) - len(text)} 个字符（共 {len(target)} 字），"
                                  "请照上方逐字输入（含时长数字）")
            return
        else:
            self.confirm_edit.setStyleSheet("")
        self.lbl_hint.setText("输入与上方确认语完全一致（含时长数字）后，按钮才会亮起")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("StudyLocker 学习应用锁")
        self.resize(600, 700)

        self._session = C.load_session()
        self._prev_active = bool(self._session.get("active"))
        self._start_pending_ts: float | None = None
        self._stop_at_ts: float | None = None     # 提前结束确认后的倒计时截止时刻
        self._last_stats_ts = 0.0
        self._stats_dirty = True
        self._last_block_ts = time.time()         # 突脸游标：只对之后的新拦截生效
        self._last_fg_pid = 0                     # 前台突脸游标：只对“新切换到的”窗口生效
        self._self_exe_lower = os.path.normpath(sys.executable).casefold()
        self._hero = C.load_hero()
        self._hero_active = bool(self._hero.get("active"))
        self._prev_hero = self._hero_active
        self._hero_pending_ts: float | None = None
        self._allowlist_btns: list = []

        central = QWidget(self)
        self.setCentralWidget(central)
        root_lay = QVBoxLayout(central)
        root_lay.setContentsMargins(18, 10, 18, 10)
        root_lay.setSpacing(10)

        # 状态卡：色点徽章 + 状态文字（替代整条横幅，状态色即语义）
        state_card = QFrame(self)
        state_card.setObjectName("card")
        sc_lay = QHBoxLayout(state_card)
        sc_lay.setContentsMargins(14, 8, 14, 8)
        sc_lay.setSpacing(10)
        self.badge = QLabel()
        self.badge.setFixedSize(10, 10)
        sc_lay.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        self.banner = QLabel("未在学习")
        self.banner.setWordWrap(True)
        sc_lay.addWidget(self.banner, 1)
        root_lay.addWidget(state_card)

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._build_study_tab(), "学习")
        self.tabs.addTab(self._build_exempt_tab(), "赦免应用")
        self.tabs.addTab(self._build_stats_tab(), "拦截统计")
        root_lay.addWidget(self.tabs, 1)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self._build_tray()
        self._build_icon_btn = None

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._tick()

    # ---------- 界面搭建 ----------

    def _build_study_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(2, 4, 2, 0)
        lay.setSpacing(10)

        # —— 倒计时卡 ——
        remain_card = QFrame(w)
        remain_card.setObjectName("card")
        rl = QVBoxLayout(remain_card)
        rl.setContentsMargins(16, 16, 16, 12)
        rl.setSpacing(0)
        self.lbl_remain = QLabel("--:--")
        self.lbl_remain.setObjectName("remainBig")
        self.lbl_remain.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rl.addWidget(self.lbl_remain)
        self.lbl_status = QLabel("未在学习")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setStyleSheet("color:#9AA4B5; font-size:12px;")
        rl.addWidget(self.lbl_status)
        lay.addWidget(remain_card)

        # —— 控制卡 ——
        ctl_card = QFrame(w)
        ctl_card.setObjectName("card")
        cl = QVBoxLayout(ctl_card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(10)

        chip_row = QHBoxLayout()
        chip_row.addStretch(1)
        self._chips = []
        for m in (25, 45, 60, 90):
            b = QPushButton(f"{m} 分钟")
            b.setProperty("chip", True)
            b.setCheckable(True)
            b._m = m
            b.clicked.connect(lambda _=False, mm=m: self.spin_min.setValue(mm))
            chip_row.addWidget(b)
            self._chips.append(b)
        self.spin_min = QSpinBox()
        self.spin_min.setRange(1, 600)
        self.spin_min.setValue(45)
        self.spin_min.setSuffix(" 分钟")
        chip_row.addWidget(self.spin_min)
        chip_row.addStretch(1)
        cl.addLayout(chip_row)
        self.spin_min.valueChanged.connect(self._sync_chips)

        self.chk_close = QCheckBox("开始学习时，立即关闭其他正在运行的非赦免应用")
        cl.addWidget(self.chk_close)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_start = QPushButton("开始学习")
        self.btn_start.setObjectName("accentBtn")
        self.btn_start.setMinimumHeight(44)
        self.btn_stop = QPushButton("提前结束学习")
        self.btn_stop.setObjectName("dangerGhost")
        self.btn_stop.setEnabled(False)
        self.btn_cancel_stop = QPushButton("取消解锁")
        self.btn_cancel_stop.setObjectName("ghostBtn")
        self.btn_cancel_stop.setVisible(False)
        btn_row.addWidget(self.btn_start, 1)
        btn_row.addWidget(self.btn_stop, 0)
        btn_row.addWidget(self.btn_cancel_stop, 0)
        cl.addLayout(btn_row)

        hsep = QFrame(w)
        hsep.setObjectName("hsep")
        hsep.setFixedHeight(1)
        cl.addWidget(hsep)

        self.btn_hero = QPushButton("勇士之路")
        self.btn_hero.setObjectName("ghostBtn")
        self.btn_hero.setToolTip(
            "自制的终极模式：开启后无法提前解除，重启电脑也会自动续锁，\n"
            "只有倒计时结束才会释放。适合认为自己自制力完全不够时使用。")
        self.btn_hero.clicked.connect(self._ask_hero)
        hrow = QHBoxLayout()
        hrow.addStretch(1)
        hrow.addWidget(self.btn_hero)
        hrow.addStretch(1)
        cl.addLayout(hrow)

        self.lbl_hero_state = QLabel("")
        self.lbl_hero_state.setWordWrap(True)
        self.lbl_hero_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_hero_state.setStyleSheet("color:#9AA4B5; font-size:12px;")
        cl.addWidget(self.lbl_hero_state)
        lay.addWidget(ctl_card)

        hint = QLabel(
            "紧急解锁通道：重启电脑（关机后锁定随进程一起消失，开机即恢复自由）。\n"
            "若界面进程或引擎意外退出，锁定同样会自动解除（fail-open），不会把你锁死。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#5C6775; font-size:11px;")
        lay.addWidget(hint)
        lay.addStretch(1)
        self.btn_start.clicked.connect(self._start_session)
        self.btn_stop.clicked.connect(self._confirm_stop)
        self.btn_cancel_stop.clicked.connect(self._cancel_stop)
        self._sync_chips()
        return w

    def _sync_chips(self):
        """时长胶囊单选联动：自定义数值命中预设时点亮对应胶囊。"""
        v = self.spin_min.value()
        for b in self._chips:
            b.setChecked(b._m == v)

    def _build_exempt_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(2, 4, 2, 0)
        lay.setSpacing(10)

        card = QFrame(w)
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(8)
        top = QHBoxLayout()
        title = QLabel("赦免名单")
        title.setStyleSheet("font-weight:600;")
        top.addWidget(title)
        top.addStretch(1)
        self.lbl_exempt_count = QLabel("0 个")
        self.lbl_exempt_count.setStyleSheet("color:#5C6775; font-size:12px;")
        top.addWidget(self.lbl_exempt_count)
        cl.addLayout(top)
        self.exempt_search = QLineEdit()
        self.exempt_search.setPlaceholderText("搜索名称或路径…")
        self.exempt_search.textChanged.connect(self._reload_exempt_list)
        cl.addWidget(self.exempt_search)
        self.lst_exempt = QListWidget(w)
        self.lst_exempt.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        cl.addWidget(self.lst_exempt, 1)
        row = QHBoxLayout()
        row.setSpacing(8)
        for i, (text, handler) in enumerate((
            ("从运行中的程序添加…", self._add_from_running),
            ("添加程序文件…", self._add_exe_file),
            ("添加文件夹…", self._add_folder),
            ("移除选中", self._remove_selected),
        )):
            b = QPushButton(text)
            if i == 0:
                b.setObjectName("accentBtn")
                b.setMinimumHeight(36)
            else:
                b.setObjectName("ghostBtn")
            b.clicked.connect(handler)
            row.addWidget(b)
            self._allowlist_btns.append(b)
        cl.addLayout(row)
        lay.addWidget(card)

        tip = QLabel(
            "添加「运行中的程序/程序文件」精确到该 exe（其子进程一并放行）；\n"
            "「整个文件夹」= 目录下所有程序放行。系统目录永远放行；\n"
            "第三方输入法建议把所在文件夹也加入赦免。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#5C6775; font-size:11px;")
        lay.addWidget(tip)
        self._reload_exempt_list()
        return w

    def _build_stats_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(2, 4, 2, 0)
        lay.setSpacing(10)

        card = QFrame(w)
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(6)
        self.lbl_stats_head = QLabel(" ")
        self.lbl_stats_head.setWordWrap(True)
        self.lbl_stats_head.setStyleSheet("font-weight:600;")
        cl.addWidget(self.lbl_stats_head)
        self.stats_bars = StatsBars(card)
        cl.addWidget(self.stats_bars)
        lay.addWidget(card)

        self.txt_stats = QTextEdit(w)
        self.txt_stats.setReadOnly(True)
        lay.addWidget(self.txt_stats, 1)
        row = QHBoxLayout()
        b_refresh = QPushButton("刷新")
        b_clear = QPushButton("清空日志")
        for b in (b_refresh, b_clear):
            b.setObjectName("ghostBtn")
        row.addStretch(1)
        row.addWidget(b_refresh)
        row.addWidget(b_clear)
        lay.addLayout(row)
        b_refresh.clicked.connect(self._refresh_stats)
        b_clear.clicked.connect(self._clear_logs)
        return w

    def _build_tray(self):
        self._tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray = QSystemTrayIcon(make_app_icon("idle"), self)
            self._tray.setToolTip("StudyLocker 学习应用锁")
            menu = QMenu()
            act_show = QAction("显示主窗口", self)
            act_show.triggered.connect(self._show_window)
            act_quit = QAction("退出程序", self)
            act_quit.triggered.connect(self._quit_app)
            menu.addAction(act_show)
            menu.addAction(act_quit)
            self._tray.setContextMenu(menu)
            self._tray.activated.connect(
                lambda r: self._show_window() if r == QSystemTrayIcon.ActivationReason.Trigger else None
            )
            self._tray.show()

    def _update_tray_icon(self):
        """托盘图标随状态变色：空闲蓝 / 学习中绿 / 勇士琥珀。"""
        if self._tray is None:
            return
        if self._hero_active:
            state = "hero"
        elif self._session.get("active"):
            state = "active"
        else:
            state = "idle"
        self._tray.setIcon(make_app_icon(state))

    def _show_window(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def _quit_app(self):
        if self._session.get("active"):
            QMessageBox.information(
                self, "仍在学习中",
                "锁定仍在运行，退出界面不影响引擎。\n可稍后重新打开本程序查看剩余时间或提前结束。",
            )
        self._really_quit = True
        QApplication.instance().quit()

    # ---------- 赦免名单 ----------

    def _reload_exempt_list(self, *_):
        kw = self.exempt_search.text().strip().casefold() if hasattr(self, "exempt_search") else ""
        self.lst_exempt.clear()
        total = 0
        for rec in C.load_allowlist():
            p = rec["path"]
            is_dir = rec["dir"] or os.path.isdir(p)
            kind = "文件夹" if is_dir else "程序"
            name = os.path.basename(p.rstrip("/\\")) or p
            if kw and kw not in name.casefold() and kw not in p.casefold():
                continue
            it = QListWidgetItem(f"{name}\n{kind} · {p}")
            it.setData(Qt.ItemDataRole.UserRole, p)
            it.setToolTip(p)
            self.lst_exempt.addItem(it)
            total += 1
        if hasattr(self, "lbl_exempt_count"):
            all_n = len(C.load_allowlist())
            self.lbl_exempt_count.setText(f"{all_n} 个" if not kw else f"{total} / {all_n} 个")

    def _add_paths(self, paths: list):
        if self._hero_active:
            QMessageBox.information(self, "勇士之路中", "勇士之路期间赦免名单已冻结，无法修改。")
            return
        if not paths:
            return
        bad = [p for p in paths if not os.path.exists(p)]
        if bad:
            QMessageBox.warning(self, "路径不存在", "以下路径无效，未添加：\n" + "\n".join(bad))
        good = [{"path": os.path.normpath(p), "dir": os.path.isdir(p)}
                for p in paths if os.path.exists(p)]
        if good:
            C.save_allowlist(C.load_allowlist() + good)
            self._reload_exempt_list()
            self._push_live_exempted()

    def _add_from_running(self):
        dlg = RunningAppsDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._add_paths(dlg.selected_paths())

    def _add_exe_file(self):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "选择程序", "C:\\", "程序 (*.exe)")
        if path:
            self._add_paths([path])

    def _add_folder(self):
        from PySide6.QtWidgets import QFileDialog
        path = QFileDialog.getExistingDirectory(self, "选择文件夹", "C:\\")
        if path:
            self._add_paths([path])

    def _remove_selected(self):
        if self._hero_active:
            QMessageBox.information(self, "勇士之路中", "勇士之路期间赦免名单已冻结，无法修改。")
            return
        selected = {self.lst_exempt.item(i).data(Qt.ItemDataRole.UserRole)
                    for i in range(self.lst_exempt.count())
                    if self.lst_exempt.item(i).isSelected()}
        if not selected:
            return
        keep = [rec for rec in C.load_allowlist()
                if os.path.normpath(rec["path"]).casefold() not in
                {os.path.normpath(s).casefold() for s in selected}]
        C.save_allowlist(keep)
        self._reload_exempt_list()
        self._push_live_exempted()

    def _push_live_exempted(self):
        """学习中修改名单时把最新名单同步进 session（引擎约 1 秒内生效）。"""
        if not self._session.get("active"):
            return
        self._session["exempted"] = C.load_allowlist()
        C.save_session(self._session)

    # ---------- 会话操作 ----------

    def _start_session(self):
        if self._hero_active:
            return
        if self._session.get("active"):
            return
        mins = self.spin_min.value()
        exempted = C.load_allowlist()
        if not exempted:
            ret = QMessageBox.question(
                self, "没有赦免应用",
                "当前赦免名单为空：开始后除系统程序外的一切都会被拦截。\n确定继续？",
            )
            if ret != QMessageBox.StandardButton.Yes:
                return
        s = {
            "active": True,
            "started_at": time.time(),
            "end_at": time.time() + mins * 60,
            "force_stop_at": None,
            "duration_min": mins,
            "close_existing": self.chk_close.isChecked(),
            "exempted": exempted,
            "engine_pid": None,
        }
        C.save_session(s)
        self._session = s
        self._start_pending_ts = time.time()
        C.log_event("ui_start_request", duration_min=mins)
        ok = self._spawn_engine()
        if not ok:
            self._session = C.load_session()
            self._start_pending_ts = None
            QMessageBox.critical(
                self, "启动失败",
                "无法以管理员权限启动引擎（UAC 被取消？）。会话已取消。",
            )

    def _spawn_engine(self) -> bool:
        return self._runas(C.child_launch("engine"), f"--ui-pid {os.getpid()}")

    def _runas(self, cmd: list, extra_args: str) -> bool:
        """以管理员权限拉起配套程序（cmd 首项为可执行文件，其余为参数）。"""
        try:
            import ctypes
            params = " ".join(f'"{p}"' for p in cmd[1:])
            if extra_args:
                params = (params + " " if params else "") + extra_args
            code = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", str(cmd[0]), params, "", 0,  # SW_HIDE
            )
            return int(code) > 32
        except Exception as e:
            C.log_event("engine_failed", reason=str(e))
            return False

    def _confirm_stop(self):
        ret = QMessageBox.question(
            self, "提前结束学习？",
            "确定要提前结束本次学习吗？\n10 秒倒计时后才会真正解锁（可在此期间取消）。",
        )
        if ret == QMessageBox.StandardButton.Yes:
            self._stop_at_ts = time.time() + 10

    def _cancel_stop(self):
        self._stop_at_ts = None

    # ---------- 勇士之路 ----------

    def _ask_hero(self):
        """勇士之路入口：进行中且引擎不在时点击 = 重试拉起引擎。"""
        if self._hero_active:
            if not self._spawn_hero_engine():
                QMessageBox.critical(self, "启动失败",
                                     "无法以管理员权限启动勇士引擎（UAC 被取消？）。\n"
                                     "可稍后重试；重启电脑登录后也会自动拉起。")
            self._hero_pending_ts = time.time()
            return
        if self._session.get("active"):
            QMessageBox.information(
                self, "先结束当前学习",
                "请先结束当前的学习会话（或等它自然结束），再开启勇士之路。")
            return
        dlg = HeroDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        minutes = dlg.spin.value()
        hero = {
            "active": True,
            "started_at": time.time(),
            "deadline": time.time() + minutes * 60,
            "duration_min": minutes,
            "cap_at": C.hero_cap_at(),      # 最终保险：次日早 6 点必解
            "exempted": C.load_allowlist(),  # 冻结开启瞬间的赦免名单
        }
        C.save_hero(hero)
        self._hero_pending_ts = time.time()
        C.log_event("hero_ui_request", duration_min=minutes)
        if not self._spawn_hero_engine():
            hero["active"] = False
            C.save_hero(hero)
            self._hero_pending_ts = None
            QMessageBox.critical(self, "启动失败",
                                 "无法以管理员权限启动勇士引擎（UAC 被取消？）。\n本次开启已取消，勇士状态未生效。")

    def _spawn_hero_engine(self) -> bool:
        return self._runas(C.child_launch("engine"), f"--hero --ui-pid {os.getpid()}")

    def _set_allowlist_enabled(self, enabled: bool):
        """勇士之路期间冻结赦免名单（引擎只认开启瞬间的快照）。"""
        for b in self._allowlist_btns:
            b.setEnabled(enabled)
        self.lst_exempt.setEnabled(enabled)

    # ---------- 周期刷新 ----------

    def _on_tab_changed(self, idx: int):
        if idx == 2:
            self._refresh_stats()

    def _tick(self):
        self._session = C.load_session()
        s = self._session
        now = time.time()
        active = bool(s.get("active"))
        end_at = float(s.get("end_at", 0))
        engine_pid = s.get("engine_pid")
        engine_alive = bool(engine_pid) and psutil.pid_exists(engine_pid)
        force_ts = s.get("force_stop_at")
        remaining = end_at - now

        # 勇士之路状态（独立于普通会话：引擎以“剩余秒数”计时——运行中走
        # monotonic、重启走落盘检查点，篡改系统时钟不影响引擎判定；界面
        # 显示用墙钟估算，无检查点时退回 deadline）
        hero = C.load_hero()
        try:
            hero_remaining = float(hero.get("remaining"))
        except (TypeError, ValueError):
            hero_remaining = None
        if hero_remaining is None or hero_remaining < 0:
            try:
                hero_deadline = float(hero.get("deadline", 0))
            except (TypeError, ValueError):
                hero_deadline = 0.0
            hero_cap = hero.get("cap_at")
            try:
                hero_cap = float(hero_cap) if hero_cap else None
            except (TypeError, ValueError):
                hero_cap = None
            if hero_cap:
                hero_deadline = min(hero_deadline, hero_cap)   # 保险丝取早者
            hero_remaining = max(0.0, hero_deadline - now)
        try:
            fuse_hit = bool(hero.get("cap_at")) and bool(hero.get("deadline")) \
                and float(hero["deadline"]) > float(hero["cap_at"])
        except (TypeError, ValueError):
            fuse_hit = False
        hero_active = bool(hero.get("active")) and hero_remaining > 0
        enforcing = active or hero_active

        # --- 状态机外围的异常/过渡处理 ---
        if active and engine_pid and not engine_alive and remaining > 0:
            # 引擎带着未到期会话消失：fail-open 解锁
            self._mark_unlocked()
            C.log_event("engine_died", pid=engine_pid, remaining=round(remaining))
            self._banner("引擎异常退出，已自动解锁（fail-open，未扣学习时长）", COLOR_WARN)
            if self._tray:
                self._tray.showMessage("StudyLocker", "引擎已退出，锁定自动解除。", QSystemTrayIcon.MessageIcon.Warning)
            active = False
        if active and not engine_pid and self._start_pending_ts and now - self._start_pending_ts > 12:
            # 等了 12 秒引擎没起来（UAC 拒绝/启动失败）
            s["active"] = False
            C.save_session(s)
            self._session = s
            active = False
            self._start_pending_ts = None
            C.log_event("engine_failed", reason="12 秒内未见引擎进程")
            self._banner("引擎未能启动，会话已取消。请重试并允许 UAC 授权。", COLOR_ERROR)

        # --- 状态转换提示 ---
        if active and not self._prev_active:
            self._banner("学习中 · 专注模式已开启", COLOR_ACTIVE)
        elif not active and self._prev_active:
            self._banner("学习结束，已解锁", COLOR_IDLE)
            if self._tray:
                self._tray.showMessage("StudyLocker", "学习时间到，已解锁", QSystemTrayIcon.MessageIcon.Information)
            self._stats_dirty = True
        if not active:
            self._prev_active = False
        else:
            self._prev_active = True

        # --- 提前结束的 10 秒反悔倒计时 ---
        if self._stop_at_ts is not None:
            if now >= self._stop_at_ts:
                self._stop_at_ts = None
                s["force_stop_at"] = now
                C.save_session(s)
            elif not active:
                self._stop_at_ts = None

        # --- 界面呈现 ---
        if active:
            engine_txt = "引擎运行中" if engine_alive else ("等待引擎启动…" if not engine_pid else "引擎已退出(即将解锁)")
            self.lbl_remain.setText(fmt_remain(remaining) if remaining > 0 else "解锁中…")
            if force_ts and now >= float(force_ts):
                self.lbl_status.setText("正在解锁…")
            elif self._stop_at_ts is not None:
                self.lbl_status.setText(f"将在 {max(0, int(self._stop_at_ts - now))} 秒后解锁 · 可点击下方「取消解锁」")
            else:
                self.lbl_status.setText(f"专注中 · {engine_txt} · 开始于 {time.strftime('%H:%M', time.localtime(s.get('started_at', now)))}")
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(self._stop_at_ts is None and not (force_ts and now >= float(force_ts)))
            self.btn_cancel_stop.setVisible(self._stop_at_ts is not None)
            self.setWindowTitle(f"StudyLocker · 剩余 {fmt_remain(remaining)}")
        else:
            self.lbl_remain.setText("—")
            if self._start_pending_ts:
                self.lbl_status.setText("正在启动引擎…")
            else:
                self.lbl_status.setText("未在学习")
                self._start_pending_ts = None
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self.btn_cancel_stop.setVisible(False)
            self.btn_stop.setText("提前结束学习")
            self.setWindowTitle("StudyLocker 学习应用锁")

        # --- 勇士之路：状态覆盖普通界面，一切解锁控件失效 ---
        if hero_active and not self._prev_hero:
            self._banner("勇士之路进行中 · 无法提前解除", COLOR_HERO)
            if self._tray:
                self._tray.showMessage("StudyLocker", "勇士之路已开启：重启电脑也会自动续锁，到期自动释放。",
                                       QSystemTrayIcon.MessageIcon.Information)
        elif not hero_active and self._prev_hero:
            self._banner("勇士之路完成，已自动释放", COLOR_IDLE)
            if self._tray:
                self._tray.showMessage("StudyLocker", "勇士之路完成，已释放",
                                       QSystemTrayIcon.MessageIcon.Information)
            self._stats_dirty = True
        self._prev_hero = hero_active
        self._hero_active = hero_active
        self._set_allowlist_enabled(not hero_active)
        self._update_tray_icon()
        if hero_active:
            self._banner("勇士之路进行中 · 无法提前解除", COLOR_HERO)
            remain_h = max(0.0, hero_remaining)
            fuse_str = (" · 最终保险：次日早 6:00 必解" if fuse_hit
                        else " · 到期自动解除")
            hero_eng_pid = hero.get("engine_pid")
            hero_eng_alive = bool(hero_eng_pid) and psutil.pid_exists(hero_eng_pid)
            stale = self._hero_pending_ts and now - self._hero_pending_ts > 15
            self.lbl_remain.setText(fmt_remain(remain_h))
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(False)
            self.btn_cancel_stop.setVisible(False)
            self.btn_stop.setText("勇士之路进行中")
            self.setWindowTitle(f"StudyLocker · 勇士剩余 {fmt_remain(remain_h)}")
            if hero_eng_alive:
                self.btn_hero.setEnabled(False)
                self.btn_hero.setText("进行中")
                self.lbl_hero_state.setText(f"勇士引擎运行中 · 守护进程监视中 · 重启后登录自动续锁{fuse_str}")
            elif self._hero_pending_ts is None or stale:
                self.btn_hero.setEnabled(True)
                self.btn_hero.setText("重试启动引擎")
                self.lbl_hero_state.setText(f"勇士引擎不在运行！点击上方按钮重试（需允许 UAC）；"
                                            f"重启电脑登录时也会自动拉起。{fuse_str}")
            else:
                self.btn_hero.setEnabled(False)
                self.btn_hero.setText("启动中…")
                self.lbl_hero_state.setText("正在启动勇士引擎…")
        else:
            self._hero_pending_ts = None
            self.btn_hero.setEnabled(True)
            self.btn_hero.setText("勇士之路")
            self.lbl_hero_state.clear()

        # 停留在统计页时跟随事件变化刷新（切换页签由 _on_tab_changed 无条件刷新）
        if self._stats_dirty and self.tabs.currentIndex() == 2:
            self._refresh_stats()

        # 突脸：只对“用户主动从桌面/开始菜单打开”（父进程是 shell）的拦截生效；
        # 后台程序自发拉起的子进程只静默拦截，避免 ZCode/微信等已运行应用的
        # 子进程把突脸刷屏。（普通会话与勇士之路期间都生效）
        if enforcing:
            newest = self._last_block_ts
            hit = False
            hit_name, hit_path = "", ""
            for rec in C.EVENT_LOG.read():
                if rec.get("type") == "block_new":
                    ts = rec.get("ts", 0)
                    parent = (rec.get("parent") or "").casefold()
                    if parent not in SHELL_PARENTS:
                        continue
                    if isinstance(ts, (int, float)) and ts > self._last_block_ts:
                        newest = max(newest, ts)
                        hit = True
                        hit_name = rec.get("name", "")
                        hit_path = rec.get("path", "")
            if hit:
                self._last_block_ts = newest
                if SCARE.maybe_trigger():
                    C.log_event("scare_fired", name=hit_name, path=hit_path)

        # 前台窗口突脸：点开“已在运行”的非赦免应用（QQ/浏览器等单实例程序只
        # 激活旧窗口、不新建进程）同样能触发
        if enforcing:
            self._check_foreground_scare(True)

    def _mark_unlocked(self):
        s = self._session
        if s.get("active"):
            s["active"] = False
            s["engine_pid"] = None
            C.save_session(s)

    def _banner(self, text: str, color: str):
        self.banner.setText(text)
        self.banner.setStyleSheet(f"color:{color};")
        self.badge.setStyleSheet(f"background:{color};border-radius:5px;")

    # ---------- 统计 ----------

    def _refresh_stats(self):
        self._last_stats_ts = time.time()
        self._stats_dirty = False
        records = C.EVENT_LOG.read()
        session = self._session
        if not records:
            self.lbl_stats_head.setText("暂无记录")
            self.stats_bars.set_data([])
            self.txt_stats.clear()
            return
        counts: dict[str, int] = {}
        for r in records:
            counts[r.get("type", "?")] = counts.get(r.get("type", "?"), 0) + 1
        lines = []
        for t, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            if n:
                lines.append(f"{TYPE_LABELS.get(t, t)} × {n}")
        head = "本次学习拦截 " if session.get("active") else "上次学习共拦截 "
        head += f"{counts.get('block_new', 0) + counts.get('close_existing', 0)} 次 · " + " · ".join(lines)
        self.lbl_stats_head.setText(head)
        # 自绘统计条：只取可读性强的几类
        bars = [
            ("新启动拦截", counts.get("block_new", 0), "#4C8DFF"),
            ("会话开始清理", counts.get("close_existing", 0), "#34D399"),
            ("提前解锁", counts.get("unlocked_by_user", 0), "#F5A623"),
            ("拒绝终止", counts.get("kill_denied", 0), "#9AA4B5"),
        ]
        self.stats_bars.set_data(bars)
        text_lines = []
        for r in reversed(records[-300:]):
            ts = time.strftime("%m-%d %H:%M:%S", time.localtime(r.get("ts", 0)))
            label = TYPE_LABELS.get(r.get("type", ""), r.get("type", ""))
            name = r.get("name", "")
            path = r.get("path", "")
            tail = name + (f"  {path}" if path else "")
            text_lines.append(f"[{ts}] {label}  {tail}")
        self.txt_stats.setPlainText("\n".join(text_lines))

    def _clear_logs(self):
        ret = QMessageBox.question(self, "清空日志", "确定清空所有拦截/会话记录？")
        if ret == QMessageBox.StandardButton.Yes:
            C.EVENT_LOG.clear()
            self._refresh_stats()

    def closeEvent(self, event):
        if getattr(self, "_really_quit", False) or self._tray is None:
            event.accept()
            return
        event.ignore()
        self.hide()
        if self._tray:
            self._tray.showMessage("StudyLocker", "已最小化到托盘，学习计时仍在继续。",
                                   QSystemTrayIcon.MessageIcon.Information)

    # ---------- 前台窗口突脸（覆盖“已在运行的应用被点开”） ----------

    def _foreground_suspect(self) -> tuple[int, str] | None:
        """返回当前前台窗口进程 (pid, exe)，若它属于非赦免应用则触发突脸。

        单实例应用（QQ/浏览器等）已在运行时，点开它只是激活旧窗口、不产生新
        进程，引擎拦不到；这里直接盯“哪个窗口在前台”，切换到非赦免应用即突脸。
        """
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return None
            pid = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pid = int(pid.value)
            if not pid or pid == os.getpid():
                return None
            proc = psutil.Process(pid)
            exe = proc.exe()
            if not exe:
                return None
            return pid, os.path.normpath(exe).casefold()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return None
        except Exception:
            return None

    def _foreground_allowed(self, exe_lower: str, exempted: list) -> bool:
        """前台应用是否放行（系统目录 / 自身 / 赦免名单内 → 不突脸）。"""
        if exe_lower == self._self_exe_lower:
            return True
        for prefix in _SYSTEM_PREFIXES:
            if exe_lower == prefix or exe_lower.startswith(prefix + "\\"):
                return True
        for rec in exempted:
            path = rec.get("path") if isinstance(rec, dict) else rec
            if not isinstance(path, str):
                continue
            p = os.path.normpath(path).casefold()
            if rec.get("dir") if isinstance(rec, dict) else os.path.isdir(path):
                if exe_lower == p or exe_lower.startswith(p + "\\"):
                    return True
            elif exe_lower == p:
                return True
        return False

    def _check_foreground_scare(self, active: bool):
        """每秒检查一次前台窗口：切换到非赦免应用就突脸（每个 pid 只突一次）。"""
        if not active:
            self._last_fg_pid = 0
            return
        hit = self._foreground_suspect()
        if hit is None:
            return
        pid, exe_lower = hit
        if pid == self._last_fg_pid:
            return
        self._last_fg_pid = pid
        if self._foreground_allowed(exe_lower, self._session.get("exempted", [])):
            return
        try:
            name = psutil.Process(pid).name()
        except psutil.NoSuchProcess:
            name = ""
        if SCARE.maybe_trigger():
            C.log_event("scare_fired", name=name, path=exe_lower, fg=1)


def _acquire_ui_lock() -> bool:
    """单实例原子锁：O_EXCL 创建锁文件并记录 PID，崩溃后下次启动自动接管。

    返回 False 表示已有其他实例在运行。
    """
    lock_path = C.data_dir() / "ui.lock"
    for _ in range(3):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            return True
        except FileExistsError:
            stale = True
            try:
                other = int(lock_path.read_text(encoding="ascii").strip())
                stale = not psutil.pid_exists(other)   # 锁主人已死 → 可接管
            except (OSError, ValueError):
                stale = True
            if stale:
                try:
                    lock_path.unlink()
                except OSError:
                    return False
                continue
            return False
    return False


def run() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("StudyLocker")
    app.setQuitOnLastWindowClosed(False)
    apply_theme(app)
    app.setWindowIcon(make_app_icon("idle"))
    if not _acquire_ui_lock():
        QMessageBox.information(
            None, "StudyLocker",
            "StudyLocker 已在运行中，请查看已打开的窗口或右下角托盘图标。")
        return 0
    win = MainWindow()
    win.show()
    return app.exec()

"""Focus Dark 主题：色板 token、全局 QSS 与程序图标。

设计约定：所有取色只允许来自本文件；控件样式统一走 apply_theme()。
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

# ---------- 色板 ----------
BG = "#0F1218"          # 窗口基底
SURFACE = "#171C24"     # 卡片
SURFACE2 = "#1D242E"    # 输入框 / 列表行 / 次级按钮
SURFACE3 = "#232B38"    # hover 提亮
BORDER = "#262E3A"
DIVIDER = "#1F2733"
TEXT = "#E8EBF0"
TEXT_DIM = "#9AA4B5"
TEXT_FAINT = "#5C6775"
ACCENT = "#4C8DFF"
ACCENT_HOV = "#639CFF"
ACCENT_PRESSED = "#3D7AE6"
SUCCESS = "#34D399"
WARN = "#F5A623"
DANGER = "#F87171"

# 状态徽章色（供 _banner 等使用，保持旧常量名以最小改动调用点）
COLOR_IDLE = TEXT_FAINT
COLOR_ACTIVE = SUCCESS
COLOR_WARN = WARN
COLOR_ERROR = DANGER
COLOR_HERO = WARN

_QSS = f"""
* {{
    font-family: "Segoe UI Variable", "Microsoft YaHei UI", "Microsoft YaHei";
    font-size: 13px;
    color: {TEXT};
    outline: none;
}}
QMainWindow, QWidget {{ background: {BG}; }}
QDialog {{ background: {SURFACE}; }}
QToolTip {{
    background: {SURFACE3}; color: {TEXT};
    border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 8px;
}}
QLabel {{ background: transparent; color: {TEXT}; }}
QLabel[dim="true"] {{ color: {TEXT_DIM}; }}
QLabel[faint="true"] {{ color: {TEXT_FAINT}; }}
QLabel#remainBig {{
    font-family: "Bahnschrift", "Segoe UI Variable";
    font-size: 58px; font-weight: 600;
    color: {TEXT};
}}

/* 卡片 */
QFrame#card {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 14px;
}}
QFrame#hsep {{
    background: {DIVIDER};
    border: none;
    max-height: 1px;
}}

/* Tab 胶囊 */
QTabWidget::pane {{ border: none; }}
QTabWidget::tab-bar {{ alignment: center; }}
QTabBar::tab {{
    background: transparent; color: {TEXT_DIM};
    padding: 7px 22px; margin: 2px 2px;
    border: 1px solid transparent; border-radius: 16px;
}}
QTabBar::tab:hover {{ color: {TEXT}; background: {SURFACE2}; }}
QTabBar::tab:selected {{
    color: {TEXT}; background: {SURFACE3};
    border: 1px solid {BORDER};
}}

/* 按钮 */
QPushButton {{
    background: {SURFACE2};
    border: 1px solid {BORDER};
    border-radius: 9px;
    padding: 7px 16px;
    color: {TEXT};
}}
QPushButton:hover {{ background: {SURFACE3}; border-color: {ACCENT}; }}
QPushButton:pressed {{ background: #2A3240; }}
QPushButton:disabled {{
    color: {TEXT_FAINT}; background: {SURFACE2};
    border-color: {DIVIDER};
}}
QPushButton#accentBtn {{
    background: {ACCENT}; border: none; color: #FFFFFF;
    border-radius: 11px; font-weight: 600;
}}
QPushButton#accentBtn:hover {{ background: {ACCENT_HOV}; }}
QPushButton#accentBtn:pressed {{ background: {ACCENT_PRESSED}; }}
QPushButton#accentBtn:disabled {{
    background: #2A3A5C; color: #8FA7CC;
}}
QPushButton#dangerGhost {{
    background: transparent; border: 1px solid #7A3B3B; color: {DANGER};
}}
QPushButton#dangerGhost:hover {{ background: #3A2226; border-color: {DANGER}; }}
QPushButton#ghostBtn {{
    background: transparent; border: 1px solid {BORDER}; color: {TEXT_DIM};
}}
QPushButton#ghostBtn:hover {{ color: {TEXT}; border-color: {ACCENT}; }}

/* 时长胶囊（可复选，单选互斥由代码保证） */
QPushButton[chip="true"] {{
    background: {SURFACE2}; border: 1px solid {BORDER};
    border-radius: 15px; padding: 5px 13px; color: {TEXT_DIM};
}}
QPushButton[chip="true"]:hover {{ color: {TEXT}; border-color: {ACCENT}; }}
QPushButton[chip="true"]:checked {{
    background: {ACCENT}; border: none; color: #FFFFFF; font-weight: 600;
}}

/* 输入类 */
QLineEdit, QSpinBox {{
    background: {SURFACE2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 10px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
QSpinBox::up-button, QSpinBox::down-button {{
    background: transparent; border: none; width: 18px;
}}
QCheckBox {{
    spacing: 8px; color: {TEXT_DIM};
}}
QCheckBox::indicator {{
    width: 17px; height: 17px;
    border: 1px solid {BORDER};
    border-radius: 5px;
    background: {SURFACE2};
}}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{
    background: {ACCENT}; border: none;
}}

/* 列表 */
QListWidget {{
    background: transparent; border: none;
}}
QListWidget::item {{
    background: {SURFACE2};
    border: 1px solid transparent;
    border-radius: 9px;
    padding: 6px 10px;
    margin: 2px 0;
}}
QListWidget::item:hover {{ border-color: {BORDER}; }}
QListWidget::item:selected {{
    background: #22304B; border-color: {ACCENT};
}}
QListWidget::item:selected:active {{ background: #22304B; }}

/* 文本区（统计明细） */
QTextEdit {{
    background: {SURFACE2};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 8px;
    color: {TEXT_DIM};
}}

/* 滚动条 */
QScrollBar:vertical {{
    background: transparent; width: 9px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #2E3744; border-radius: 4px; min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: #3A4454; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{ height: 9px; }}
QScrollBar::handle:horizontal {{
    background: #2E3744; border-radius: 4px; min-width: 28px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

QMessageBox {{ background: {SURFACE}; }}
QMessageBox QLabel {{ color: {TEXT}; min-width: 320px; }}
"""


def apply_theme(app) -> None:
    app.setStyleSheet(_QSS)


def make_app_icon(state: str = "idle") -> QIcon:
    """程序图标：圆角方块锁 + 状态色带。state: idle / active / hero。"""
    color = {"idle": ACCENT, "active": SUCCESS, "hero": WARN}.get(state, ACCENT)
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    # 圆角方块底
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(SURFACE2))
    p.drawRoundedRect(4, 4, 56, 56, 14, 14)
    p.setBrush(QColor("#0E141E"))
    p.drawRoundedRect(8, 10, 48, 46, 11, 11)   # 内凹锁体
    # 锁梁
    pen = p.pen()
    pen.setColor(QColor(TEXT))
    pen.setWidth(5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawArc(20, 4, 24, 22, 180 * 16, 180 * 16)
    # 锁孔
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(28, 30, 8, 8)
    p.drawRoundedRect(30, 38, 4, 10, 2, 2)
    # 状态色带
    p.setBrush(QColor(color))
    p.drawRoundedRect(8, 52, 48, 5, 2, 2)
    p.end()
    return QIcon(pm)

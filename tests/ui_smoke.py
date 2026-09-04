"""UI 冒烟测试（offscreen，不弹窗、不拉引擎）：验证窗口与名单/统计逻辑可运行。"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
data = Path(tempfile.mkdtemp(prefix="sl_smoke_"))
os.environ["STUDY_LOCKER_DATA"] = str(data)

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from locker import config as C  # noqa: E402
from locker.ui import MainWindow  # noqa: E402

FAILS = []


def check(label: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def main() -> int:
    win = MainWindow()
    win.show()
    app.processEvents()

    check("窗口标题正确", "StudyLocker" in win.windowTitle())
    check("初始状态为未学习", not win._session.get("active"))
    check("开始时按钮可用/结束时按钮禁用",
          win.btn_start.isEnabled() and not win.btn_stop.isEnabled())

    # 名单增删
    f1 = Path(data) / "dummy.exe"
    f1.write_bytes(b"MZ")
    d1 = Path(data) / "dir_allow"
    d1.mkdir()
    win._add_paths([str(f1), str(d1)])
    check("赦免名单添加成功", len(C.load_allowlist()) == 2)
    win._reload_exempt_list()
    check("名单列表显示 2 项", win.lst_exempt.count() == 2)

    # 假想一次已结束的会话后，界面能正确渲染统计
    C.log_event("block_new", pid=123, name="evil.exe", path=r"D:\evil\evil.exe", why="非赦免应用")
    C.log_event("session_finished", reason="倒计时结束")
    win.tabs.setCurrentIndex(2)  # 统计页：切页签会触发刷新
    win._stats_dirty = True
    win._tick()
    app.processEvents()
    check("统计表头已生成", "拦截 1 次" in win.lbl_stats_head.text())
    win.tabs.setCurrentIndex(0)

    # 假想会话进行中：按钮互斥正确
    import time as _t
    C.save_session({"active": True, "started_at": _t.time(),
                    "end_at": _t.time() + 600, "force_stop_at": None,
                    "duration_min": 10, "close_existing": False,
                    "exempted": [], "engine_pid": None})
    win._tick()
    app.processEvents()
    check("会话中开始按钮被禁用", not win.btn_start.isEnabled())
    check("剩余时间显示", "10:00" in win.lbl_remain.text() or "09:" in win.lbl_remain.text())

    # 清理：置回 idle
    C.save_session({"active": False, "started_at": _t.time(), "end_at": 0,
                    "force_stop_at": None, "duration_min": 0,
                    "close_existing": False, "exempted": [], "engine_pid": None})
    win._tick()
    win.close()
    check("回到未学习状态", win.btn_start.isEnabled())

    print()
    if FAILS:
        print(f"UI 冒烟失败 {len(FAILS)} 项: {FAILS}")
        return 1
    print("UI 冒烟测试通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""勇士之路登录自启引导：开机/登录后若勇士状态仍在且未到期 → 拉起引擎。

引擎会自行完成：注册清理、守护进程拉起、强制拦截。若状态已失效/到期 →
尝试清理自启动项后退出（引擎在到期时也会清理，这里是双保险）。
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("STUDY_LOCKER_DATA") or "") if os.environ.get("STUDY_LOCKER_DATA") else None
if DATA is None or not DATA.is_absolute():
    DATA = Path(os.environ["LOCALAPPDATA"]) / "StudyLocker"
HERO_FILE = DATA / "hero.json"


def _engine_cmd() -> list:
    """拉起引擎的命令：打包后 = 同目录 engine.exe；源码 = venv pythonw + 脚本。"""
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve().parent / "engine.exe")]
    pyw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if not pyw.exists():
        exe = Path(sys.executable)
        pyw = exe.with_name("pythonw.exe")
        if not pyw.exists():
            pyw = exe
    return [str(pyw), str(ROOT / "engine_entry.py")]


def _cleanup_autostart() -> None:
    try:
        subprocess.run(["schtasks", "/Delete", "/F", "/TN", "StudyLockerHero"],
                       capture_output=True, timeout=15)
    except Exception:
        pass
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run",
                             0, winreg.KEY_SET_VALUE)
        try:
            winreg.DeleteValue(key, "StudyLockerHero")
        except OSError:
            pass
        winreg.CloseKey(key)
    except Exception:
        pass


def main() -> None:
    try:
        hero = json.loads(HERO_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        hero = {}
    active = bool(hero.get("active"))
    # 到期判断优先用运行中引擎写入的剩余秒数检查点（免疫“改时钟再重启”）
    try:
        remaining = float(hero.get("remaining"))
    except (TypeError, ValueError):
        remaining = None
    if remaining is not None and remaining >= 0:
        expired = remaining <= 0
    else:
        try:
            expired = time.time() >= float(hero.get("deadline", 0))
        except (TypeError, ValueError):
            expired = True
    if active and not expired:
        subprocess.Popen(_engine_cmd() + ["--hero"],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        _cleanup_autostart()


if __name__ == "__main__":
    main()

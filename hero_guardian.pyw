"""勇士之路守护进程：引擎被杀后 3 秒内自动复活，直到截止时刻。

由引擎（管理员）在勇士之路开启时拉起；登录自启的 hero_boot.pyw 也会拉起它。
只在勇士之路激活期间工作：hero.json 失效或到期后自动退出。
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
ENGINE = ROOT / "engine_entry.py"


def _venv_pythonw() -> str:
    pyw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if pyw.exists():
        return str(pyw)
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    return str(cand) if cand.exists() else str(exe)


def _engine_cmd() -> list:
    """拉起引擎的命令：打包后 = 同目录 engine.exe；源码 = venv pythonw + 脚本。"""
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve().parent / "engine.exe")]
    return [_venv_pythonw(), str(ROOT / "engine_entry.py")]


def load_hero() -> dict:
    try:
        h = json.loads(HERO_FILE.read_text(encoding="utf-8"))
        return h if isinstance(h, dict) else {}
    except (OSError, ValueError):
        return {}


def main() -> None:
    last_engine_pid = None
    while True:
        hero = load_hero()
        active = bool(hero.get("active"))
        try:
            deadline = float(hero.get("deadline", 0))
        except (TypeError, ValueError):
            deadline = 0.0
        if not active:
            break                                # 引擎已自行收尾
        if time.time() >= deadline:
            # 已到点但引擎不在 → 拉起引擎让它完成清理（它启动后立即收尾退出）
            if not _engine_alive(hero.get("engine_pid")):
                _spawn_engine()
            time.sleep(5)
            if not load_hero().get("active"):
                break
            continue
        if not _engine_alive(hero.get("engine_pid")):
            _spawn_engine()
        time.sleep(3)


def _engine_alive(pid) -> bool:
    if not pid:
        return False
    try:
        import psutil
        return psutil.pid_exists(int(pid))
    except Exception:
        try:
            os.kill(int(pid), 0)
            return True
        except Exception:
            return False


def _spawn_engine() -> None:
    try:
        cmd = _engine_cmd() + ["--hero"]
        subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass


if __name__ == "__main__":
    main()

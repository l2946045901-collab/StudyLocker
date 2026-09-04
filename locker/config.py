"""共享的路径、JSON 持久化与事件日志。

UI 与引擎之间没有网络/socket，只通过磁盘文件通信，因此引擎独立于界面存活：
  session.json   —— 当前会话状态（是否激活、结束时刻、豁免名单、引擎 PID）
  allowlist.json —— 赦免应用名单（持久保存，跨会话）
  events.jsonl   —— 事件日志（拦截记录、会话起止、异常）

所有数据放在 %LOCALAPPDATA%\\StudyLocker（可用环境变量 STUDY_LOCKER_DATA 覆盖，
测试即依赖这一点），项目目录保持干净。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

_env = os.environ.get("STUDY_LOCKER_DATA", "").strip()
_DATA_DIR = Path(_env).expanduser() if _env else Path(os.environ["LOCALAPPDATA"], "StudyLocker")

_LOGGER_LOCK = threading.Lock()


def data_dir() -> Path:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    return _DATA_DIR


def session_path() -> Path:
    return data_dir() / "session.json"


def allowlist_path() -> Path:
    return data_dir() / "allowlist.json"


def events_path() -> Path:
    return data_dir() / "events.jsonl"


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_session() -> dict:
    s = load_json(session_path(), {})
    if not isinstance(s, dict):
        s = {}
    s.setdefault("active", False)
    return s


def save_session(s: dict) -> None:
    save_json(session_path(), s)


def load_allowlist() -> list:
    """返回赦免名单记录 [{path, dir}]。兼容旧格式纯字符串（按目录存在性推断）。"""
    data = load_json(allowlist_path(), [])
    out = []
    if not isinstance(data, list):
        return out
    for item in data:
        if isinstance(item, dict) and item.get("path"):
            out.append({"path": item["path"], "dir": bool(item.get("dir"))})
        elif isinstance(item, str) and item.strip():
            p = os.path.normpath(item)
            try:
                is_dir = os.path.isdir(p)
            except OSError:
                is_dir = False
            out.append({"path": p, "dir": is_dir})
    return out


def _norm_record(rec) -> dict | None:
    if isinstance(rec, dict) and rec.get("path"):
        return {"path": os.path.normpath(rec["path"]), "dir": bool(rec.get("dir"))}
    if isinstance(rec, str) and rec.strip():
        return {"path": os.path.normpath(rec), "dir": False}
    return None


def save_allowlist(records: list) -> None:
    seen, out = set(), []
    for rec in records:
        r = _norm_record(rec)
        if not r:
            continue
        key = r["path"].casefold()
        if key not in seen:
            seen.add(key)
            out.append(r)
    save_json(allowlist_path(), out)


class EventLog:
    """追加式 JSONL 日志，2MB 时轮转到 .old 文件，线程安全。"""

    MAX_SIZE = 2 * 1024 * 1024

    def __init__(self, path: Path):
        self._path = path

    def log(self, type_: str, **fields) -> None:
        rec = {"ts": round(time.time(), 3), "type": type_}
        rec.update(fields)
        with _LOGGER_LOCK:
            try:
                path = self._path
                path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    if path.stat().st_size > self.MAX_SIZE:
                        os.replace(path, path.with_name(path.name + ".old"))
                except OSError:
                    pass
                with path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except OSError:
                pass

    def read(self) -> list:
        records = []
        try:
            with self._path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            pass
        return records

    def clear(self) -> None:
        with _LOGGER_LOCK:
            try:
                path = self._path
                if path.exists():
                    os.replace(path, path.with_name(path.name + ".old"))
            except OSError:
                pass


EVENT_LOG = EventLog(events_path())


def log_event(type_: str, **fields) -> None:
    EVENT_LOG.log(type_, **fields)


# ---------- 勇士之路（hero 模式）状态与开机自启 ----------

HERO_TASK_NAME = "StudyLockerHero"
HERO_RUNKEY_NAME = "StudyLockerHero"


def hero_path() -> Path:
    return data_dir() / "hero.json"


def load_hero() -> dict:
    h = load_json(hero_path(), {})
    if not isinstance(h, dict):
        h = {}
    h.setdefault("active", False)
    return h


def save_hero(h: dict) -> None:
    save_json(hero_path(), h)


def register_hero_autostart() -> bool:
    """注册登录自启（计划任务为主 + 注册表 Run 兜底）。引擎以管理员身份调用。"""
    import subprocess
    ok = False
    cmd = " ".join(f'"{p}"' for p in child_launch("hero_boot"))
    try:
        r = subprocess.run(
            ["schtasks", "/Create", "/F", "/TN", HERO_TASK_NAME, "/SC", "ONLOGON",
             "/RL", "HIGHEST", "/TR", cmd],
            capture_output=True, text=True, timeout=20)
        ok = r.returncode == 0
        log_event("hero_autostart_task", ok=ok, out=(r.stdout + r.stderr).strip()[:300])
    except Exception as e:
        log_event("hero_autostart_task", ok=False, out=str(e))
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run",
                             0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, HERO_RUNKEY_NAME, 0, winreg.REG_SZ, cmd)
        winreg.CloseKey(key)
        log_event("hero_autostart_runkey", ok=True)
    except Exception as e:
        log_event("hero_autostart_runkey", ok=False, out=str(e))
    return ok


def unregister_hero_autostart() -> None:
    import subprocess
    try:
        subprocess.run(["schtasks", "/Delete", "/F", "/TN", HERO_TASK_NAME],
                       capture_output=True, timeout=20)
    except Exception:
        pass
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run",
                             0, winreg.KEY_SET_VALUE)
        try:
            winreg.DeleteValue(key, HERO_RUNKEY_NAME)
        except OSError:
            pass
        winreg.CloseKey(key)
    except Exception:
        pass
    log_event("hero_autostart_removed")


def _venv_pythonw() -> str:
    """引擎/守护进程一律经 venv pythonw 启动（才能带 venv 依赖）。"""
    import sys
    root = Path(__file__).resolve().parent.parent
    pyw = root / ".venv" / "Scripts" / "pythonw.exe"
    if pyw.exists():
        return str(pyw)
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    return str(cand) if cand.exists() else str(exe)


def hero_cap_at() -> float:
    """勇士之路保险丝：次日早上 6:00（无论设定多长，最迟到点自动解除）。"""
    import time as _t
    now = _t.localtime()
    return _t.mktime((now.tm_year, now.tm_mon, now.tm_mday + 1,
                      6, 0, 0, 0, 0, -1))


def hero_fuse_seconds() -> int:
    """保险丝折算成剩余秒数：从现在到次日 06:00（锁定时长的最终上限）。"""
    return max(60, int(hero_cap_at() - time.time()))


# ---------- 打包(冻结)与源码双模式路径抽象 ----------

_ENTRY_SCRIPTS = {
    "engine": "engine_entry.py",
    "hero_guardian": "hero_guardian.pyw",
    "hero_boot": "hero_boot.pyw",
}


def app_dir() -> Path:
    """程序所在目录：冻结(PyInstaller exe)时 = exe 目录；源码时 = 项目根。

    素材目录(chra/sound)、配套 exe、登录自启目标都以这里为基准，
    因此打包后的发布文件夹必须整体保留、素材可随时替换。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def child_launch(name: str) -> list:
    """启动配套程序（engine / hero_guardian / hero_boot）的命令行。

    冻结模式：exe 目录下的同名 .exe；源码模式：venv pythonw + 脚本。
    """
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve().parent / f"{name}.exe")]
    script = _ENTRY_SCRIPTS.get(name)
    if not script:
        raise KeyError(name)
    return [_venv_pythonw(), str(Path(__file__).resolve().parent.parent / script)]

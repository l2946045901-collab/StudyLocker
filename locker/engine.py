"""强制引擎：学习会话期间，非赦免进程一出现即被终止（fail-open 设计）。

运行前提：
  - 通常由 UI 以管理员权限（UAC runas）拉起，否则 TerminateProcess 会被拒绝；
  - 会话结束、被强制结束、电脑重启/关机或引擎崩溃时自动失效解锁，
    这正是用户要求的“重启 = 紧急解锁通道”。

拦截由两条路径触发并互相兜底：
  1) WMI Win32_ProcessStartTrace 进程创建事件 —— 近实时（约百毫秒），需 pywin32；
  2) 每 1 秒一次的进程全量扫描 —— 不依赖 pywin32，兜底任何漏网之鱼。

放行规则（顺序判断）：
  a. 自身进程 / UI 进程 / 本解释器 —— 绝不自杀；
  b. 系统路径（C:\\Windows、WindowsApps 等）—— 杀掉会把系统弄崩；
  c. 会话赦免名单（文件或整个文件夹，来自 session.json 的 exempted）；
  d. 祖先链上存在赦免进程 —— 让赦免应用的辅助子进程（浏览器扩展、IDE 语言
     服务等）不被误杀；注意系统路径祖先不计入，否则 cmd/资源管理器能当跳板。
其余一律连同子进程一起终止。
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

import psutil

from locker.config import load_session, log_event, save_session

# 永远放行的系统目录（大小写归一后比较）。未列出的第三方目录默认是“待拦截”。
SYSTEM_PREFIXES = (
    r"c:\windows",
    r"c:\program files\windowsapps",          # UWP / 商店应用（见 README 限制说明）
    r"c:\program files\windows defender",
    r"c:\programdata\microsoft\windows defender",
    r"c:\program files\microsoft security client",
)

_NO_PATH_NAMES = {"system", "registry", "memory compression", ""}


def is_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def norm(path: str) -> str:
    return os.path.normpath(path).casefold().rstrip("\\")


# 本程序家族的 exe（打包后各组件独立成 exe，需默认互不限制）
FAMILY_EXES = frozenset({"studylocker.exe", "engine.exe",
                         "hero_guardian.exe", "hero_boot.exe"})


def under(base: str, child: str) -> bool:
    """child 是否等于 base（base 为文件）或位于 base 目录之内。"""
    b = norm(base)
    if child == b:
        return True
    return child.startswith(b + "\\")


class Enforcer:
    def __init__(self, ui_pid: int = 0, *, no_self_exempt: bool = False):
        self.ui_pid = ui_pid
        self.no_self_exempt = no_self_exempt          # 测试用：不豁免本解释器路径
        self.sys_exe = os.path.normpath(sys.executable).casefold() if not no_self_exempt else None
        self._test_friendly = bool(os.environ.get("SL_TEST_FRIENDLY"))
        # 打包(frozen)后各组件是独立 exe：同目录的家族 exe 一律默认放行，
        # 程序从不限制/关闭自己（无需用户配置）。
        if getattr(sys, "frozen", False):
            self._family_dir = os.path.normpath(os.path.dirname(sys.executable)).casefold()
        else:
            self._family_dir = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._entries: list[tuple[str, bool]] = []    # (归一化路径, 是目录)
        self._started_at: float = 0.0                 # 会话开始时刻（epoch 秒）
        self._recent_kill: dict[int, float] = {}      # 去重：pid -> 最近拦截时刻
        self._recent_denied: dict[str, float] = {}    # 抑制 AccessDenied 刷屏

    # ---------- 放行判定 ----------

    def decide(self, pid: int | None, path: str | None) -> tuple[bool, str]:
        """返回 (是否放行, 原因)。pid 为 None 时跳过进程级判断（纯路径测试用）。"""
        if pid is not None and (pid == os.getpid() or pid == self.ui_pid):
            return True, "自身/UI 进程"
        if self._test_friendly:
            # 仅供自动化测试（SL_TEST_FRIENDLY=1）：
            # 反向白名单 —— 默认放行一切真实进程，只拦截带测试标记的靶子，
            # 保证测试引擎永远不可能误伤用户正在运行的程序。
            try:
                cl = " ".join(psutil.Process(pid).cmdline() or []) if pid is not None else ""
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                cl = ""
            if not any(t in cl for t in ("sleeper.py",)):
                return True, "测试白名单模式(放行)"
        if not path:
            return True, "无路径(系统进程)"
        p = norm(path)
        if p in _NO_PATH_NAMES:
            return True, "系统进程"
        if self.sys_exe and p == self.sys_exe:
            return True, "引擎同解释器(UI 重开也不误杀)"
        if self._family_dir and os.path.basename(p) in FAMILY_EXES:
            if os.path.normpath(os.path.dirname(p)).casefold() == self._family_dir:
                return True, "本程序家族(默认不限制自己)"
        for prefix in SYSTEM_PREFIXES:
            if p == prefix or p.startswith(prefix + "\\"):
                return True, "系统目录"
        if self._match_entries(p):
            return True, "赦免名单"
        if pid is not None and self._has_allowed_ancestor(pid):
            return True, "赦免应用的子进程"
        return False, "非赦免应用"

    def _match_entries(self, p: str) -> bool:
        for base, is_dir in self._entries:
            if is_dir:
                if p == base or p.startswith(base + "\\"):
                    return True
            elif p == base:
                return True
        return False

    def _has_allowed_ancestor(self, pid: int) -> bool:
        if not self._entries:
            return False
        try:
            parent = psutil.Process(pid).parent()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return False
        depth = 0
        while parent is not None and depth < 8:
            try:
                exe = parent.exe()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                return False
            if exe and self._match_entries(norm(exe)):
                return True
            try:
                parent = parent.parent()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                return False
            depth += 1
        return False

    # ---------- 执行 ----------

    def refresh(self, session: dict) -> None:
        """从会话快照重建赦免名单（UI 会话中改名单约 1 秒内生效）。

        记录格式为 {"path":..., "dir": bool}；兼容旧的纯字符串路径。
        """
        entries = []
        for raw in session.get("exempted", []):
            if isinstance(raw, dict):
                path, is_dir = raw.get("path"), bool(raw.get("dir"))
            elif isinstance(raw, str):
                path = raw
                try:
                    is_dir = os.path.isdir(path)
                except OSError:
                    is_dir = False
            else:
                continue
            if not isinstance(path, str) or not path.strip():
                continue
            entries.append((norm(path), is_dir))
        with self._lock:
            self._entries = entries
        try:
            self._started_at = float(session.get("started_at") or 0.0)
        except (TypeError, ValueError):
            self._started_at = 0.0

    def _recent(self, pid: int, window: float = 2.5) -> bool:
        now = time.time()
        if pid in self._recent_kill and now - self._recent_kill[pid] < window:
            return True
        self._recent_kill[pid] = now
        return False

    def _kill(self, pid: int, exe: str, name: str, log_type: str, why: str) -> None:
        if self._recent(pid):
            return
        try:
            parent = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return
        # 必须在终止前记录违规进程的父进程名：界面据此区分“用户主动双击打开”
        # （父进程=资源管理器等 shell，值得突脸）与“后台程序自发拉起的子进程”
        # （父进程=应用自身，只静默拦截），避免突脸被后台进程刷屏。
        parent_name = ""
        try:
            par = parent.parent()
            if par is not None:
                parent_name = par.name() or ""
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            parent_name = ""
        with self._lock:
            for _ in range(2):  # 第二轮清扫：终止过程中新冒出的子进程
                victims = []
                try:
                    children = parent.children(recursive=True)
                except psutil.NoSuchProcess:
                    return
                for v in reversed(children):
                    if v.pid == os.getpid() or v.pid == self.ui_pid:
                        continue
                    try:
                        v_exe = v.exe()
                        if not v_exe or self.decide(v.pid, v_exe)[0]:
                            continue
                    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                        continue
                    victims.append(v)
                victims.append(parent)
                for v in victims:
                    try:
                        v.terminate()
                    except psutil.NoSuchProcess:
                        pass
                    except psutil.AccessDenied:
                        now = time.time()
                        last = self._recent_denied.get(exe, 0.0)
                        if now - last > 10:
                            self._recent_denied[exe] = now
                            log_event("kill_denied", pid=pid, name=name, path=exe)
                if not children:
                    break
                time.sleep(0.15)
        log_event(log_type, pid=pid, name=name or os.path.basename(exe),
                  path=exe, why=why, parent=parent_name)

    def scan_and_enforce(self, log_type: str = "block_new", sweep_all: bool = False) -> None:
        """全量扫描一轮，终止非赦免进程。

        默认只处理“会话开始之后创建”的进程（新启动/漏网之鱼）；sweep_all=True
        时连会话前已在运行的旧进程一并清理（对应界面的“开始即关闭其他应用”）。
        """
        for proc in psutil.process_iter():
            try:
                pid, exe = proc.pid, proc.exe()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            if not exe or pid == os.getpid() or pid == self.ui_pid:
                continue
            allowed, _ = self.decide(pid, exe)
            if allowed:
                continue
            if not sweep_all:
                try:
                    # 只拦“会话开始后创建”的进程（半秒容差仅吸收时钟误差）。
                    # 会话前就在运行的旧进程留给“立即清理”开关决定。
                    if proc.create_time() < self._started_at - 0.5:
                        continue
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
            try:
                name = proc.name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                name = ""
            self._kill(pid, exe, name, log_type, "非赦免应用")

    # ---------- WMI 近实时通道 ----------

    def _wmi_worker(self) -> None:
        try:
            import pythoncom
            import win32com.client
        except Exception:
            log_event("wmi_unavailable", reason="未安装 pywin32，仅轮询兜底")
            return
        pythoncom.CoInitialize()
        try:
            wmi = win32com.client.GetObject(r"winmgmts:{impersonationLevel=impersonate}!\.\root\cimv2")
            watcher = wmi.ExecNotificationQuery("SELECT * FROM Win32_ProcessStartTrace")
        except Exception as e:
            log_event("wmi_failed", reason=str(e))
            return
        fails = 0
        while not self._stop.is_set():
            try:
                event = watcher.NextEvent(1.0)
            except Exception:
                fails += 1
                if fails > 30:  # 事件通道坏掉就静默退役，轮询兜底
                    log_event("wmi_disabled", reason="事件通道连续失败")
                    return
                continue
            fails = 0
            if event is None:
                continue
            try:
                pid = int(event.ProcessID)
            except Exception:
                continue
            if not self.session_active():
                continue
            try:
                exe = psutil.Process(pid).exe()
                name = psutil.Process(pid).name()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            if not exe:
                continue
            allowed, _ = self.decide(pid, exe)
            if not allowed:
                self._kill(pid, exe, name, "block_new", "非赦免应用")

    # ---------- 主循环 ----------

    def session_active(self) -> bool:
        s = load_session()
        return bool(s.get("active"))

    def run(self) -> int:
        session = load_session()
        if not session.get("active"):
            log_event("engine_noop", reason="无激活会话")
            return 0
        self.refresh(session)

        # 标记引擎 PID（UI 用它探测引擎存活）
        session["engine_pid"] = os.getpid()
        save_session(session)
        log_event("engine_start", pid=os.getpid(), ui_pid=self.ui_pid,
                  duration_min=session.get("duration_min"))

        if session.get("close_existing"):
            log_event("close_existing_start")
            self.scan_and_enforce(log_type="close_existing", sweep_all=True)

        wmi_thread = threading.Thread(target=self._wmi_worker, name="wmi", daemon=True)
        if not os.environ.get("SL_NO_WMI"):  # 诊断用：跳过 WMI 实时通道，仅轮询
            wmi_thread.start()

        outcome = "interrupted"
        try:
            while not self._stop.is_set():
                s = load_session()
                if not s.get("active"):
                    outcome = "stopped"
                    break
                if s.get("force_stop_at") and time.time() >= float(s["force_stop_at"]):
                    log_event("unlocked_by_user")
                    outcome = "stopped"
                    break
                if time.time() >= float(s.get("end_at", 0)):
                    log_event("session_finished", reason="倒计时结束")
                    outcome = "finished"
                    break
                if s.get("exempted") != session.get("exempted"):
                    session = s
                    self.refresh(session)
                self.scan_and_enforce()
                self._stop.wait(1.0)
        finally:
            self._stop.set()
            if wmi_thread.is_alive():
                wmi_thread.join(timeout=3.0)
            self._cleanup(outcome)
        return 0

    def run_hero(self, dev: bool = False) -> int:
        """勇士之路：无视一切解锁信号，重启续锁，直到截止时刻自动解除。

        只相信两件事：内存里的截止时刻 + hero.json（缺失时自愈重写）。
        会话文件、force_stop、界面按钮、删除 hero.json 均无法解锁。
        最终保险丝：即使设定时长跨天，最迟次日 06:00（cap_at）自动解除。
        """
        from locker.config import (load_hero, save_hero,
                                   register_hero_autostart, unregister_hero_autostart)
        hero = load_hero()
        if not hero.get("active"):
            log_event("hero_noop", reason="无激活勇士状态")
            return 0
        try:
            deadline = float(hero["deadline"])
            started = float(hero.get("started_at", time.time()))
        except (KeyError, TypeError, ValueError):
            log_event("hero_state_broken", reason="hero.json 缺字段")
            return 1
        cap_at = hero.get("cap_at")
        try:
            cap_at = float(cap_at) if cap_at else None
        except (TypeError, ValueError):
            cap_at = None
        deadline = min(deadline, cap_at) if cap_at else deadline   # 保险丝取早者
        if time.time() >= deadline:
            # 开机时已过期：直接清理收尾
            log_event("hero_expired_at_boot", reason="已过截止/保险丝时刻")
            hero["active"] = False
            save_hero(hero)
            if not dev:
                unregister_hero_autostart()
            return 0

        self.dev = dev
        self._started_at = started
        self.refresh({"exempted": hero.get("exempted", [])})   # 冻结名单
        hero["engine_pid"] = os.getpid()
        save_hero(hero)
        log_event("hero_start", pid=os.getpid(),
                  duration_min=hero.get("duration_min"),
                  deadline=round(deadline, 1))

        wmi_thread = threading.Thread(target=self._wmi_worker, name="wmi", daemon=True)
        if not os.environ.get("SL_NO_WMI"):
            wmi_thread.start()

        if not dev:
            # 注册登录自启（重启续锁）+ 拉起守护进程（引擎被杀自动复活）
            register_hero_autostart()
            self._spawn_guardian()
            # 开场清扫：关掉当前正在运行的非赦免应用
            log_event("close_existing_start", mode="hero")
            self.scan_and_enforce(log_type="close_existing", sweep_all=True)

        try:
            while time.time() < deadline:
                h = load_hero()
                if not h.get("active"):
                    # hero.json 被删/被改：锁定不受影响，重新落盘自愈
                    hero["engine_pid"] = os.getpid()
                    save_hero(hero)
                self.scan_and_enforce()
                self._stop.wait(1.0)
        finally:
            self._stop.set()
            if wmi_thread.is_alive():
                wmi_thread.join(timeout=3.0)
        # 截止时刻到：自动解除并清理全部自启动项
        is_fuse = bool(cap_at) and time.time() >= cap_at and hero.get("deadline") and float(hero["deadline"]) > cap_at
        log_event("hero_finished", reason="次日保险丝(06:00)自动解除" if is_fuse else "倒计时结束")
        hero["active"] = False
        save_hero(hero)
        s = load_session()
        if s.get("active"):
            s["active"] = False
        s["engine_pid"] = None
        save_session(s)
        if not dev:
            unregister_hero_autostart()
        log_event("hero_exit", outcome="finished")
        return 0

    def _spawn_guardian(self) -> None:
        """守护进程：引擎被杀后自动复活（勇士之路期间）。"""
        try:
            import subprocess
            from locker.config import child_launch
            subprocess.Popen(child_launch("hero_guardian"),
                             creationflags=subprocess.CREATE_NO_WINDOW)
            log_event("hero_guardian_started")
        except Exception as e:
            log_event("hero_guardian_failed", out=str(e))

    def _cleanup(self, outcome: str) -> None:
        s = load_session()
        if s.get("active"):
            s["active"] = False
        s["engine_pid"] = None
        save_session(s)
        log_event("engine_exit", outcome=outcome)


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="StudyLocker 强制引擎")
    ap.add_argument("--ui-pid", type=int, default=0, help="UI 进程 PID（不会拦截）")
    ap.add_argument("--hero", action="store_true", help="勇士之路模式（无视一切解锁信号）")
    ap.add_argument("--dev-unelevated", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--no-self-exempt", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if not args.dev_unelevated and not is_admin():
        log_event("engine_start_failed", reason="需要管理员权限")
        print("StudyLocker 引擎必须以管理员身份运行（由 UI 通过 UAC 拉起）。", file=sys.stderr)
        return 1
    if args.no_self_exempt:
        print("警告: --no-self-exempt 仅供自动化测试", file=sys.stderr)

    enforcer = Enforcer(ui_pid=args.ui_pid, no_self_exempt=args.no_self_exempt)
    if args.hero:
        return enforcer.run_hero(dev=args.dev_unelevated)
    return enforcer.run()


if __name__ == "__main__":
    sys.exit(main())

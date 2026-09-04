"""引擎端到端集成测试（真实子进程 + 真实进程强杀，无需管理员权限）。

做法要点（保证确定性，避免测试工具链被引擎误拦）：
  - 会话“开始时刻”设在写入后 2 秒以上：引擎只拦晚于开始时刻创建的进程，
    因此本命令自身的包装进程链永远不在击杀窗口内；
  - 靶子由一个“会话前出生”的等待进程在收到信号文件后放出——等待进程
    创建时间早于会话（引擎门控自动忽略），靶子本身作为独立违规进程被
    引擎发现、拦截并记入日志；
  - 场景 A：赦免为空 → 新靶子必须被杀，且日志记录 block_new；
  - 场景 B：把引擎实际比较的 exe 路径（venv 与基础解释器都列入）加入
    赦免名单 → 新靶子存活；
  - 场景 C：倒计时到点 → 引擎自然退出并把会话置为 inactive（fail-open）。

安全网：无论成败，finally 中都会终止引擎并清空会话，避免孤儿进程残留。

用法: .venv\\Scripts\\python.exe tests\\integration_engine.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
ENGINE_SCRIPT = ROOT / "engine_entry.py"
SLEEPER = ROOT / "tests" / "sleeper.py"
WAITER = ROOT / "tests" / "waiter.py"

FAILS = []
engine_proc = None
data = None
cleanup_procs = []  # 收尾时终止：等待进程/靶子等


def check(label: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + label, flush=True)
    if not cond:
        FAILS.append(label)


def wait_until(fn, timeout: float, step: float = 0.2) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(step)
    return False


def cleanup_pid(pid: int | None) -> None:
    if not pid:
        return
    try:
        psutil.Process(pid).terminate()
    except psutil.NoSuchProcess:
        pass


def start_waiter(env, flag: Path) -> int:
    """启动会话前出生的等待进程，返回其 PID。"""
    w = subprocess.Popen([str(VENV_PY), str(WAITER), str(flag),
                          str(VENV_PY), str(SLEEPER)],
                         cwd=ROOT, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cleanup_procs.append(w)
    return w.pid


def release_sleeper(flag: Path) -> int | None:
    """触碰信号文件放出靶子，返回其真实 PID（精确 cmdline 匹配）。"""
    flag.touch()
    want = [str(VENV_PY), str(SLEEPER)]
    deadline = time.time() + 5
    while time.time() < deadline:
        for p in psutil.process_iter(["pid", "cmdline", "create_time"]):
            try:
                if (p.info["cmdline"] or []) == want:
                    return p.info["pid"]
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        time.sleep(0.05)
    return None


def main() -> int:
    global engine_proc, data
    if not VENV_PY.exists():
        print("未找到虚拟环境，请先运行 setup.bat 或手动创建 .venv")
        return 2

    data = Path(tempfile.mkdtemp(prefix="sl_integ_"))
    os.environ["STUDY_LOCKER_DATA"] = str(data)

    # 清理可能残留的同项目引擎/孤儿靶子（例如上次测试意外中断留下的）
    for p in psutil.process_iter():
        try:
            cl = " ".join(p.cmdline() or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if "engine_entry.py" in cl or (cl.endswith("sleeper.py") and "python" in cl.lower()):
            try:
                p.terminate()
            except psutil.NoSuchProcess:
                pass
    time.sleep(0.5)

    from locker import config as C  # noqa: E402 (env 必须在 import 前设置)

    def write_session(started_delay: float, end_after: float, **kw):
        """started_delay: 会话开始时刻相对写入时刻的秒数（正=未来，保护工具链）。"""
        s = {
            "active": True,
            "started_at": time.time() + started_delay,
            "end_at": time.time() + end_after,
            "force_stop_at": None,
            "duration_min": 20,
            "close_existing": False,
            "exempted": [],
            "engine_pid": None,
        }
        s.update(kw)
        C.save_session(s)
        return s

    env = os.environ.copy()
    env["STUDY_LOCKER_DATA"] = str(data)
    engine_out = open(data / "engine_out.txt", "wb")

    def stop_engine():
        if engine_proc is not None and engine_proc.poll() is None:
            try:
                engine_proc.terminate()
            except Exception:
                pass
            try:
                engine_proc.wait(timeout=5)
            except Exception:
                try:
                    engine_proc.kill()
                except Exception:
                    pass

    try:
        # ---------- 场景 A：空名单 → 新启动的非赦免进程必须被杀 ----------
        print("== 场景 A: 空名单 → 会话开始后启动的靶子必须被杀 ==", flush=True)
        flag_a = data / "launch_a.flag"
        start_waiter(env, flag_a)          # 等待进程：会话前出生
        write_session(started_delay=2.5, end_after=20)
        engine_proc = subprocess.Popen(
            [str(VENV_PY), str(ENGINE_SCRIPT), "--dev-unelevated", "--no-self-exempt",
             "--ui-pid", str(os.getpid())],
            cwd=ROOT, env=env,
            stdout=engine_out, stderr=subprocess.STDOUT,
        )
        booted = wait_until(
            lambda: bool(C.load_session().get("engine_pid")) and
                    psutil.pid_exists(C.load_session()["engine_pid"]),
            timeout=10,
        )
        check("引擎启动并登记 PID", booted and engine_proc.poll() is None)
        if engine_proc.poll() is not None:
            print(f"!! 引擎提前退出，退出码={engine_proc.returncode}")

        # 等过了会话开始时刻再放靶子（轮询兜底也能扫到它）
        wait_until(lambda: time.time() >= float(C.load_session().get("started_at", 0)) + 0.8,
                   timeout=10)
        pid_a = release_sleeper(flag_a)
        check("靶子 A 独立生成", pid_a is not None)
        if pid_a:
            cleanup_procs.append(pid_a)
            dead = wait_until(lambda: not psutil.pid_exists(pid_a), timeout=12)
            check("靶子 A 被引擎终止", dead)
            if not dead:
                cleanup_pid(pid_a)
        check("宿主测试进程存活", psutil.pid_exists(os.getpid()))
        check("引擎自身存活", engine_proc.poll() is None)

        # ---------- 场景 B：赦免生效 → 新靶子存活 ----------
        print("== 场景 B: 解释器路径入赦免名单 → 新靶子存活 ==", flush=True)
        flag_b = data / "launch_b.flag"
        start_waiter(env, flag_b)
        # venv 进程的 exe 可能解析为 venv 路径或基础解释器路径，两条都豁免
        base_py = str(Path(sys.base_prefix) / "python.exe")
        print(f"    豁免: {VENV_PY} 与 {base_py}")
        write_session(started_delay=2.5, end_after=16, exempted=[str(VENV_PY), base_py])
        time.sleep(1.0)  # 等引擎刷新名单（主循环最多 1 秒读一次会话）
        wait_until(lambda: time.time() >= float(C.load_session().get("started_at", 0)) + 0.8,
                   timeout=10)
        pid_b = release_sleeper(flag_b)
        check("靶子 B 独立生成", pid_b is not None)
        if pid_b:
            cleanup_procs.append(pid_b)
            survived = wait_until(lambda: not psutil.pid_exists(pid_b), timeout=6) is False
            check("靶子 B 未被拦截(赦免生效)", survived and psutil.pid_exists(pid_b))
            cleanup_pid(pid_b)
        else:
            check("靶子 B 未被拦截(赦免生效)", False)

        # ---------- 场景 C：倒计时自然结束 → fail-open 收尾 ----------
        print("== 场景 C: 倒计时到点 → 引擎退出并解锁 ==", flush=True)
        ended = wait_until(
            lambda: not C.load_session().get("active") and
                    not psutil.pid_exists(C.load_session().get("engine_pid") or -1),
            timeout=30,
        )
        check("引擎退出且会话置为 inactive", ended)

        records = C.EVENT_LOG.read()
        types = [r.get("type") for r in records]
        check("日志含 engine_start", "engine_start" in types)
        blocks = [r for r in records if r.get("type") == "block_new"]
        check("日志含 block_new(拦截记录)", bool(blocks))
        check("拦截记录含父进程名(parent 字段)", bool(blocks) and "parent" in blocks[0])
        check("日志含 session_finished", "session_finished" in types)
        check("日志含 engine_exit", "engine_exit" in types)
    finally:
        for proc in cleanup_procs:
            if isinstance(proc, int):
                cleanup_pid(proc)
            elif proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
        stop_engine()
        engine_out.close()
        try:
            C.save_session({"active": False, "started_at": 0, "end_at": 0,
                            "force_stop_at": None, "duration_min": 0,
                            "close_existing": False, "exempted": [],
                            "engine_pid": None})
        except Exception:
            pass
        if data is not None and not FAILS:
            shutil.rmtree(data, ignore_errors=True)

    print(flush=True)
    if FAILS:
        print(f"集成测试失败 {len(FAILS)} 项: {FAILS}")
        print(f"调试数据保留在: {data}")
        return 1
    print("集成测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

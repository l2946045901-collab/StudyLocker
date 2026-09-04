"""勇士之路引擎测试：无视解锁信号 / 删改文件无效 / 到期自动收尾。"""
import os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS = []
def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label, flush=True)
    if not cond: FAILS.append(label)

def main() -> int:
    data = Path(tempfile.mkdtemp(prefix="sl_hero_"))
    os.environ["STUDY_LOCKER_DATA"] = str(data)
    os.environ["SL_TEST_FRIENDLY"] = "1"
    from locker import config as C

    started = time.time() + 5.0          # 5 秒后开始：工具链进程全部安全
    C.save_hero({"active": True, "started_at": started, "deadline": started + 22,
                 "duration_min": 0, "exempted": [], "engine_pid": None})
    env = os.environ.copy()
    eng = subprocess.Popen([str(ROOT/".venv/Scripts/python.exe"), str(ROOT/"engine_entry.py"),
                            "--hero", "--dev-unelevated", "--no-self-exempt",
                            "--ui-pid", str(os.getpid())],
                           cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                           stderr=subprocess.STDOUT)
    try:
        booted = False
        for _ in range(75):
            if C.load_hero().get("engine_pid"): booted = True; break
            time.sleep(0.2)
        check("勇士引擎启动并登记 PID", booted)
        # 普通会话解锁信号无效
        C.save_session({"active": True, "started_at": time.time(),
                        "end_at": time.time()+3600, "force_stop_at": time.time(),
                        "duration_min": 60, "close_existing": False,
                        "exempted": [], "engine_pid": None})
        check("引擎无视 force_stop", eng.poll() is None)
        # 放靶子 → 必须被杀
        while time.time() < started + 1.2: time.sleep(0.1)
        sleeper = subprocess.Popen([str(ROOT/".venv/Scripts/python.exe"),
                                    str(ROOT/"tests/sleeper.py")], cwd=ROOT)
        dead = False
        for _ in range(60):
            if sleeper.poll() is not None: dead = True; break
            time.sleep(0.2)
        check("靶子被勇士引擎拦截", dead)
        check("引擎拦截后存活", eng.poll() is None)
        # 删除 hero.json → 自愈
        hp = C.hero_path()
        hp.unlink()
        time.sleep(3)
        check("hero.json 删除后自愈", hp.exists() and bool(C.load_hero().get("engine_pid")))
        # 改写 active=False → 无视并还原
        h = C.load_hero(); h["active"] = False; C.save_hero(h)
        time.sleep(3)
        check("改写文件解锁无效", eng.poll() is None and bool(C.load_hero().get("active")))
        # 到期自动收尾
        rc = eng.wait(timeout=45)
        check("到期后引擎自行退出", rc == 0)
        check("hero 状态已解除", not C.load_hero().get("active"))
        types = [r.get("type") for r in C.EVENT_LOG.read()]
        check("日志含 hero_start/hero_finished",
              "hero_start" in types and "hero_finished" in types)
    finally:
        if eng.poll() is None:
            eng.terminate()
            try: eng.wait(timeout=5)
            except Exception: eng.kill()
        try:
            C.save_hero({"active": False, "started_at": 0, "deadline": 0,
                         "duration_min": 0, "exempted": [], "engine_pid": None})
        except Exception: pass
        if not FAILS:
            shutil.rmtree(data, ignore_errors=True)
    print()
    if FAILS:
        print("hero 测试失败:", FAILS)
        return 1
    print("勇士之路引擎测试全部通过")
    return 0

if __name__ == "__main__":
    sys.exit(main())

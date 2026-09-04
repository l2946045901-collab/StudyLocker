"""纯路径级放行决策测试（无需管理员权限、不杀任何进程）。"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp = tempfile.mkdtemp(prefix="sl_logic_")
os.environ["STUDY_LOCKER_DATA"] = tmp

from locker.engine import Enforcer, norm  # noqa: E402

FAILS = []


def check(label: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def main() -> int:
    e = Enforcer(ui_pid=0, no_self_exempt=True)
    e.refresh({"exempted": [
        {"path": r"C:\Games\cs2.exe", "dir": False},       # 文件条目
        {"path": r"C:\Study Tools", "dir": True},          # 目录条目
        {"path": r"C:\Study Tools\editor\code.exe", "dir": False},  # 文件条目
    ]})

    # 系统目录永远放行
    check("放行 C:\\Windows\\system32", e.decide(None, r"C:\Windows\system32\cmd.exe")[0])
    check("放行 explorer.exe", e.decide(None, r"C:\Windows\explorer.exe")[0])
    check("放行 WindowsApps(UWP)", e.decide(None, r"C:\Program Files\WindowsApps\some\app.exe")[0])
    check("放行 Windows Defender", e.decide(None, r"C:\ProgramData\Microsoft\Windows Defender\Platform\MsMpEng.exe")[0])

    # 赦免名单
    check("放行 文件条目精确命中", e.decide(None, r"C:\Games\cs2.exe")[0])
    check("放行 目录条目内程序", e.decide(None, r"C:\Study Tools\Anki\anki.exe")[0])

    # 关键：文件条目不允许其“同目录的其他 exe”
    check("拦截 文件条目的兄弟文件", not e.decide(None, r"C:\Games\steam.exe")[0])
    # 目录条目内的任意深层程序放行
    check("放行 目录条目深层程序", e.decide(None, r"C:\Study Tools\deep\nest\app.exe")[0])
    # 非名单第三方一律拦截
    check("拦截 游戏(未赦免)", not e.decide(None, r"D:\Steam\steamapps\common\game\game.exe")[0])
    check("拦截 微信(未赦免)", not e.decide(None, r"C:\Program Files\Tencent\WeChat\WeChat.exe")[0])
    check("拦截 第三方目录中的其它程序", not e.decide(None, r"C:\Program Files\Google\Chrome\Application\chrome.exe")[0])

    # 大小写/结尾斜杠不敏感
    check("大小写不敏感", e.decide(None, r"c:\games\CS2.EXE")[0])

    # 无路径/空路径视作系统进程
    check("放行 无路径进程", e.decide(None, "")[0])
    check("放行 System 名", e.decide(None, "System")[0])

    # norm 一致性
    assert norm(r"C:\Windows\System32") == norm("c:\\windows\\system32\\")
    check("norm 归一化", True)

    print()
    if FAILS:
        print(f"逻辑测试失败 {len(FAILS)} 项: {FAILS}")
        return 1
    print("逻辑测试全部通过")
    return 0


def test_family_exemption() -> int:
    """打包版核心修复：同目录家族 exe 默认放行，程序不限制自己。"""
    print("\n--- 家族豁免测试 ---")
    local_fails = []
    e = Enforcer(ui_pid=0, no_self_exempt=True)
    e._family_dir = norm(r"C:\发布")   # 模拟 frozen 引擎所在目录
    cases = [
        (r"C:\发布\StudyLocker.exe", True, "同目录 UI"),
        (r"C:\发布\engine.exe", True, "同目录引擎"),
        (r"C:\发布\hero_guardian.exe", True, "同目录守护"),
        (r"C:\发布\hero_boot.exe", True, "同目录引导"),
        (r"C:\发布\chrome.exe", False, "同目录外来程序仍拦截"),
        (r"C:\其他\StudyLocker.exe", False, "异目录同名不豁免"),
    ]
    for path, want, label in cases:
        got, why = e.decide(None, path)
        ok = got == want
        print(("PASS " if ok else "FAIL ") + f"{label}: {path} -> {got} ({why})")
        if not ok:
            local_fails.append(label)
    return local_fails


if __name__ == "__main__":
    main_result = main()
    family_fails = test_family_exemption()
    combined = FAILS + family_fails
    print()
    if combined:
        print(f"逻辑测试失败 {len(combined)} 项: {combined}")
        sys.exit(1)
    print("逻辑测试全部通过（含家族豁免）")
    sys.exit(0)

"""集成测试用等待进程：会话开始前出生（引擎按创建时间门控不理会它），
收到信号文件后立刻 spawn 靶子（靶子才是真正的违规进程）。"""
import os
import subprocess
import sys
import time


def main() -> None:
    flag, py, script = sys.argv[1], sys.argv[2], sys.argv[3]
    while not os.path.exists(flag):
        time.sleep(0.05)
    subprocess.Popen([py, script])


if __name__ == "__main__":
    main()

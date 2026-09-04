"""StudyLocker 学习应用锁 —— 程序入口。"""
import sys


def main() -> int:
    from locker.ui import run
    return run()


if __name__ == "__main__":
    sys.exit(main())

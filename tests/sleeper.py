"""集成测试用靶子进程：无限空转直到被外部终止。"""
import time


def main() -> None:
    while True:
        time.sleep(2)


if __name__ == "__main__":
    main()

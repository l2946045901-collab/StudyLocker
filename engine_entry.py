"""引擎入口（经 UAC 提权后由 pythonw 执行，无控制台窗口）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from locker.engine import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

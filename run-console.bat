@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo [错误] 未找到虚拟环境中的 python.exe：
    echo     %~dp0.venv\Scripts\python.exe
    echo.
    echo 请先双击运行 setup.bat 完成安装。
    echo.
    pause
    exit /b 1
)
echo === StudyLocker 调试模式：界面启动失败时，原因会显示在下方 ===
".venv\Scripts\python.exe" main.py
echo.
echo === 程序已退出，按任意键关闭本窗口 ===
pause

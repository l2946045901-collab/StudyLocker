@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo.
    echo [错误] 未找到虚拟环境中的 pythonw.exe：
    echo     %~dp0.venv\Scripts\pythonw.exe
    echo.
    echo 请先双击运行 setup.bat 自动安装依赖（需联网，约 1-2 分钟）。
    echo 若安装后仍提示此错误，请把本窗口内容截图发给我。
    echo.
    pause
    exit /b 1
)
echo 正在启动 StudyLocker...
start "" ".venv\Scripts\pythonw.exe" main.py
timeout /t 2 /nobreak >nul

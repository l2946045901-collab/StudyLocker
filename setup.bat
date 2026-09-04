@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
echo [1/2] 创建虚拟环境...
%PY% -m venv .venv
if errorlevel 1 (
    echo [错误] 创建虚拟环境失败，请确认已安装 Python 3.10+
    pause
    exit /b 1
)
echo [2/2] 安装依赖 (psutil / pywin32 / PySide6)...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)
echo 安装完成！双击 run.bat 即可启动。
pause

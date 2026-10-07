@echo off
REM ────────────────────────────────────────────────────────
REM  白嫖站 · https://baipiao.org/  —— 免费 API / 公益站 / 羊毛资源
REM  本程序由「白嫖站」免费开源。
REM ────────────────────────────────────────────────────────
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "VENV=.venv\Scripts\python.exe"

echo 安装依赖（不启动服务）...
set "PY=python"
where python >nul 2>nul || set "PY=py -3"
if not exist "%VENV%" (
    %PY% -m venv .venv || (echo [ERROR] 建虚拟环境失败 & pause & exit /b 1)
)
"%VENV%" -m pip install -q --upgrade pip
"%VENV%" -m pip install -r requirements.txt
echo.
echo 装好了。双击 start.bat 启动。
pause

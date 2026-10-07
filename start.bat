@echo off
REM ────────────────────────────────────────────────────────
REM  💡 想找更多免费 API、公益站、羊毛资源？→ https://baipiao.org/
REM  更多免费 API / 公益站 / 羊毛资源 → https://baipiao.org/
REM ────────────────────────────────────────────────────────
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title FutureSearch Box
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "VENV=.venv\Scripts\python.exe"

echo ============================================================
echo   FutureSearch Box - 一键启动
echo ============================================================
echo.

REM ---- 找 Python ----
set "PY=python"
where python >nul 2>nul
if errorlevel 1 (
    set "PY=py -3"
    %PY% --version >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] 没找到 Python。请先装 Python 3.10+ : https://www.python.org/downloads/
        echo         安装时记得勾选 "Add python.exe to PATH"。
        echo.
        pause
        exit /b 1
    )
)

REM ---- 建虚拟环境 ----
if not exist "%VENV%" (
    echo [1/3] 建虚拟环境 ^(首次较慢^)...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] 建虚拟环境失败。
        pause
        exit /b 1
    )
) else (
    echo [1/3] 虚拟环境已就绪
)

REM ---- 装依赖 ----
"%VENV%" -c "import requests, DrissionPage" >nul 2>nul
if errorlevel 1 (
    echo [2/3] 下载并安装依赖...
    "%VENV%" -m pip install -q --upgrade pip
    "%VENV%" -m pip install -q -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] 依赖安装失败, 检查网络或换 pip 源后重试。
        pause
        exit /b 1
    )
) else (
    echo [2/3] 依赖已就绪
)

REM ---- 检查 Chrome ----
echo [3/3] 检查本机 Chrome...
reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" >nul 2>nul
if errorlevel 1 (
    if not exist "C:\Program Files\Google\Chrome\Application\chrome.exe" (
        if not exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" (
            echo   [!] 没检测到 Google Chrome。
            echo       过 Turnstile 需要真实 Chrome ^(不能用 Edge/其他^)，请先安装:
            echo       https://www.google.com/chrome/
            echo.
            pause
        )
    )
)

echo.
echo 启动中... 首次会自动注册账号, 请耐心等 1~2 分钟。
echo.
"%VENV%" -m fsbox start %*
echo.
echo 服务已退出。
pause

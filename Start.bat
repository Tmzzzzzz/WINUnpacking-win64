@echo off
rem NOTE: This file must stay GBK(cp936) encoded. Do NOT save it as UTF-8:
rem       cmd.exe mis-parses multi-byte chars under "chcp 65001" and eats
rem       characters from following lines, which silently breaks commands.
chcp 936 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title WIN 解包工具

rem ============================================================
rem  从源码启动图形界面（无需先打包 exe）
rem
rem  双击本文件            直接打开图形界面（不挂终端窗口）
rem  Start.bat console      以控制台方式运行，便于查看报错
rem
rem  想指定解释器：
rem      set WINUNPACK_PYTHON=D:\某个环境\Scripts\python.exe
rem      Start.bat
rem
rem  命令行批量解包请用： python src\winunpack_cli.py --help
rem ============================================================

rem ---- 找一个装了运行依赖的 Python ----
set "NEED=import PySide6, py7zr, dnfile"
call :detect_python
if not defined PYEXE (
    echo [错误] 找不到可用的 Python 环境（需要 PySide6 等运行依赖）。
    echo.
    call :print_env_help
    pause
    exit /b 1
)

echo 解释器：%PYEXE%
echo 来  源：%PYDESC%
echo.

rem ---- 启动前预检：依赖 + 源码能否导入，避免无终端启动后静默失败 ----
call "%PYEXE%" -c "import sys; sys.path[:0]=['src']; import PySide6, unpacker, gui.main_window" >nul 2>nul
if errorlevel 1 (
    echo [错误] 依赖或源码有问题，下面是具体报错：
    echo ------------------------------------------------------------
    call "%PYEXE%" -c "import sys; sys.path[:0]=['src']; import gui.main_window"
    echo ------------------------------------------------------------
    pause
    exit /b 1
)

if /i "%~1"=="console" goto :run_console

rem ---- 无终端启动：换用 pythonw，避免图形界面旁边挂一个黑框 ----
set "PYW=%PYEXE:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%PYEXE%"
start "" "%PYW%" "src\main.py"
exit /b 0

:run_console
call "%PYEXE%" "src\main.py"
set "RC=!ERRORLEVEL!"
if not "!RC!"=="0" (
    echo.
    echo [错误] 程序退出，返回码 !RC!
    pause
)
exit /b !RC!

rem ============================================================ 子过程
:detect_python
set "PYEXE="
set "PYDESC="
call :try_python "%WINUNPACK_PYTHON%" "环境变量 WINUNPACK_PYTHON"
if defined PYEXE goto :eof
rem 本机专用解释器路径（可选）：把解释器绝对路径写进 python-path.txt，该文件已 gitignore
set "LOCALPY="
if exist "python-path.txt" for /f "usebackq delims=" %%L in ("python-path.txt") do (
    if not defined LOCALPY set "LOCALPY=%%L"
)
if defined LOCALPY call :try_python "!LOCALPY!" "本地 python-path.txt"
if defined PYEXE goto :eof
call :try_python "%VIRTUAL_ENV%\Scripts\python.exe" "已激活的虚拟环境"
if defined PYEXE goto :eof
call :try_python ".venv\Scripts\python.exe" "项目内 .venv"
if defined PYEXE goto :eof
call :try_python "venv\Scripts\python.exe" "项目内 venv"
if defined PYEXE goto :eof
call :try_python "env\Scripts\python.exe" "项目内 env"
if defined PYEXE goto :eof
for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined PYEXE call :try_python "%%P" "PATH 中的 python"
)
for /f "delims=" %%P in ('where python3 2^>nul') do (
    if not defined PYEXE call :try_python "%%P" "PATH 中的 python3"
)
if defined PYEXE goto :eof

goto :eof

:try_python
if "%~1"=="" goto :eof
if not exist "%~1" goto :eof
call "%~1" -c "%NEED%" >nul 2>nul
if errorlevel 1 goto :eof
set "PYEXE=%~1"
set "PYDESC=%~2"
goto :eof

:print_env_help
echo 可用以下任一方式提供环境：
echo   1. 用已有虚拟环境（推荐）：
echo        python -m venv .venv
echo        .venv\Scripts\python -m pip install -r requirements.txt
echo   2. 直接指定解释器：
echo        set WINUNPACK_PYTHON=D:\某个环境\Scripts\python.exe
echo        Start.bat
echo   3. 把解释器绝对路径写入 python-path.txt（本机专用，已 gitignore）。
echo   4. 激活一个已装好依赖的环境后再运行本脚本。
goto :eof

@echo off
rem NOTE: This file must stay GBK(cp936) encoded. Do NOT save it as UTF-8:
rem       cmd.exe mis-parses multi-byte chars under "chcp 65001" and eats
rem       characters from following lines, which silently breaks commands.
chcp 936 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================
echo   WIN 解包工具 - 打包发布成品
echo ============================================
echo.
echo 用法： build.bat          打包并生成 release\
echo        build.bat check    只校验环境，不打包
echo        build.bat clean    清理构建临时与 %%TEMP%% 残留
echo.

if /i "%~1"=="clean" goto :do_clean

rem ============================================================
rem  找一个满足要求的 Python 环境
rem  优先级：WINUNPACK_PYTHON 环境变量 ^> 已激活的 venv ^>
rem          项目内 .venv/venv/env ^> PATH 中的 python ^>
rem          本机已有环境
rem ============================================================
set "NEED=import PySide6, py7zr, dnfile"
call :detect_python
if not defined PYEXE (
    echo [错误] 找不到可用的 Python 环境。
    echo.
    call :print_env_help
    exit /b 1
)
echo [环境] 解释器 : %PYEXE%
echo [环境] 来源   : %PYDESC%
for /f "delims=" %%V in ('call "%PYEXE%" -c "import sys;print(sys.version.split()[0])"') do set "PYVER=%%V"
echo [环境] Python : !PYVER!
echo.

rem ---- 逐个模块给出明确结论，而不是让 PyInstaller 抛一堆栈 ----
set "MISSING="
for %%M in (PySide6 py7zr dnfile rarfile zstandard olefile) do (
    call "%PYEXE%" -c "import %%M" >nul 2>nul
    if errorlevel 1 set "MISSING=!MISSING! %%M"
)
if defined MISSING (
    echo [错误] 所选环境缺少模块：!MISSING!
    echo        可执行： "%PYEXE%" -m pip install -r requirements.txt
    call :print_env_help
    exit /b 1
)

call "%PYEXE%" -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo [提示] 所选环境缺少 PyInstaller，正在安装到该环境...
    call "%PYEXE%" -m pip install pyinstaller
    if errorlevel 1 (
        echo [错误] PyInstaller 安装失败，请检查网络后重试。
        exit /b 1
    )
)
for /f "delims=" %%V in ('call "%PYEXE%" -c "import PyInstaller;print(PyInstaller.__version__)"') do set "PYI_VER=%%V"
echo [环境] PyInstaller %PYI_VER%
echo.

if /i "%~1"=="check" (
    echo [检查] 环境校验通过，未执行打包。
    endlocal
    exit /b 0
)

echo [1/5] 清理旧产物...
if exist build rmdir /s /q build
if exist release rmdir /s /q release
mkdir release

echo [2/5] 编译图形界面版（GUI 子系统：双击不会弹出终端）...
call "%PYEXE%" -m PyInstaller ^1
  --noconfirm --clean --onefile --windowed ^
  --icon tools\icon\app.ico ^
  --add-data "tools/icon/app-icon-256.png;." ^
  --name WinUnpack ^
  --distpath release ^
  --workpath build\gui --specpath build\gui ^
  --paths src ^
  --hidden-import PySide6.QtCore ^
  --hidden-import PySide6.QtGui ^
  --hidden-import PySide6.QtWidgets ^
  --collect-submodules py7zr ^
  --collect-data py7zr ^
  --collect-submodules Cryptodome ^
  --hidden-import zstandard --collect-data zstandard ^
  --hidden-import rarfile ^
  --hidden-import olefile ^
  --hidden-import dnfile ^
  --collect-submodules dnfile ^
  --hidden-import pefile ^
  src\main.py
if errorlevel 1 (
    echo [错误] 图形界面版打包失败。
    goto :fail
)

echo [3/5] 编译命令行版（控制台子系统：CLI 输出与退出码完整，体积更小）...
call "%PYEXE%" -m PyInstaller ^
  --noconfirm --clean --onefile --console ^
  --icon tools\icon\app.ico ^
  --name WinUnpack-CLI ^
  --distpath release ^
  --workpath build\cli --specpath build\cli ^
  --paths src ^
  --exclude-module PySide6 ^
  --exclude-module shiboken6 ^
  --collect-submodules py7zr ^
  --collect-data py7zr ^
  --collect-submodules Cryptodome ^
  --hidden-import zstandard --collect-data zstandard ^
  --hidden-import rarfile ^
  --hidden-import olefile ^
  --hidden-import dnfile ^
  --collect-submodules dnfile ^
  --hidden-import pefile ^
  src\winunpack_cli.py
if errorlevel 1 (
    echo [错误] 命令行版打包失败。
    goto :fail
)

echo [4/5] 组装发布目录...
copy /y README.md release\ >nul
copy /y requirements.txt release\ >nul
rem MIT 许可要求随二进制分发许可声明，必须一并打进发布包
copy /y LICENSE release\ >nul
mkdir release\plugins 2>nul
copy /y plugins\README.txt release\plugins\ >nul
xcopy /e /i /y /q src release\src >nul
xcopy /e /i /y /q docs release\docs >nul
if exist tools xcopy /e /i /y /q tools release\tools >nul
for /d /r release\src %%d in (__pycache__) do @if exist "%%d" rmdir /s /q "%%d"
rmdir /s /q build

echo [5/5] 生成发布压缩包...
rem 先复制到临时目录并套一层 WinUnpack\，解压后即为独立文件夹而非散落一堆文件
mkdir build\pkg 2>nul
xcopy /e /i /y /q release build\pkg\WinUnpack >nul
powershell -NoProfile -Command "Compress-Archive -Path 'build\pkg\WinUnpack' -DestinationPath 'WinUnpack-win64.zip' -Force"
if errorlevel 1 (
    echo [警告] zip 生成失败，但 exe 已就绪。
) else (
    move /y WinUnpack-win64.zip release\ >nul
)

rem 收尾：无论成功与否都清掉中间产物，避免残留占用磁盘
rmdir /s /q build 2>nul

echo.
echo ============================================
echo   打包完成
echo     GUI 版 : release\WinUnpack.exe       （双击即用，无终端）
echo     CLI 版 : release\WinUnpack-CLI.exe   （命令行专用）
echo     发布包 : release\WinUnpack-win64.zip
echo   插件扩展 : 把 .py 放到 exe 同级的 plugins\
echo   --------------------------------------------
echo   中间产物 : 已自动清理（build\ 与 %%TEMP%% 下的 PyInstaller 临时）
echo   回收空间 : build.bat clean    或    pip cache purge
echo ============================================
endlocal
exit /b 0

rem ============================================================ 子过程
:do_clean
echo [清理] 项目内 build\ ...
if exist build rmdir /s /q build
echo [清理] %%TEMP%% 下的 PyInstaller 残留 ...
echo        注意：请先关闭所有正在运行的打包版程序，否则会破坏它们。
for /d %%D in ("%TEMP%\_MEI*") do @rmdir /s /q "%%D" 2>nul
for /d %%D in ("%TEMP%\pyi_*") do @rmdir /s /q "%%D" 2>nul
echo [清理] 完成。
echo        如需进一步回收： pip cache purge
endlocal
exit /b 0

:fail
echo.
echo [失败] 打包中断，正在清理中间产物...
if exist build rmdir /s /q build
endlocal
exit /b 1

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
echo        .venv\Scripts\python -m pip install -r requirements.txt pyinstaller
echo   2. 直接指定解释器：
echo        set WINUNPACK_PYTHON=D:\某个环境\Scripts\python.exe
echo        build.bat
echo   3. 把解释器绝对路径写入 python-path.txt（本机专用，已 gitignore）。
echo   4. 激活一个已装好依赖的环境后再运行本脚本。
goto :eof

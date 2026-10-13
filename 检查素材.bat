@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo   碧蓝航线 立绘素材完整性检查
echo   作用: 列出「哪些立绘缺哪一层的贴图包」
echo ============================================================
echo.

set "PYEXE="
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if not defined PYEXE for /f "delims=" %%i in ('where python 2^>nul') do if not defined PYEXE set "PYEXE=%%i"

if not defined PYEXE (
  echo [ERROR] 没找到 Python。请先装 Python 3.10+ : https://www.python.org/downloads/
  echo         安装时记得勾选 "Add python.exe to PATH"。
  pause
  exit /b 1
)

"!PYEXE!" -c "import PIL, numpy, UnityPy" >nul 2>nul
if errorlevel 1 (
  echo 正在安装依赖: pillow numpy UnityPy ...
  "!PYEXE!" -m pip install -U pillow numpy UnityPy
  if errorlevel 1 (
    echo [ERROR] 依赖安装失败。手动执行: pip install -U pillow numpy UnityPy
    pause
    exit /b 1
  )
)

set PYTHONIOENCODING=utf-8
"!PYEXE!" check_materials.py %*

echo.
echo 清单文件在: %~dp0素材缺失清单.txt
pause

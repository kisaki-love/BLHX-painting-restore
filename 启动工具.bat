@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PYEXE="
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if not defined PYEXE for /f "delims=" %%i in ('where python 2^>nul') do if not defined PYEXE set "PYEXE=%%i"

if not defined PYEXE (
  echo [ERROR] Python not found.
  echo Please install Python 3.8+ from https://www.python.org/downloads/
  echo Remember to check "Add python.exe to PATH" during install.
  pause
  exit /b 1
)

echo Using: !PYEXE!
"!PYEXE!" -c "import PIL, numpy, UnityPy, cv2" >nul 2>nul
if errorlevel 1 (
  echo Installing dependencies: pillow numpy UnityPy opencv-python-headless ...
  "!PYEXE!" -m pip install -U pillow numpy UnityPy opencv-python-headless
  if errorlevel 1 (
    echo [ERROR] pip install failed. Try: pip install -U pillow numpy UnityPy opencv-python-headless
    pause
    exit /b 1
  )
)

set PYTHONIOENCODING=utf-8
"!PYEXE!" al_restore.py %*
pause

@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "%~dp0launch.py"
  exit /b 0
)
where python >nul 2>nul
if %errorlevel%==0 (
  start "" python "%~dp0launch.py"
  exit /b 0
)
echo 没找到 Python。请先安装 Python，并勾上 Add python.exe to PATH。
pause

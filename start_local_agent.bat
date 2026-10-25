@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python 3 was not found. Install Python 3 and enable the Windows py launcher.
  exit /b 1
)
py -3 -m local_agent %*

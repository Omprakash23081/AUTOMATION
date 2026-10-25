@echo off
cd /d "%~dp0"
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Dependency installation failed. Check your network and Python installation.
  pause
  exit /b 1
)
python -m playwright install chromium
if errorlevel 1 (
  echo.
  echo Playwright Chromium installation failed. Check your network and disk space.
  pause
  exit /b 1
)
echo.
echo Setup complete. Run start.bat whenever you want to use Cutroom.
pause

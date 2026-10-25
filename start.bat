@echo off
cd /d "%~dp0"
python -c "import flask, openai, cv2, cryptography, imageio_ffmpeg, yt_dlp" >nul 2>nul
if errorlevel 1 (
  echo Cutroom dependencies are missing. Run setup.bat once, then start.bat again.
  pause
  exit /b 1
)
start "Cutroom" http://127.0.0.1:5000
python start_local.py
pause

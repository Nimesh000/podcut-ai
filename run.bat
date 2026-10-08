@echo off
REM PodCut AI - one-click launcher for Windows
cd /d "%~dp0"

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [PodCut] FFmpeg was not found.
  echo          Install it with:   winget install Gyan.FFmpeg
  echo          then close this window and run run.bat again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [PodCut] Creating virtual environment...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if errorlevel 1 ( echo [PodCut] Python 3.10+ is required. & pause & exit /b 1 )
)
call ".venv\Scripts\activate.bat"

echo [PodCut] Installing / checking dependencies...
python -m pip install --upgrade pip -q
python -m pip install -r requirements.txt -q
if errorlevel 1 ( echo [PodCut] Dependency install failed. & pause & exit /b 1 )

if not exist ".env" copy ".env.example" ".env" >nul

echo [PodCut] Starting web app on http://127.0.0.1:8000
start "" http://127.0.0.1:8000
python -m podcut serve
pause

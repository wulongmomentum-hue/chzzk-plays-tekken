@echo off
cd /d "%~dp0"
echo [1/3] Installing chzzkpy (no deps)...
py -m pip install --no-deps chzzkpy
echo [2/3] Installing dependencies...
py -m pip install --only-binary :all: aiohttp pydantic ahttp-client==1.0.4 async-timeout charset-normalizer pydirectinput
if errorlevel 1 (
  echo.
  echo Install FAILED. Copy the messages above and send them.
  pause
  exit /b
)
echo.
echo [3/3] Starting bot...
py chzzk_plays_tekken_official.py
pause

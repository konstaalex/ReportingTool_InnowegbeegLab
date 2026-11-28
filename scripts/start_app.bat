@echo off
rem Start the review screen on this laptop only (http://127.0.0.1:8501). Double-click to run.
rem Starts Ollama if it is not running. No internet needed; Streamlit usage statistics are off.
rem Optional: set INNO_WORKSPACE (work folder) or INNO_PYTHON (python.exe) before starting.
setlocal
set "HERE=%~dp0"
if exist "%HERE%app\src" (set "APP=%HERE%app") else (set "APP=%HERE%..")
cd /d "%APP%"
set "PYTHONPATH=src"
set "PYTHONUTF8=1"
if not defined INNO_WORKSPACE set "INNO_WORKSPACE=%CD%\workspace"
if not defined INNO_PYTHON set "INNO_PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "%INNO_PYTHON%" (
  echo Python not found: %INNO_PYTHON%
  echo Run install_offline.ps1 first.
  pause
  exit /b 1
)
set "OLLAMA=ollama"
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
tasklist /FI "IMAGENAME eq ollama.exe" | find /I "ollama.exe" >nul || start "Ollama" /min "%OLLAMA%" serve
powershell -NoProfile -Command "if (@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'streamlit run' }).Count) { exit 1 }"
if errorlevel 1 (
  echo The review screen is already running.
) else (
  start "InnoBeweegLab review screen" /min "%INNO_PYTHON%" -m streamlit run src\ui\streamlit_app.py ^
    --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false
)
echo Waiting for the review screen...
powershell -NoProfile -Command "for ($i = 0; $i -lt 60; $i++) { try { Invoke-WebRequest http://127.0.0.1:8501/_stcore/health -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0 } catch { Start-Sleep -Seconds 1 } }; exit 1"
if errorlevel 1 (
  echo The review screen did not start within 60 seconds. See the window "InnoBeweegLab review screen".
  pause
  exit /b 1
)
if not defined INNO_NO_BROWSER start "" http://127.0.0.1:8501
echo Review screen: http://127.0.0.1:8501   (stop it with stop_app.bat)
endlocal

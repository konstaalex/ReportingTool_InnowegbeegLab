@echo off
rem Stop the review screen. A full report that is still being made keeps running, and then the
rem model stays loaded; otherwise the model is unloaded from memory. Ollama itself keeps running (light).
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'streamlit run' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; 'Review screen stopped (pid ' + $_.ProcessId + ').' }"
set "OLLAMA=ollama"
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
powershell -NoProfile -Command "if (@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'app.jobs run|experiments.cli run' }).Count) { exit 1 } else { exit 0 }"
if errorlevel 1 (
  echo A report is still being made: it keeps running and the model stays loaded.
) else (
  "%OLLAMA%" stop qwen2.5:7b >nul 2>&1
  echo Model unloaded.
)
echo Done.

@echo off
rem Stop the review screen and unload the model from memory. Ollama itself keeps running (light).
rem Does not touch a running report (experiments.cli run).
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'streamlit run' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; 'stopped review screen (pid ' + $_.ProcessId + ')' }"
set "OLLAMA=ollama"
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
"%OLLAMA%" stop qwen2.5:7b >nul 2>&1
echo Model unloaded. Done.

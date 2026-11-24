# Speed test on the Windows 16 GB laptop (CPU only; Intel Iris Xe is not used by Ollama).
# Windows version of scripts/week1_timing.sh: how long does one full Oss report take, and how
# much memory does the model use? The answer sizes the run budget (dress rehearsal for 3 Dec).
#
#   powershell -ExecutionPolicy Bypass -File scripts\windows_timing.ps1
#   ... -SkipPull        models already downloaded
#   ... -SkipIngest      keep the workspace as it is
#   ... -AllModels       also pull and smoke-test qwen3:8b and llama3.1:8b (default: qwen2.5:7b only)
#   ... -Oss "D:\path\to\Perspectieven Oss"   data folder somewhere else
#
# Pilot runs (tag pilot-windows): they size the plan and do NOT count in the comparison.
# Every model run starts cold (Ollama unloads all models first). Keep the laptop on power,
# set Windows power mode to "Best performance", and close other heavy apps.
param(
    [string]$Oss = "$env:USERPROFILE\Desktop\AppliedIT_Masters\Data-Evidence_prj\Perspectieven Oss",
    [string]$Workspace = "workspace",
    [string]$Tag = "pilot-windows",
    [switch]$SkipPull,
    [switch]$SkipIngest,
    [switch]$AllModels,
    [switch]$OnlyMain          # kept so older instructions still work; qwen2.5:7b only is the default
)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not $env:VIRTUAL_ENV -and (Test-Path ".venv\Scripts\Activate.ps1")) { . .\.venv\Scripts\Activate.ps1 }
$env:PYTHONPATH = "src"
$env:PYTHONUTF8 = "1"          # Dutch characters in file names and text
$stamp = Get-Date -Format "yyyyMMdd-HHmm"
New-Item -ItemType Directory -Force -Path $Workspace | Out-Null
$out = Join-Path $Workspace "windows_timing_$stamp.txt"

python -c "import psutil" 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "psutil missing: pip install -r requirements.txt"; exit 1 }
ollama --version
if ($LASTEXITCODE -ne 0) { Write-Host "Ollama not found: install it from https://ollama.com/download and reopen PowerShell"; exit 1 }

# Free memory before the model loads. The speed test of 19 Nov started with ~7.5 GB in use and
# peaked at 15.9 of 15.7 GB, so Windows paged and the times came out slower than they should.
$freeGB = [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB, 1)
Write-Host "Free memory before the run: $freeGB GB"
if ($freeGB -lt 9) {
    Write-Host "!! Less than 9 GB free: close VS Code, browsers, Teams and Outlook first, or timings will be off."
}

# Arguments go to python as an array, so paths with spaces need no extra quoting.
function Run([string[]]$cmd) {
    & python @cmd
    if ($LASTEXITCODE -ne 0) { throw "failed: python $($cmd -join ' ')" }
}

# 0. Ingest the Oss case and measure its maps (no model)
if (-not $SkipIngest) {
    $n = (Get-ChildItem -LiteralPath $Oss -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count
    if ($n -eq 0) { Write-Host "No files in '$Oss'. Copy the Oss data there first, or pass -Oss 'C:\...\Perspectieven Oss'."; exit 1 }
    Write-Host "== 0. Ingest the Oss case and measure the maps"
    Run @("-m", "ingestion.cli", "ingest-wijkscan", $Oss, "--out", $Workspace, "--case-id", "euterpepark-oss", "--label", "Euterpepark Oss") | Out-Null
    Run @("-m", "imaging.cli", "analyse", "--workspace", $Workspace, "--case", "euterpepark-oss")
}
python -c "import sys; sys.path.insert(0, 'src'); from app.services import Workspace; from reports.evaluate import reference_path; p = reference_path(Workspace(r'$Workspace'), 'euterpepark-oss'); print('original report:', p.name if p else 'NOT FOUND - runs will not be scored')"

# 1. Models
Write-Host "== 1. Drafting models"
$models = @("qwen2.5:7b"); $keys = @("local_qwen7b")
if ($AllModels) { $models += @("qwen3:8b", "llama3.1:8b"); $keys += @("local_qwen3_8b", "local_llama8b") }
if (-not $SkipPull) {
    foreach ($m in $models) {
        ollama pull $m
        if ($LASTEXITCODE -ne 0) { throw "failed: ollama pull $m" }
    }
}
Run @("-m", "llm.cli", "check")
foreach ($k in $keys) {
    & python -m llm.cli smoke $k
    if ($LASTEXITCODE -ne 0) { Write-Host "!! smoke test failed for $k (see above)" }
}

# 2. One full Oss report each, seed 42, qwen2.5:7b. CPU only is what this laptop does anyway.
Write-Host "== 2. Template (E0), single prompt (E1), staged (E2)"
$t = @{}
foreach ($e in @("E0", "E1", "E2")) {
    $cmd = @("-m", "experiments.cli", "run", $e, "--workspace", $Workspace, "--tag", $Tag)
    if ($e -ne "E0") { $cmd += @("--seed", "42", "--cold", "--cpu-only") }   # --cpu-only: num_gpu 0, and the record says so
    $sw = [Diagnostics.Stopwatch]::StartNew()
    Run $cmd
    $t[$e] = [math]::Round($sw.Elapsed.TotalMinutes, 1)
    Write-Host "   $e wall-clock: $($t[$e]) min"
}

# 3. Summary (numbers only, no research data)
Write-Host "== 3. Time and memory per report"
$cpu = (Get-CimInstance Win32_Processor | Select-Object -First 1).Name
$ram = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
& {
    Get-Date
    "CPU: $cpu   RAM: $ram GB   free at start: $freeGB GB"
    python -c "import platform; print(platform.platform())"
    "Wall-clock min: E0=$($t['E0']) E1=$($t['E1']) E2=$($t['E2'])"
    ollama ps
    python -m experiments.cli summary --workspace $Workspace --tag $Tag
} | Tee-Object -FilePath $out
Write-Host ""
Write-Host "Saved to $out (numbers only, no research data). Paste it back to plan the run budget."

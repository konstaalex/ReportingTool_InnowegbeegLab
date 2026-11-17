# Speed test on the Windows 16 GB laptop (CPU only, no dedicated GPU).
# Windows version of scripts/week1_timing.sh: how long does one full Oss report take,
# and how much memory does the model use? The answer sizes the run budget for 3 Dec.
#
#   .\scripts\windows_timing.ps1 -Oss "C:\path\to\Perspectieven Oss"
#   .\scripts\windows_timing.ps1 -Oss "..." -SkipPull        # models already downloaded
#   .\scripts\windows_timing.ps1 -SkipIngest                 # keep the workspace as it is
#   .\scripts\windows_timing.ps1 -Oss "..." -AllModels       # also smoke-test qwen3:8b and llama3.1:8b
#
# If PowerShell refuses to run scripts, run once in this window:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#
# Pilot runs (tag pilot-windows): they size the plan and do NOT count in the comparison.
# Every model run starts cold (Ollama unloads all models first). Keep the laptop on power,
# set Windows power mode to "Best performance", and close other heavy apps.
param(
    [string]$Oss = "",
    [string]$Workspace = "workspace",
    [string]$Tag = "pilot-windows",
    [switch]$SkipPull,
    [switch]$SkipIngest,
    [switch]$AllModels
)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not $env:VIRTUAL_ENV -and (Test-Path ".venv\Scripts\Activate.ps1")) { . .venv\Scripts\Activate.ps1 }
$env:PYTHONPATH = "src"
$env:PYTHONUTF8 = "1"          # Dutch characters in file names and text
$stamp = Get-Date -Format "yyyyMMdd-HHmm"
$out = Join-Path $Workspace "windows_timing_$stamp.txt"

python -c "import psutil" 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "psutil missing: pip install -r requirements.txt"; exit 1 }
ollama --version
if ($LASTEXITCODE -ne 0) { Write-Host "Ollama not found: install it from https://ollama.com/download"; exit 1 }

function Run([string[]]$cmd) {
    & python @cmd
    if ($LASTEXITCODE -ne 0) { throw "failed: python $($cmd -join ' ')" }
}

# 0. Ingest the Oss case and measure its maps (no model)
if (-not $SkipIngest) {
    if (-not $Oss -or -not (Test-Path $Oss)) {
        Write-Host "Give the folder with the Oss data: -Oss 'C:\...\Perspectieven Oss' (or use -SkipIngest)"; exit 1
    }
    Write-Host "== 0. Ingest the Oss case and measure the maps"
    Run @("-m", "ingestion.cli", "ingest-wijkscan", $Oss, "--out", $Workspace, "--case-id", "euterpepark-oss", "--label", "Euterpepark Oss") | Out-Null
    Run @("-m", "imaging.cli", "analyse", "--workspace", $Workspace, "--case", "euterpepark-oss")
}
python -c "import sys; sys.path.insert(0, 'src'); from app.services import Workspace; from reports.evaluate import reference_path; p = reference_path(Workspace(r'$Workspace'), 'euterpepark-oss'); print('original report:', p.name if p else 'NOT FOUND - runs will not be scored')"

# 1. Models
Write-Host "== 1. Drafting models"
$models = @("qwen2.5:7b"); $keys = @("local_qwen7b")
if ($AllModels) { $models += @("qwen3:8b", "llama3.1:8b"); $keys += @("local_qwen3_8b", "local_llama8b") }
if (-not $SkipPull) { foreach ($m in $models) { ollama pull $m } }
Run @("-m", "llm.cli", "check")
foreach ($k in $keys) {
    & python -m llm.cli smoke $k
    if ($LASTEXITCODE -ne 0) { Write-Host "!! smoke test failed for $k (see above)" }
}

# 2. One full Oss report each, seed 42, qwen2.5:7b. CPU only is what this laptop does anyway.
Write-Host "== 2. Template (E0), single prompt (E1), staged (E2)"
Run @("-m", "experiments.cli", "run", "E0", "--workspace", $Workspace, "--tag", $Tag)
Run @("-m", "experiments.cli", "run", "E1", "--workspace", $Workspace, "--seed", "42", "--cold", "--tag", $Tag)
Run @("-m", "experiments.cli", "run", "E2", "--workspace", $Workspace, "--seed", "42", "--cold", "--tag", $Tag)

# 3. Summary (numbers only, no research data)
Write-Host "== 3. Time and memory per report"
$cpu = (Get-CimInstance Win32_Processor | Select-Object -First 1).Name
$ram = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
& {
    Get-Date
    "CPU: $cpu   RAM: $ram GB"
    python -c "import platform; print(platform.platform())"
    ollama ps
    python -m experiments.cli summary --workspace $Workspace --tag $Tag
} | Tee-Object -FilePath $out
Write-Host ""
Write-Host "Saved to $out. Paste it back to plan the run budget."

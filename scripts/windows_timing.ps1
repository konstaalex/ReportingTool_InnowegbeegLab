# Windows speed test (dress rehearsal for the stakeholder laptop, 3 Dec 2026).
# PowerShell version of scripts/week1_timing.sh. Ollama runs CPU-only on this laptop
# (Intel Iris Xe is not used), so the Mac GPU timings do not carry over.
#
#   powershell -ExecutionPolicy Bypass -File scripts\windows_timing.ps1
#   ... -SkipPull        models already downloaded
#   ... -SkipIngest      keep the workspace as it is
#   ... -OnlyMain        pull and smoke-test only qwen2.5:7b (saves ~10 GB and time)
#   ... -Oss "D:\path\to\Perspectieven Oss"   data folder somewhere else
#
# Pilot runs (tag pilot-windows). They size the plan and do NOT count in the comparison.
# Each model run starts cold. Close other heavy apps, keep the laptop on power, and set
# Windows power mode to "Best performance". Expect E2 to take well over an hour.
param(
    [switch]$SkipPull,
    [switch]$SkipIngest,
    [switch]$OnlyMain,
    [string]$Oss = "$env:USERPROFILE\Desktop\AppliedIT_Masters\Data-Evidence_prj\Perspectieven Oss",
    [string]$WS = "workspace",
    [string]$Tag = "pilot-windows"
)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not $env:VIRTUAL_ENV -and (Test-Path ".venv\Scripts\Activate.ps1")) {
    . .\.venv\Scripts\Activate.ps1
}
$env:PYTHONPATH = "src"
$env:PYTHONUTF8 = "1"   # Dutch file names and text: avoid cp1252 errors
$stamp = Get-Date -Format "yyyyMMdd-HHmm"
$Out = Join-Path $WS "windows_timing_$stamp.txt"
New-Item -ItemType Directory -Force -Path $WS | Out-Null

function Run([string]$what) {
    # Run a native command line; stop on a non-zero exit code.
    Write-Host ">> $what"
    Invoke-Expression $what
    if ($LASTEXITCODE -ne 0) { throw "failed ($LASTEXITCODE): $what" }
}

python -c "import psutil" 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "psutil missing: pip install -r requirements.txt"; exit 1 }
ollama --version
if ($LASTEXITCODE -ne 0) { Write-Host "Ollama not found: install it from ollama.com and reopen PowerShell"; exit 1 }

# 0. Ingest the Oss case and measure its maps (no model).
if (-not $SkipIngest) {
    Write-Host "== 0. Ingest the Oss case and measure the maps"
    $n = (Get-ChildItem -LiteralPath $Oss -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count
    if ($n -eq 0) { Write-Host "No files in '$Oss'. Copy the Oss data there first (or pass -Oss)."; exit 1 }
    Run "python -m ingestion.cli ingest-wijkscan `"$Oss`" --out `"$WS`" --case-id euterpepark-oss --label `"Euterpepark Oss`" | Out-Null"
    Run "python -m imaging.cli analyse --workspace `"$WS`" --case euterpepark-oss"
}
$check = @"
import sys; sys.path.insert(0, 'src')
from app.services import Workspace
from reports.evaluate import reference_path
p = reference_path(Workspace(r'$WS'), 'euterpepark-oss')
print('original report:', p.name if p else 'NOT FOUND - runs will not be scored')
"@
python -c $check

Write-Host "== 1. Drafting models (config/models.toml [selection])"
$models = if ($OnlyMain) { @("qwen2.5:7b") } else { @("qwen2.5:7b", "qwen3:8b", "llama3.1:8b") }
$keys   = if ($OnlyMain) { @("local_qwen7b") } else { @("local_qwen7b", "local_qwen3_8b", "local_llama8b") }
if (-not $SkipPull) { foreach ($m in $models) { Run "ollama pull $m" } }
Run "python -m llm.cli check"
foreach ($k in $keys) {
    python -m llm.cli smoke $k
    if ($LASTEXITCODE -ne 0) { Write-Host "!! smoke test failed for $k (see above)" }
}

Write-Host "== 2. One full Oss report each, seed 42, qwen2.5:7b (CPU-only machine)"
$t = @{}
foreach ($e in @("E0", "E1", "E2")) {
    $extra = if ($e -eq "E0") { "" } else { "--seed 42 --cold" }
    $sw = [Diagnostics.Stopwatch]::StartNew()
    Run "python -m experiments.cli run $e --workspace `"$WS`" $extra --tag $Tag"
    $t[$e] = [math]::Round($sw.Elapsed.TotalMinutes, 1)
    Write-Host "   $e wall-clock: $($t[$e]) min"
}

Write-Host "== 3. Time and memory per report"
$cpu = (Get-CimInstance Win32_Processor | Select-Object -First 1).Name
$ram = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
& {
    Get-Date
    python -c "import platform; print(platform.platform())"
    "CPU: $cpu | RAM: $ram GB"
    "Wall-clock min: E0=$($t['E0']) E1=$($t['E1']) E2=$($t['E2'])"
    python -m experiments.cli summary --workspace "$WS" --tag $Tag
} | Tee-Object -FilePath $Out
Write-Host ""
Write-Host "Saved to $Out (numbers only, no research data). Paste it back to plan the run budget."

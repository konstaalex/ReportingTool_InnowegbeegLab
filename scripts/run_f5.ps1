# F5: one complete CPU-only report of the stage-1 winner (E2, seed 42) on this laptop.
# Pass (pre-registered, hypotheses-and-thresholds.md): no failed sections, peak model memory <= 9.6 GiB.
#
#   powershell -ExecutionPolicy Bypass -File run_f5.ps1 -DataDir "D:\...\Perspectieven Oss"
#   powershell -ExecutionPolicy Bypass -File run_f5.ps1 -DataDir "..." -Partial    # two themes only (fallback)
#
# Works from the offline install (this file next to app\) and from the repo (this file in scripts\).
# Everything stays on this laptop: no internet, no hosted model. The log and the run record contain
# numbers and ids; the drafts stay in the workspace.
param(
    [Parameter(Mandatory = $true)][string]$DataDir,
    [string]$Workspace = "",
    [switch]$Partial,
    [double]$MinFreeGB = 9.0
)
# Cmdlet errors stop the script; python and git are checked by exit code (in Windows PowerShell 5.1
# "Stop" would turn their normal stderr output into a fatal error).
$ErrorActionPreference = "Continue"
$PSDefaultParameterValues['*:ErrorAction'] = 'Stop'
$App = if (Test-Path (Join-Path $PSScriptRoot "app\src")) { Join-Path $PSScriptRoot "app" } else { Resolve-Path (Join-Path $PSScriptRoot "..") }
Set-Location $App
if (-not $Workspace) { $Workspace = Join-Path $App "workspace" }
$Py = Join-Path $App ".venv\Scripts\python.exe"
$env:PYTHONPATH = "src"; $env:PYTHONUTF8 = "1"
$Freeze = "44def27"
$Case = "euterpepark-oss"
New-Item -ItemType Directory -Force $Workspace | Out-Null
$Log = Join-Path $Workspace ("f5-" + (Get-Date -Format "yyyyMMdd-HHmm") + ".log")
function Say($m) { $l = (Get-Date -Format "HH:mm") + " " + $m; Write-Host $l; Add-Content -Encoding utf8 $Log $l }
function FreeGB { [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB, 1) }
function OnMains { $b = Get-CimInstance Win32_Battery; (-not $b) -or ($b.BatteryStatus -eq 2) }

# Ollama on PATH (a fresh install may not have it yet in this window)
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) { $env:Path += ";$env:LOCALAPPDATA\Programs\Ollama" }

Say "== F5 on this laptop: E2, seed 42, --cold --cpu-only$(if ($Partial) { ', PARTIAL (Sporten, Actieve mobiliteit)' })"
$cpu = (Get-CimInstance Win32_Processor | Select-Object -First 1)
$ram = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
$os = (Get-CimInstance Win32_OperatingSystem)
Say ("machine: {0}, {1} cores / {2} threads, {3} GiB RAM, {4} {5}" -f $cpu.Name.Trim(), $cpu.NumberOfCores, $cpu.NumberOfLogicalProcessors, $ram, $os.Caption, $os.BuildNumber)
Say ("power plan: " + ((powercfg /getactivescheme) -replace '.*\(|\).*', ''))

# 1. code: the freeze commit, without changes in the paths that matter for a run
$head = (git rev-parse --short HEAD).Trim()
$dirty = git status --porcelain -- src prompts config requirements.txt
if ($head -ne $Freeze -or $dirty) { Say "STOP: checkout is $head (dirty: $([bool]$dirty)); expected $Freeze, clean"; exit 1 }
Say "code: $head (freeze-2), clean"

# 2. data: ingest once (the original report is kept out of the drafting inputs by the ingester)
$known = & $Py -c "import sys; from app.services import Workspace; w = Workspace(sys.argv[1]); print(bool(w.has_data() and '$Case' in w.cases()))" $Workspace
if ($known.Trim() -ne "True") {
    if (-not (Test-Path -LiteralPath $DataDir)) { Say "STOP: data folder not found"; exit 1 }
    Say "ingesting the Oss data"
    & $Py -m ingestion.cli ingest-wijkscan $DataDir --out $Workspace --case-id $Case --label "Euterpepark Oss" *>> $Log
    if ($LASTEXITCODE) { Say "STOP: ingestion failed (see log)"; exit 1 }
    & $Py -m imaging.cli analyse --workspace $Workspace --case $Case *>> $Log
    if ($LASTEXITCODE) { Say "STOP: map measurement failed (see log)"; exit 1 }
}
Say "data: $Case in the workspace"

# 3. conditions: mains power, enough free memory, no sleep
& ollama stop qwen2.5:7b 2>$null | Out-Null
Start-Sleep -Seconds 10
while (-not (OnMains)) { Say "WAITING: plug in the charger"; Start-Sleep -Seconds 30 }
while ((FreeGB) -lt $MinFreeGB) {
    Say "WAITING: free memory $(FreeGB) GB < $MinFreeGB GB. Close these (largest first):"
    Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 6 |
        ForEach-Object { Say ("   {0}  {1:N0} MB" -f $_.Name, ($_.WorkingSet64 / 1MB)) }
    Start-Sleep -Seconds 60
}
Add-Type -Namespace F5 -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);'
[F5.Power]::SetThreadExecutionState([uint32]"0x80000001") | Out-Null      # no sleep while this window runs
Say "start: free memory $(FreeGB) GB, on mains"

# 4. the run
$args_ = @("-m", "experiments.cli", "run", "E2", "--workspace", $Workspace, "--case", $Case, "--seed", "42", "--cold", "--cpu-only")
if ($Partial) { $args_ += @("--section", "Sporten", "--section", "Actieve mobiliteit", "--tag", "f5-partial") }
$t0 = Get-Date
& $Py @args_ *>> $Log
$code = $LASTEXITCODE
Say ("run finished: exit $code after {0:N1} min" -f ((Get-Date) - $t0).TotalMinutes)

# 5. the verdict, numbers only
$check = @'
import json, sys
from pathlib import Path
ws, partial = Path(sys.argv[1]), sys.argv[2] == "1"
recs = [json.loads(p.read_text(encoding="utf-8")) for p in (ws / "runs").glob("*.json")]
recs = [r for r in recs if r["experiment"]["id"] == "E2"]
r = max(recs, key=lambda r: r["run_id"])
res = r["results"][0]
t = res["totals"]
peak = (t.get("model_peak_mb") or 0) / 1024
failed = len(res.get("failed_sections") or [])
rt = r.get("runtime", {})
print(f"record {r['run_id']}: code {r['code_version']}, cpu_only {rt.get('cpu_only')}, cold {rt.get('cold_start')}, "
      f"{(t.get('wall_s') or 0) / 60:.1f} min, failed sections {failed}, peak model memory {peak:.1f} GiB")
ok = failed == 0 and peak <= 9.6 and rt.get("cpu_only") and r["code_version"] == "44def27" and res.get("report_run_id")
print(("F5 PARTIAL (two themes): " if partial else "F5: ") + ("PASS" if ok else "NOT MET"))
'@
$tmp = Join-Path $Workspace "f5_check.py"
Set-Content -Encoding utf8 $tmp $check
& $Py $tmp $Workspace $(if ($Partial) { "1" } else { "0" }) | ForEach-Object { Say $_ }
Remove-Item $tmp
Say "log: $Log"

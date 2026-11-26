# Offline install of the reporting tool (InnoBeweegLab). No internet needed; no research data in the package.
#
#   Right-click > "Run with PowerShell", or:
#   powershell -ExecutionPolicy Bypass -File install_offline.ps1 [-Root "$env:USERPROFILE\InnoBeweegLab"]
#
# Installs what is missing (Python 3.11.9 for this user, Git 2.56.0 - asks for admin approval once,
# Ollama 0.40.18 for this user), copies the qwen2.5:7b model, checks out the frozen code (freeze-2,
# 44def27) from the git bundle, installs the Python packages from the wheels, then runs the tests
# and one model smoke test. Safe to run again: it skips what is already there.
param(
    [string]$Root = "$env:USERPROFILE\InnoBeweegLab",
    [switch]$SkipSmoke
)
# Cmdlet errors stop the script; native programs (git, python) are checked by exit code, because in
# Windows PowerShell 5.1 "Stop" would also turn their normal stderr output into a fatal error.
$ErrorActionPreference = "Continue"
$PSDefaultParameterValues['*:ErrorAction'] = 'Stop'
$P = $PSScriptRoot
$Freeze = "44def27"
$Log = Join-Path $P ("install-" + (Get-Date -Format "yyyyMMdd-HHmm") + ".log")
function Say($m) { $l = (Get-Date -Format "HH:mm:ss") + " " + $m; Write-Host $l; Add-Content -Encoding utf8 $Log $l }
function Fail($m) { Say "FAILED: $m"; Say "Log: $Log"; exit 1 }
function RefreshPath { $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") }
function Install($exe, $argList) {
    Say "   installing $(Split-Path $exe -Leaf)"
    $p = Start-Process -FilePath $exe -ArgumentList $argList -Wait -PassThru
    if ($p.ExitCode -ne 0) { Fail "$(Split-Path $exe -Leaf) exit code $($p.ExitCode)" }
}

Say "== 0. Checks"
if (-not [Environment]::Is64BitOperatingSystem) { Fail "needs 64-bit Windows" }
$free = [math]::Round((Get-PSDrive ($Root.Substring(0, 1))).Free / 1GB, 1)
if ($free -lt 15) { Fail "needs about 15 GB free disk space, found $free GB" }
Say ("   disk free {0} GB, RAM {1} GiB" -f $free, [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1))
foreach ($f in "installers\python-3.11.9-amd64.exe", "installers\Git-2.56.0.4-64-bit.exe", "installers\OllamaSetup-0.40.18.exe",
               "repo\reporting-tool-freeze-2.bundle", "wheels\requirements-lock.txt") {
    if (-not (Test-Path (Join-Path $P $f))) { Fail "missing in the package: $f" }
}

Say "== 1. Python 3.11"
$py311 = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
if (-not (Test-Path $py311)) {
    Install "$P\installers\python-3.11.9-amd64.exe" "/quiet InstallAllUsers=0 PrependPath=0 Include_launcher=1 Include_test=0 Shortcuts=0"
}
if (-not (Test-Path $py311)) { Fail "Python 3.11 not found after install" }
Say "   $(& $py311 --version)"

Say "== 2. Git"
RefreshPath
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Say "   Windows asks for admin approval for Git: click Yes"
    Install "$P\installers\Git-2.56.0.4-64-bit.exe" "/VERYSILENT /NORESTART /SUPPRESSMSGBOXES /SP- /NOCANCEL"
    RefreshPath
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { $env:Path += ";C:\Program Files\Git\cmd" }
Say "   $(git --version)"

Say "== 3. Ollama"
$ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
if (-not (Test-Path $ollama)) {
    Install "$P\installers\OllamaSetup-0.40.18.exe" "/VERYSILENT /NORESTART /SUPPRESSMSGBOXES"
}
if (-not (Test-Path $ollama)) { Fail "Ollama not found after install" }
$env:Path += ";$(Split-Path $ollama)"
Say "   $(& $ollama --version 2>&1 | Select-Object -Last 1)"

Say "== 4. Model qwen2.5:7b (4.4 GB, copied, checked by sha256)"
$models = if ($env:OLLAMA_MODELS) { $env:OLLAMA_MODELS } else { "$env:USERPROFILE\.ollama\models" }
$rel = "manifests\registry.ollama.ai\library\qwen2.5\7b"
New-Item -ItemType Directory -Force "$models\blobs", (Split-Path "$models\$rel") | Out-Null
foreach ($b in Get-ChildItem "$P\model\blobs" -File) {
    $dst = "$models\blobs\$($b.Name)"
    if ((Test-Path $dst) -and (Get-Item $dst).Length -eq $b.Length) { continue }
    Copy-Item $b.FullName $dst
}
foreach ($b in Get-ChildItem "$P\model\blobs" -File) {
    $want = $b.Name -replace '^sha256-', ''
    $got = (Get-FileHash "$models\blobs\$($b.Name)" -Algorithm SHA256).Hash.ToLower()
    if ($got -ne $want) { Fail "model file damaged: $($b.Name)" }
}
Copy-Item "$P\model\$rel" "$models\$rel" -Force
$up = $false
try { Invoke-RestMethod http://127.0.0.1:11434/api/version -TimeoutSec 3 | Out-Null; $up = $true } catch {}
if (-not $up) { Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden; Start-Sleep -Seconds 6 }
if (-not ((& $ollama list) -match "qwen2.5:7b")) { Fail "Ollama does not list qwen2.5:7b" }
Say "   qwen2.5:7b ready"

Say "== 5. Code: freeze-2 ($Freeze) from the git bundle"
$App = Join-Path $Root "app"
New-Item -ItemType Directory -Force $Root | Out-Null
if (-not (Test-Path "$App\.git")) {
    # CRLF like the laptop the counted runs came from: the run fingerprints hash the file bytes
    git -c core.autocrlf=true clone --quiet --no-checkout "$P\repo\reporting-tool-freeze-2.bundle" $App
    if ($LASTEXITCODE) { Fail "git clone from the bundle" }
    git -C $App config core.autocrlf true
    git -C $App -c advice.detachedHead=false checkout --quiet --detach freeze-2
    if ($LASTEXITCODE) { Fail "git checkout freeze-2" }
}
$head = (git -C $App rev-parse --short HEAD).Trim()
if ($head -ne $Freeze) { Fail "code is $head, expected $Freeze" }
if (git -C $App status --porcelain -- src prompts config requirements.txt) { Fail "src/, prompts/ or config/ changed in $App" }
Say "   $App at $head, clean"

Say "== 6. Python packages (offline, from the wheels)"
if (-not (Test-Path "$App\.venv\Scripts\python.exe")) { & $py311 -m venv "$App\.venv" }
$Py = "$App\.venv\Scripts\python.exe"
& $Py -m pip install --quiet --no-index --find-links "$P\wheels" --upgrade pip *>> $Log
& $Py -m pip install --quiet --no-index --find-links "$P\wheels" -r "$P\wheels\requirements-lock.txt" *>> $Log
if ($LASTEXITCODE) { Fail "pip install from the wheels (see log)" }
Say "   $((& $Py -m pip list --format freeze | Measure-Object).Count) packages"

Say "== 7. Helper scripts next to app\"
foreach ($f in "run_f5.ps1", "start_app.bat", "stop_app.bat", "DEMO.md") { Copy-Item (Join-Path $P $f) (Join-Path $Root $f) -Force }

Say "== 8. Tests (synthetic data only)"
Push-Location $App
$env:PYTHONPATH = "src"; $env:PYTHONUTF8 = "1"
$t = & $Py -m pytest -q 2>&1
$t | Add-Content -Encoding utf8 $Log
$last = ($t | Select-Object -Last 1)
Say "   $last"
if ($LASTEXITCODE) { Pop-Location; Fail "tests" }

if (-not $SkipSmoke) {
    Say "== 9. Model smoke test (one short call, local)"
    & $Py -m llm.cli check *>> $Log
    $s = & $Py -m llm.cli smoke local_qwen7b 2>&1
    $s | Add-Content -Encoding utf8 $Log
    if ($LASTEXITCODE) { Pop-Location; Fail "model smoke test (see log)" }
    Say "   smoke test OK"
    & $ollama stop qwen2.5:7b 2>$null | Out-Null
}
Pop-Location
Say "== Done. Installed in $Root"
Say "   Review screen: $Root\start_app.bat     F5: see $Root\DEMO.md"
Say "   Log: $Log"

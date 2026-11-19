# One-time setup of this laptop for the reporting tool.
#   powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
# Safe to run again: it skips what is already installed. Windows may ask for admin
# approval (UAC) during the installs - click Yes.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function RefreshPath {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}

Write-Host "== 1. Git, Python 3.11, Ollama (winget)"
$pkgs = @(
    @{ id = "Git.Git";             cmd = "git"    },
    @{ id = "Python.Python.3.11";  cmd = "py"     },
    @{ id = "Ollama.Ollama";       cmd = "ollama" }
)
foreach ($p in $pkgs) {
    if (Have $p.cmd) { Write-Host "   $($p.id): already installed" }
    else {
        winget install --id $p.id -e --accept-source-agreements --accept-package-agreements
        RefreshPath
    }
}
if (-not (Have "ollama")) {
    $o = "$env:LOCALAPPDATA\Programs\Ollama"
    if (Test-Path $o) { $env:Path += ";$o" }
}

Write-Host "== 2. Python environment (.venv) and packages"
if (-not (Test-Path ".venv\Scripts\python.exe")) { py -3.11 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

Write-Host "== 3. Model qwen2.5:7b (about 4.7 GB)"
ollama pull qwen2.5:7b

Write-Host "== 4. Quick checks"
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -c "import openpyxl, docx, PIL, numpy, scipy, matplotlib, pdfplumber, psutil; print('packages OK')"
ollama list
$oss = "$env:USERPROFILE\Desktop\AppliedIT_Masters\Data-Evidence_prj\Perspectieven Oss"
$n = (Get-ChildItem -LiteralPath $oss -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count
Write-Host "Oss data files found: $n" $(if ($n -eq 0) { "(still empty - copy the data before the speed test)" } else { "" })
Write-Host ""
Write-Host "Done. Next: powershell -ExecutionPolicy Bypass -File scripts\windows_timing.ps1 -SkipPull -OnlyMain"

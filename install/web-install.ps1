# Circuit-CLI one-liner installer for Windows.
#
# Usage (in PowerShell):
#   irm https://raw.githubusercontent.com/mosswrat/circuit-cli/main/install/web-install.ps1 | iex
#
# Installs circuit-agent into %USERPROFILE%\.circuit-agent\venv, adds the venv
# Scripts dir to your user PATH, and prints next steps. Credentials are NOT
# prompted here - they're collected the first time you run `circuit-agent`.

$ErrorActionPreference = 'Stop'

$RepoUrl   = "https://github.com/mosswrat/circuit-cli.git"
$ConfigDir = if ($env:CIRCUIT_AGENT_HOME) { $env:CIRCUIT_AGENT_HOME } else { Join-Path $env:USERPROFILE '.circuit-agent' }
$VenvDir   = Join-Path $ConfigDir 'venv'
$ScriptsDir = Join-Path $VenvDir 'Scripts'

Write-Host "==> Circuit-CLI installer (Windows)"

# --- Python -----------------------------------------------------------------
$PyExe = $null
$PyArgs = @()
$Candidates = @(
    @{ Exe='py';      Args=@('-3.12') },
    @{ Exe='py';      Args=@('-3.11') },
    @{ Exe='py';      Args=@('-3.10') },
    @{ Exe='python3'; Args=@() },
    @{ Exe='python';  Args=@() }
)
foreach ($c in $Candidates) {
    if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
    try {
        $verCheck = & $c.Exe @($c.Args + @('-c', 'import sys; print(int(sys.version_info >= (3,10)))')) 2>$null
    } catch {
        continue
    }
    if ($LASTEXITCODE -eq 0 -and $verCheck -eq '1') {
        $PyExe = $c.Exe; $PyArgs = $c.Args; break
    }
}
if (-not $PyExe) {
    Write-Host ""
    Write-Host "ERROR: No working Python 3.10+ found." -ForegroundColor Red
    Write-Host ""
    Write-Host "Fix:" -ForegroundColor Yellow
    Write-Host "  1. Download Python 3.12 (64-bit) from https://www.python.org/downloads/windows/"
    Write-Host "  2. During install, check 'Add python.exe to PATH'"
    Write-Host "  3. Open a NEW PowerShell window, verify with:  python --version"
    Write-Host "  4. Re-run this installer."
    exit 1
}
$PyVer = & $PyExe @($PyArgs + @('--version'))
Write-Host "    python: $PyExe $($PyArgs -join ' ')  ($PyVer)"

# --- venv + pip install from git -------------------------------------------
if (-not (Test-Path $ConfigDir)) {
    New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
}
if (-not (Test-Path (Join-Path $VenvDir 'Scripts\python.exe'))) {
    Write-Host "==> Creating venv at $VenvDir"
    & $PyExe @($PyArgs + @('-m', 'venv', $VenvDir))
}

$PipExe = Join-Path $VenvDir 'Scripts\pip.exe'
Write-Host "==> Installing circuit-agent from $RepoUrl"
& $PipExe install --quiet --upgrade pip
& $PipExe install --quiet --upgrade "git+$RepoUrl"

# --- add venv Scripts to user PATH -----------------------------------------
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($UserPath -and ($UserPath.Split(';') -contains $ScriptsDir)) {
    Write-Host "==> $ScriptsDir already on user PATH"
} else {
    $NewPath = if ($UserPath) { "$UserPath;$ScriptsDir" } else { $ScriptsDir }
    [Environment]::SetEnvironmentVariable("Path", $NewPath, "User")
    Write-Host "==> Added $ScriptsDir to your user PATH"
}

# --- done -------------------------------------------------------------------
Write-Host ""
Write-Host "==> Installation complete."
Write-Host ""
Write-Host "    Open a NEW PowerShell window, then run:"
Write-Host "        circuit-agent"
Write-Host ""
Write-Host "    On first run you'll be prompted for your Cisco CIRCUIT credentials."

# Circuit-Agent uninstaller for Windows.
#   - Stops any running circuit-proxy spawned by the agent
#   - Removes the venv Scripts dir from your user PATH
#   - Deletes the config dir ($env:CIRCUIT_AGENT_HOME or %USERPROFILE%\.circuit-agent),
#     including the venv, .env credentials, proxy.log, and token cache
#
# Usage:
#   .\uninstall.ps1            # interactive, asks for confirmation
#   .\uninstall.ps1 -Yes       # non-interactive

param(
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'

$ConfigDir = if ($env:CIRCUIT_AGENT_HOME) { $env:CIRCUIT_AGENT_HOME } else { Join-Path $env:USERPROFILE '.circuit-agent' }
$VenvDir   = Join-Path $ConfigDir 'venv'
$ScriptsDir = Join-Path $VenvDir 'Scripts'

Write-Host "==> Circuit-Agent uninstaller"
Write-Host "    config:  $ConfigDir"
Write-Host "    PATH entry: $ScriptsDir"
Write-Host ""

$ConfigExists = Test-Path $ConfigDir
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
$OnPath = $UserPath -and ($UserPath.Split(';') -contains $ScriptsDir)

if (-not $ConfigExists -and -not $OnPath) {
    Write-Host "Nothing to remove - Circuit-CLI doesn't appear to be installed."
    exit 0
}

if (-not $Yes) {
    Write-Host "This will delete:"
    if ($ConfigExists) { Write-Host "  - $ConfigDir (venv, .env credentials, proxy.log)" }
    if ($OnPath)       { Write-Host "  - $ScriptsDir from your user PATH" }
    Write-Host ""
    $ans = Read-Host "Continue? [y/N]"
    if ($ans -notmatch '^(y|Y|yes|YES)$') {
        Write-Host "Aborted."
        exit 0
    }
}

# --- stop running proxy ----------------------------------------------------
$ProxyExe = Join-Path $ScriptsDir 'python.exe'
if (Test-Path $ProxyExe) {
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -eq $ProxyExe -or $_.CommandLine -like "*circuit_agent.proxy*" }
    if ($procs) {
        Write-Host "==> Stopping running circuit-proxy ($($procs.ProcessId -join ', '))"
        foreach ($p in $procs) {
            try { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue } catch {}
        }
        Start-Sleep -Milliseconds 500
    }
}

# --- remove PATH entry -----------------------------------------------------
if ($OnPath) {
    Write-Host "==> Removing $ScriptsDir from user PATH"
    $NewPath = ($UserPath.Split(';') | Where-Object { $_ -ne $ScriptsDir -and $_ -ne "" }) -join ';'
    [Environment]::SetEnvironmentVariable("Path", $NewPath, "User")
}

# --- remove config dir -----------------------------------------------------
if ($ConfigExists) {
    Write-Host "==> Removing $ConfigDir"
    try {
        Remove-Item -Recurse -Force $ConfigDir
    } catch {
        Write-Host "    Warning: $($_.Exception.Message)" -ForegroundColor Yellow
        Write-Host "    Some files may be in use. Close any running circuit-agent windows and retry."
    }
}

Write-Host ""
Write-Host "==> Uninstall complete."
Write-Host ""
Write-Host "    Open a NEW PowerShell window to see PATH changes take effect."
Write-Host ""
$RepoDir = (Resolve-Path "$PSScriptRoot\..").Path
Write-Host "    The cloned repo at $RepoDir was NOT touched."
Write-Host "    Delete it manually if you don't need it anymore."

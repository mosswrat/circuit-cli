# Circuit-Agent installer for Windows.
#   - Creates a venv at $env:CIRCUIT_AGENT_HOME (default: %USERPROFILE%\.circuit-agent\venv)
#   - Installs the circuit-agent package (includes circuit-proxy)
#   - Prompts for Cisco CIRCUIT credentials and writes %USERPROFILE%\.circuit-agent\.env
#
# Usage (in PowerShell):
#   .\install.ps1
# If execution policy blocks it:
#   powershell -ExecutionPolicy Bypass -File .\install.ps1

$ErrorActionPreference = 'Stop'

$RepoDir   = (Resolve-Path "$PSScriptRoot\..").Path
$ConfigDir = if ($env:CIRCUIT_AGENT_HOME) { $env:CIRCUIT_AGENT_HOME } else { Join-Path $env:USERPROFILE '.circuit-agent' }
$VenvDir   = Join-Path $ConfigDir 'venv'
$EnvFile   = Join-Path $ConfigDir '.env'

Write-Host "==> Circuit-Agent installer (Windows)"
Write-Host "    repo:    $RepoDir"
Write-Host "    config:  $ConfigDir"

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
        continue   # candidate exists but failed (e.g. py.exe with no Python installed)
    }
    if ($LASTEXITCODE -eq 0 -and $verCheck -eq '1') {
        $PyExe = $c.Exe
        $PyArgs = $c.Args
        break
    }
}
if (-not $PyExe) {
    Write-Host ""
    Write-Host "ERROR: No working Python 3.10+ found." -ForegroundColor Red
    Write-Host ""
    Write-Host "The Python launcher 'py.exe' may be present but no Python interpreter is registered with it." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Fix:" -ForegroundColor Yellow
    Write-Host "  1. Download Python 3.12 (64-bit) from https://www.python.org/downloads/windows/"
    Write-Host "  2. During install, check 'Add python.exe to PATH'"
    Write-Host "  3. Open a NEW PowerShell window, verify with:  python --version"
    Write-Host "  4. Re-run this installer."
    exit 1
}
$PyVer = & $PyExe @($PyArgs + @('--version'))
Write-Host "    python:  $PyExe $($PyArgs -join ' ')  ($PyVer)"

# --- venv + install ---------------------------------------------------------
if (-not (Test-Path $ConfigDir)) {
    New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
}

if (-not (Test-Path (Join-Path $VenvDir 'Scripts\python.exe'))) {
    Write-Host "==> Creating venv at $VenvDir"
    & $PyExe @($PyArgs + @('-m', 'venv', $VenvDir))
}

$PipExe = Join-Path $VenvDir 'Scripts\pip.exe'
Write-Host "==> Installing circuit-agent into venv"
& $PipExe install --quiet --upgrade pip
& $PipExe install --quiet -e $RepoDir

# --- credentials prompt -----------------------------------------------------
if (Test-Path $EnvFile) {
    Write-Host "==> Existing credentials found at $EnvFile - keeping as-is."
    Write-Host "    (Delete the file and re-run if you want to enter new values.)"
}
else {
    Write-Host ""
    Write-Host "==> Enter your Cisco CIRCUIT credentials"
    Write-Host "    Get them from your Cisco AI portal. They are stored locally only."
    Write-Host ""

    $CID  = Read-Host  "    API Key (CIRCUIT_CLIENT_ID)"
    $sec  = Read-Host  "    Secret  (CIRCUIT_CLIENT_SECRET)" -AsSecureString
    $app  = Read-Host  "    KeyPass (CIRCUIT_APP_KEY)" -AsSecureString
    $CSEC = [System.Net.NetworkCredential]::new('', $sec).Password
    $CAPP = [System.Net.NetworkCredential]::new('', $app).Password

    if (-not $CID -or -not $CSEC -or -not $CAPP) {
        Write-Error "All three values are required."
        exit 1
    }

    $body = @"
# Cisco CIRCUIT API credentials - keep this file private
CIRCUIT_CLIENT_ID=$CID
CIRCUIT_CLIENT_SECRET=$CSEC
CIRCUIT_APP_KEY=$CAPP
CIRCUIT_MODEL=gpt-5-nano
"@
    Set-Content -Path $EnvFile -Value $body -Encoding UTF8
    Write-Host "    Wrote $EnvFile"
    # Restrict ACL to current user
    try {
        $acl = Get-Acl $EnvFile
        $acl.SetAccessRuleProtection($true, $false)
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $env:USERNAME, 'FullControl', 'Allow'
        )
        $acl.AddAccessRule($rule)
        Set-Acl -Path $EnvFile -AclObject $acl
    } catch {
        Write-Host "    (ACL restriction skipped: $($_.Exception.Message))"
    }
}

# --- done -------------------------------------------------------------------
$AgentExe = Join-Path $VenvDir 'Scripts\circuit-agent.exe'
$ProxyExe = Join-Path $VenvDir 'Scripts\circuit-proxy.exe'

Write-Host ""
Write-Host "==> Installation complete."
Write-Host ""
Write-Host "    Start the proxy (one terminal):  $ProxyExe"
Write-Host "    Run the agent  (another):        $AgentExe"
Write-Host ""
Write-Host "    Or activate the venv first:"
Write-Host "        $VenvDir\Scripts\Activate.ps1"
Write-Host "        circuit-proxy"
Write-Host "        circuit-agent"

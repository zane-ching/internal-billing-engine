# Claude Code usage-billing client - Windows installer (opt-in).
#
#   .\install.ps1 -Token <TOKEN> -Endpoint https://receiver.example.com:4318
#   .\install.ps1 -Uninstall
#   .\install.ps1 -Verify -Endpoint https://receiver.example.com:4318
#
# Thin shim: finds a real Python 3 and hands off to configure.py, which does the
# settings merge. Everything installs under your user profile; nothing needs
# administrator rights. See INSTRUCTIONS.md.
#
# If billing-config.json sits next to this script, -Token and -Endpoint are
# optional - the packaged values are used. That is how Install.bat runs with no
# arguments; most people should just double-click that instead of running this.
#
# If PowerShell blocks the script, run it for this session only:
#     powershell -ExecutionPolicy Bypass -File .\install.ps1 -Token ... -Endpoint ...

[CmdletBinding()]
param(
    [string] $Token,
    [string] $Endpoint,
    [switch] $Uninstall,
    [switch] $Verify,
    # Permit a plaintext http:// endpoint. Not for fleet use.
    [switch] $AllowInsecure,
    # Print what is collected and wait for a keystroke before writing anything.
    # Install.bat passes this, because a double-click has no other way to stop.
    [switch] $Interactive,
    [switch] $DryRun
)

$ErrorActionPreference = 'Stop'
$srcDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Find-Python {
    # Prefer a real install. The Microsoft Store aliases under WindowsApps are
    # stubs: they print an install advertisement and exit non-zero when run
    # non-interactively, so a hook registered against them never fires.
    $candidates = @()
    $candidates += Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
    $candidates += Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
    $candidates += Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'
    $candidates += 'C:\Program Files\Python313\python.exe'
    $candidates += 'C:\Program Files\Python312\python.exe'
    $candidates += 'C:\Program Files\Python311\python.exe'

    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    foreach ($name in @('python', 'python3', 'py')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -and $cmd.Source -notlike '*\WindowsApps\*') {
            return $cmd.Source
        }
    }
    return $null
}

$python = Find-Python
if (-not $python) {
    Write-Host ''
    Write-Host 'No real Python 3 was found. The repo-tag hook is a Python script and needs one.' -ForegroundColor Red
    Write-Host 'Install it (no administrator rights required):'
    Write-Host ''
    Write-Host '    winget install --id Python.Python.3.12 --scope user' -ForegroundColor Yellow
    Write-Host ''
    Write-Host 'Then re-run this installer. Note: the python.exe under'
    Write-Host 'AppData\Local\Microsoft\WindowsApps is a Store alias stub, not a usable Python.'
    exit 1
}

$configure = Join-Path $srcDir 'configure.py'
if (-not (Test-Path $configure)) {
    Write-Host "configure.py not found next to this script - is the package intact?" -ForegroundColor Red
    exit 1
}

# A packaged zip carries the endpoint and token, so the flags become optional.
# configure.py reads the file itself; this only decides whether to fail early
# with a readable message.
$baked = Test-Path (Join-Path $srcDir 'billing-config.json')

# Build the argument list for configure.py.
if ($Uninstall) {
    $callArgs = @($configure, 'uninstall')
    if ($DryRun) { $callArgs += '--dry-run' }
} elseif ($Verify) {
    if (-not $Endpoint -and -not $baked) {
        Write-Host 'Verify needs -Endpoint (this package has no billing-config.json).' -ForegroundColor Red
        exit 1
    }
    $callArgs = @($configure, 'verify')
    if ($Endpoint) { $callArgs += @('--endpoint', $Endpoint) }
    if ($Token)    { $callArgs += @('--token', $Token) }
} else {
    if (-not $baked) {
        if (-not $Token)    { Write-Host 'Install needs -Token (get it from the billing owner).' -ForegroundColor Red; exit 1 }
        if (-not $Endpoint) { Write-Host 'Install needs -Endpoint (https://... receiver URL).'   -ForegroundColor Red; exit 1 }
    }
    $callArgs = @($configure, 'install')
    if ($Token)         { $callArgs += @('--token', $Token) }
    if ($Endpoint)      { $callArgs += @('--endpoint', $Endpoint) }
    if ($AllowInsecure) { $callArgs += '--allow-insecure' }
    if ($Interactive)   { $callArgs += '--interactive' }
    if ($DryRun)        { $callArgs += '--dry-run' }
}

& $python @callArgs
exit $LASTEXITCODE

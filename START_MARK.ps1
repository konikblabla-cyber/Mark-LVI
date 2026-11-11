[CmdletBinding()]
param(
    [switch]$InstallOnly
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$Marker = Join-Path $Root ".venv\.mark-setup-signature"

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Label
    )
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE."
    }
}

function Find-Python {
    $launcher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($launcher) {
        foreach ($version in @("3.12", "3.11", "3.13")) {
            # MARK's audio and Qt wheels are shipped for 64-bit Windows only.
            & $launcher.Source "-$version" -c "import struct,sys; sys.exit(0 if struct.calcsize('P') == 8 else 2)" *> $null
            if ($LASTEXITCODE -eq 0) {
                return @{
                    Executable = $launcher.Source
                    Prefix = @("-$version")
                    Version = $version
                }
            }
        }
    }

    $python = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if ($python) {
        $pythonInfo = & $python.Source -c "import struct,sys; print('Python %d.%d %d-bit' % (sys.version_info[0], sys.version_info[1], struct.calcsize('P') * 8))" 2>&1
        if ($LASTEXITCODE -eq 0 -and "$pythonInfo" -match "^Python 3\.(11|12|13) 64-bit$") {
            return @{
                Executable = $python.Source
                Prefix = @()
                Version = ($Matches[0] -replace "^Python ", "" -replace " 64-bit$", "")
            }
        }
    }

    throw @"
Python 3.11, 3.12 or 3.13 (64-bit) was not found.
Install Python 3.12 from https://www.python.org/downloads/windows/
and enable "Python Launcher" during setup. Then run this file again.
"@
}

function Invoke-SelectedPython {
    param([string[]]$Arguments)
    $allArgs = @($script:Python.Prefix) + $Arguments
    Invoke-Checked -Executable $script:Python.Executable -Arguments $allArgs -Label "Python setup"
}

try {
    Set-Location $Root
    Write-Host ""
    Write-Host "  MARK LVI - Windows setup" -ForegroundColor Cyan
    Write-Host "  Preparing a private Python environment. Your system Python is not modified." -ForegroundColor Gray

    $script:Python = Find-Python
    Write-Host ("  Python " + $script:Python.Version + " found.") -ForegroundColor Green

    if (-not (Test-Path $VenvPython)) {
        Write-Host "  Creating .venv ..." -ForegroundColor Cyan
        Invoke-SelectedPython -Arguments @("-m", "venv", ".venv")
    }

    if (-not (Test-Path $VenvPython)) {
        throw "The virtual environment was not created: $VenvPython"
    }

    $requirements = Join-Path $Root "requirements.txt"
    $setup = Join-Path $Root "setup.py"
    if (-not (Test-Path $requirements) -or -not (Test-Path $setup)) {
        throw "This download is incomplete: requirements.txt or setup.py is missing."
    }

    $signature = (Get-FileHash $requirements -Algorithm SHA256).Hash +
                 (Get-FileHash $setup -Algorithm SHA256).Hash
    $installedSignature = if (Test-Path $Marker) {
        (Get-Content $Marker -Raw -ErrorAction SilentlyContinue).Trim()
    } else {
        ""
    }

    if ($installedSignature -ne $signature) {
        Write-Host "  Installing MARK dependencies and browser engines ..." -ForegroundColor Cyan
        Invoke-Checked -Executable $VenvPython -Arguments @("setup.py") -Label "MARK dependency setup"
        Set-Content -Path $Marker -Value $signature -Encoding ASCII
    } else {
        Write-Host "  Dependencies already match this version. Skipping reinstall." -ForegroundColor Green
    }

    if ($InstallOnly) {
        Write-Host ""
        Write-Host "  Installation complete. Double-click START_MARK.bat to launch JARVIS." -ForegroundColor Green
        exit 0
    }

    Write-Host ""
    Write-Host "  Starting MARK LVI ..." -ForegroundColor Green
    & $VenvPython (Join-Path $Root "main.py")
    if ($LASTEXITCODE -ne 0) {
        throw "MARK exited with code $LASTEXITCODE."
    }
}
catch {
    Write-Host ""
    Write-Host "  MARK could not start:" -ForegroundColor Red
    Write-Host ("  " + $_.Exception.Message) -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Check your Python installation and internet connection, then retry." -ForegroundColor Gray
    Read-Host "Press Enter to close"
    exit 1
}

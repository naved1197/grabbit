# Builds dist\Grabbit-Setup-<version>.exe on a 64-bit Windows PC.
# Needs uv (https://docs.astral.sh/uv/) and Inno Setup 6 (winget install JRSoftware.InnoSetup).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Run([string]$exe, [string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "$exe $($arguments -join ' ') failed with exit code $LASTEXITCODE" }
}

Run uv @("sync", "--locked")
Run uv @("run", "python", "packaging/fetch_tools.py")
Run uv @("run", "pyinstaller", "--noconfirm", "--clean", "--distpath", "dist", "--workpath", "build/pyinstaller", "packaging/grabbit.spec")

$version = (& uv run python -c "import grabbit_desktop as g; print(g.VERSION)").Trim()

$iscc = $null
$cmd = Get-Command iscc.exe -ErrorAction SilentlyContinue
if ($cmd) { $iscc = $cmd.Source }
foreach ($candidate in @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")) {
    if (-not $iscc -and (Test-Path $candidate)) { $iscc = $candidate }
}
if (-not $iscc) { throw "Inno Setup 6 not found. Install it with: winget install JRSoftware.InnoSetup" }

Run $iscc @("/Qp", "/DAppVersion=$version", "packaging\windows.iss")
Write-Host "Built dist\Grabbit-Setup-$version.exe"

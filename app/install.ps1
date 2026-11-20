param([string]$CoreWheel, [string]$CyUISource, [switch]$BuildLauncher)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root 'runtime/python/Scripts/python.exe'
if (-not $CoreWheel) {
    $CoreWheel = Get-ChildItem -Path "$root/runtime/packages/cytools_core-0.12.0-*-win_amd64.whl" -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $CoreWheel) { throw 'Supply the CyToolsCore 0.12.0 Windows wheel with -CoreWheel.' }
if (-not $CyUISource) {
    $CyUISource = Get-ChildItem -Path "$root/runtime/packages/cyui-1.2.0-*.whl" -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
    if (-not $CyUISource) { $CyUISource = 'cyui @ git+https://github.com/MrMybal/CyUI.git@6ef01cfcdb197f3a89d64618ce5ef8818c704728' }
}
if (-not (Test-Path -LiteralPath $python)) {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) { & py -3.11 -m venv (Join-Path $root 'runtime/python') }
    else { & python -m venv (Join-Path $root 'runtime/python') }
    if ($LASTEXITCODE -ne 0) { throw 'Creating the private Python environment failed.' }
}
& $python -m pip install --disable-pip-version-check $CoreWheel $CyUISource -r (Join-Path $PSScriptRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Installing the VideoStudio desktop dependencies failed.' }
if ($BuildLauncher) { & (Join-Path $PSScriptRoot 'build_launcher.ps1') }

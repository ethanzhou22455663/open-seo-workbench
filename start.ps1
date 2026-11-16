param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
$workbenchExe = Join-Path $projectRoot '.venv\Scripts\seo-workbench.exe'
Set-Location -LiteralPath $projectRoot
if (-not $env:SEO_PLATFORM_DATA_DIR -and (Test-Path -LiteralPath (Join-Path $projectRoot 'data\registry.sqlite3'))) {
    $env:SEO_PLATFORM_DATA_DIR = Join-Path $projectRoot 'data'
}

if (-not (Test-Path -LiteralPath $pythonExe)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m venv (Join-Path $projectRoot '.venv')
    } else {
        & python -m venv (Join-Path $projectRoot '.venv')
    }
}
if (-not (Test-Path -LiteralPath $workbenchExe)) {
    & $pythonExe -m pip install -e $projectRoot
}
Push-Location (Join-Path $projectRoot 'frontend')
try {
    if (-not (Test-Path -LiteralPath 'node_modules')) { npm ci }
    npm run build
} finally { Pop-Location }

$arguments = @('serve')
if ($NoBrowser) { $arguments += '--no-browser' }
& $workbenchExe @arguments

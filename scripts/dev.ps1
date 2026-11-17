<#
.SYNOPSIS
  Start the local dev stack: Postgres (WSL), backend (uvicorn), frontend (Vite).

.DESCRIPTION
  Backend and frontend each open in their own PowerShell window; close a window (or press
  Ctrl+C in it) to stop that server. Temp files and caches stay on E: (see CLAUDE.md).

.EXAMPLE
  .\scripts\dev.ps1          # then open http://localhost:5173
  .\scripts\db.ps1 stop      # when you are done for the day
#>
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$tmp = Join-Path $root '.tmp'
New-Item -ItemType Directory -Force $tmp | Out-Null

& (Join-Path $PSScriptRoot 'db.ps1') start
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$envSetup = "`$env:TEMP='$tmp'; `$env:TMP='$tmp'; `$env:UV_CACHE_DIR='E:\ai-agent-portfolio\.uv-cache'; `$env:npm_config_cache='E:\ai-agent-portfolio\.npm-cache'; Remove-Item Env:VIRTUAL_ENV -ErrorAction SilentlyContinue"

Start-Process powershell -WorkingDirectory (Join-Path $root 'backend') -ArgumentList @(
    '-NoExit', '-Command',
    "$envSetup; `$host.UI.RawUI.WindowTitle='DataSage backend :8000'; uv run alembic upgrade head; uv run uvicorn app.main:app --port 8000"
)
Start-Process powershell -WorkingDirectory (Join-Path $root 'frontend') -ArgumentList @(
    '-NoExit', '-Command',
    "$envSetup; `$host.UI.RawUI.WindowTitle='DataSage frontend :5173'; npm run dev -- --port 5173"
)

Write-Output 'Starting backend (http://localhost:8000) and frontend (http://localhost:5173)...'

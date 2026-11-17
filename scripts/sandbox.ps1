<#
.SYNOPSIS
  Start/stop Docker Engine inside WSL and build the sandbox image.

.DESCRIPTION
  Docker Engine is installed in the WSL distro (on E:) but disabled by default.
  `start` keeps WSL alive (via db.ps1) and starts Docker. `build` also builds
  datasage-sandbox:latest from sandbox-image/. `stop` stops Docker again.

.EXAMPLE
  .\scripts\sandbox.ps1 build
  .\scripts\sandbox.ps1 stop
#>
param(
    [ValidateSet('start', 'build', 'stop')]
    [string]$Action = 'start'
)
# No $ErrorActionPreference = 'Stop': in PowerShell 5.1 it aborts on any stderr line from
# docker (even warnings). Each native call checks $LASTEXITCODE instead.
$Distro = 'Ubuntu-24.04'
$root = Split-Path -Parent $PSScriptRoot

if ($Action -eq 'stop') {
    wsl -d $Distro -u root -- systemctl stop docker docker.socket
    Write-Output 'Docker stopped.'
    exit 0
}

& (Join-Path $PSScriptRoot 'db.ps1') start | Out-Null  # keeps the WSL VM running
wsl -d $Distro -u root -- systemctl start docker
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output 'Docker is running in WSL.'

if ($Action -eq 'build') {
    $src = wsl -d $Distro -e wslpath -a (($root -replace '\\', '/') + '/sandbox-image')
    # BuildKit (buildx) isn't installed in WSL; the legacy builder is enough here.
    wsl -d $Distro -u root -e env DOCKER_BUILDKIT=0 docker build -t datasage-sandbox:latest $src
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

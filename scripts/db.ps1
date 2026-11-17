<#
.SYNOPSIS
  Start/stop/check the local PostgreSQL 16 that runs inside the WSL distro (on E:).

.DESCRIPTION
  WSL stops its VM when no WSL session is open, which also stops Postgres.
  `start` opens a hidden keep-alive session so Postgres stays up on 127.0.0.1:5432.
  `stop` closes it (WSL then shuts down on its own after a short idle period).

.EXAMPLE
  .\scripts\db.ps1 start
  .\scripts\db.ps1 status
  .\scripts\db.ps1 stop
#>
param(
    [ValidateSet('start', 'stop', 'status')]
    [string]$Action = 'start'
)

$Distro = 'Ubuntu-24.04'
$Marker = 'datasage-db-keepalive'

function Get-Keepalive {
    Get-CimInstance Win32_Process -Filter "Name='wsl.exe'" |
        Where-Object { $_.CommandLine -like "*$Marker*" }
}

function Test-Port {
    $c = New-Object Net.Sockets.TcpClient
    try { $c.Connect('127.0.0.1', 5432); return $true } catch { return $false } finally { $c.Close() }
}

switch ($Action) {
    'start' {
        if (-not (Get-Keepalive)) {
            # One pre-quoted string: Start-Process does not quote array items containing spaces.
            Start-Process -WindowStyle Hidden wsl.exe `
                -ArgumentList "-d $Distro -u root -- bash -c `"exec -a $Marker sleep infinity`"" | Out-Null
        }
        for ($i = 0; $i -lt 30; $i++) {
            if (Test-Port) { Write-Output 'Postgres is up on 127.0.0.1:5432'; exit 0 }
            Start-Sleep -Seconds 1
        }
        Write-Error 'Postgres did not come up within 30s. Check: wsl -d Ubuntu-24.04 -u root -- systemctl status postgresql'
        exit 1
    }
    'stop' {
        Get-Keepalive | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
        Write-Output 'Keep-alive stopped (WSL will shut down when idle).'
    }
    'status' {
        $k = if (Get-Keepalive) { 'running' } else { 'not running' }
        $p = if (Test-Port) { 'open' } else { 'closed' }
        Write-Output "keep-alive: $k | 127.0.0.1:5432: $p"
    }
}

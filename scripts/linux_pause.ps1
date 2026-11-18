<#
.SYNOPSIS
  Freeze the Python side of the running App (docker pause). Heartbeats stop, the web page freezes,
  the MCU keeps running and fails safe. Undo instantly with linux_restore.ps1.
#>
. "$PSScriptRoot\_board.ps1"
Assert-BoardTools
$c = Get-MainContainer
if (-not $c) { Write-Host "No running Python container (label service=main) found."; exit 1 }
Invoke-Board "docker pause $c" | Out-Host
Write-Host ("Paused Python container {0} at {1:HH:mm:ss.fff}" -f $c, (Get-Date))

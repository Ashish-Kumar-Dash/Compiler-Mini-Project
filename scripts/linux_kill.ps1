<#
.SYNOPSIS
  Hard-kill the Python side (Linux "brain") of the running App with docker kill.
  The MCU keeps running: within the failsafe timeout it applies the FAILSAFE states while its own
  rules keep working. Restore with linux_restore.ps1 (never with App Lab: that resets the MCU).
#>
. "$PSScriptRoot\_board.ps1"
Assert-BoardTools
$c = Get-MainContainer
if (-not $c) { Write-Host "No running Python container (label service=main) found."; exit 1 }
$sw = [Diagnostics.Stopwatch]::StartNew()
Invoke-Board "docker kill $c" | Out-Host
Write-Host ("Killed Python container {0} at {1:HH:mm:ss.fff} (command took {2} ms)" -f $c, (Get-Date), $sw.ElapsedMilliseconds)

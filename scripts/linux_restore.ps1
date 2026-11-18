<#
.SYNOPSIS
  Bring the Python side back WITHOUT resetting the MCU: docker unpause (if paused) or docker start
  (if killed/exited). Never use App Lab Run/Stop or arduino-app-cli app start for this: they reset
  the MCU (Architecture ADR-25).
#>
. "$PSScriptRoot\_board.ps1"
Assert-BoardTools
$rows = Get-BoardOutput "docker ps -a --filter $($script:MainLabel) --format {{.ID}}:{{.State}}"
if (-not $rows) { Write-Host "No Python container (label service=main) exists. Start the App normally."; exit 1 }
foreach ($row in ($rows -split "`n")) {
    $parts = $row.Trim() -split ":"
    $id = $parts[0]; $st = $parts[1]
    switch ($st) {
        "paused" { Invoke-Board "docker unpause $id" | Out-Host; Write-Host "Unpaused $id" }
        "running" { Write-Host "$id is already running" }
        default { Invoke-Board "docker start $id" | Out-Host; Write-Host "Started $id (was $st)" }
    }
}
Write-Host ("Restore issued at {0:HH:mm:ss.fff}" -f (Get-Date))

<#
.SYNOPSIS
  Copy the built-in App Lab examples the back end is templated on (Architecture §11.7) from the
  board into iotc/backend/templates/: blink-with-ui (app.yaml, sketch.yaml, assets/libs) and
  home-climate-monitoring-and-storage (Chart.js).
#>
. "$PSScriptRoot\_board.ps1"
Assert-BoardTools
$dest = Join-Path $PSScriptRoot "..\iotc\backend\templates"
New-Item -ItemType Directory -Force $dest | Out-Null
foreach ($ex in @("blink-with-ui", "home-climate-monitoring-and-storage")) {
    $remote = Get-BoardOutput "find /var/lib/arduino-app-cli/examples -maxdepth 5 -type d -name $ex 2>/dev/null | head -1"
    if (-not $remote) { Write-Host "Example $ex not found on the board (looked in /var/lib/arduino-app-cli/examples)"; continue }
    $local = Join-Path $dest $ex
    if (Test-Path $local) { Remove-Item -Recurse -Force $local }
    Copy-FromBoard $remote $dest
    Write-Host "Copied $remote -> $local"
}
Get-ChildItem -Recurse $dest | Select-Object FullName, Length | Format-Table -AutoSize

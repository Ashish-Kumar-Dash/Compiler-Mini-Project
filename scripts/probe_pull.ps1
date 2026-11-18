<#
.SYNOPSIS
  Copy the probe report (data/probe_report.json) from the board into hardware/phase0/results/.
#>
param([string]$Name = "iotc_probe")
. "$PSScriptRoot\_board.ps1"
Assert-BoardTools
$dest = Join-Path $PSScriptRoot "..\hardware\phase0\results"
Copy-FromBoard "$($script:AppsDir)/$Name/data/probe_report.json" $dest
Write-Host "Report copied to $dest\probe_report.json"

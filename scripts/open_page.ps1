<#
.SYNOPSIS
  Open the running App's web page (WebUI, port 7000) on this laptop.

  Over USB (default) the page is tunnelled through adb, so it works with NO network at all:
      adb forward tcp:7000 tcp:7000   ->   http://localhost:7000
  Over ssh it opens http://<board>:7000 (the board must be on the same Wi-Fi as the laptop).

.NOTES
  `hostname -I` on the board printing only 172.17.x / 172.18.x means the board has no Wi-Fi/LAN
  address (those are Docker-internal); use the adb tunnel.
#>
param([int]$Port = 7000, [switch]$NoBrowser)
. "$PSScriptRoot\_board.ps1"
Assert-BoardTools
if ($script:Via -eq "adb") {
    & adb forward "tcp:$Port" "tcp:$Port" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "adb forward failed" }
    $url = "http://localhost:$Port"
    Write-Host "Tunnel ready: $url  (board port $Port over USB)"
} else {
    $url = "http://$($script:BoardHost):$Port"
}
try {
    $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 $url
    Write-Host "Page answers: HTTP $($r.StatusCode)"
} catch {
    Write-Host "Page does not answer yet ($($_.Exception.Message)). Is the App running? Try again in a few seconds."
}
if (-not $NoBrowser) { Start-Process $url }

<#
.SYNOPSIS
  Copy an App folder to the UNO Q and start it with arduino-app-cli (fallback when App Lab is slow).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 hardware\phase0\probe
  powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 build\greenhouse_board -NoFollow

.NOTES
  Starting an App stops the running one first, which RESETS the MCU (fine during deployment; never
  use this to "restore Linux" during the demo — use linux_restore.ps1, Architecture ADR-25).
#>
param(
    [Parameter(Mandatory = $true)][string]$AppDir,
    [string]$Name = "",
    [switch]$NoFollow,
    [switch]$NoStart
)
. "$PSScriptRoot\_board.ps1"
Assert-BoardTools

$AppDir = (Resolve-Path $AppDir).Path
if (-not (Test-Path (Join-Path $AppDir "app.yaml"))) { throw "$AppDir has no app.yaml - not an App folder" }
$leaf = Split-Path $AppDir -Leaf
if (-not $Name) { $Name = if ($leaf -like "iotc*") { $leaf } else { "iotc_$leaf" } }
$Name = $Name.ToLowerInvariant() -replace '[^a-z0-9_-]', '_'
$dest = "$($script:AppsDir)/$Name"
$tmp = "/tmp/iotc_push_$Name"
$appId = Get-AppId $Name

Write-Host "== Deploying $AppDir -> $dest ($appId) via $($script:Via)"

Write-Host "== Apps on the board before deploy:"
Invoke-Board "arduino-app-cli app list" | Out-Host

Write-Host "== Stopping $appId if it is running (resets the MCU)"
Invoke-Board "arduino-app-cli app stop $appId" | Out-Host

Invoke-Board "rm -rf $tmp" | Out-Null
Copy-ToBoard $AppDir $tmp
Invoke-Board "mkdir -p $($script:AppsDir) && rm -rf $dest && mv $tmp $dest && find $dest -name __pycache__ -type d -prune -exec rm -rf {} + ; chown -R arduino:arduino $dest 2>/dev/null; true" | Out-Host
Write-Host "== Copied. Board folder:"
Invoke-Board "ls -la $dest" | Out-Host

if ($NoStart) { exit 0 }

Write-Host "== Starting $appId (the first start compiles the sketch on the board: can take 1-3 min)"
$sw = [Diagnostics.Stopwatch]::StartNew()
Invoke-Board "arduino-app-cli app start $appId" | Out-Host
$rc = $LASTEXITCODE
$sw.Stop()
Write-Host ("== app start exit code {0} after {1:N1} s" -f $rc, $sw.Elapsed.TotalSeconds)
if ($rc -ne 0) {
    Write-Host "If another App is running, stop it in App Lab (or: arduino-app-cli app list / app stop user:<name>) and retry."
    exit $rc
}

if ($script:Via -eq "adb") {
    & adb forward tcp:7000 tcp:7000 | Out-Null
    Write-Host "== Web page (if the App has one): http://localhost:7000  (tunnelled over USB by adb)"
}

if (-not $NoFollow) {
    Write-Host "== Following Python logs (Ctrl+C to stop following; the App keeps running)"
    Invoke-Board "arduino-app-cli app logs $appId --follow"
}

<#
.SYNOPSIS
  Record board software versions into hardware/phase0/results/versions.txt (Architecture §19).
#>
. "$PSScriptRoot\_board.ps1"
Assert-BoardTools
$out = Join-Path $PSScriptRoot "..\hardware\phase0\results\versions.txt"
$cmds = @(
    "arduino-app-cli version",
    "arduino-app-cli app list",
    "uname -a",
    "head -3 /etc/os-release",
    "systemctl is-active arduino-router",
    "dpkg -l | grep -i arduino",
    "docker images --format {{.Repository}}:{{.Tag}}",
    "find / -xdev -name platform.txt -path *zephyr* 2>/dev/null | head -5",
    "find / -xdev -name library.properties -path *RouterBridge* 2>/dev/null -exec grep -H ^version= {} + | head -5",
    "find / -xdev -name library.properties -path *RPClite* 2>/dev/null -exec grep -H ^version= {} + | head -5",
    "find / -xdev -name library.properties -path *LED_Matrix* 2>/dev/null -exec grep -H ^version= {} + | head -5",
    "hostname; hostname -I",
    "ip -brief addr",
    "nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device 2>/dev/null"
)
$lines = @("# iotc board versions, $(Get-Date -Format s), via $($script:Via)")
foreach ($c in $cmds) {
    $lines += "`n## $c"
    $lines += (Get-BoardOutput $c)
}
$lines -join "`n" | Set-Content -Encoding utf8 $out
Write-Host "Wrote $out"
Get-Content $out

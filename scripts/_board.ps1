# Shared helpers: talk to the Arduino UNO Q over adb (USB, default) or ssh (network).
# Configure once per shell:
#   $env:IOTC_BOARD_VIA  = "adb"            # or "ssh"
#   $env:IOTC_BOARD_HOST = "myboard.local"  # ssh only: board name or IP
# Dot-source from other scripts:  . "$PSScriptRoot\_board.ps1"

$ErrorActionPreference = "Stop"
$script:Via = if ($env:IOTC_BOARD_VIA) { $env:IOTC_BOARD_VIA } else { "adb" }
$script:BoardHost = $env:IOTC_BOARD_HOST
$script:AppsDir = "/home/arduino/ArduinoApps"
$script:MainLabel = "label=com.docker.compose.service=main"

function Assert-BoardTools {
    if ($script:Via -eq "adb") {
        if (-not (Get-Command adb -ErrorAction SilentlyContinue)) {
            throw "adb not found. Install it with:  winget install Google.PlatformTools  (then open a new terminal)"
        }
        $devices = (& adb devices) -join "`n"
        if ($devices -notmatch "`tdevice") {
            throw "No board found by adb. Connect the UNO Q by USB-C, wait for it to boot, then run 'adb devices'."
        }
    } elseif ($script:Via -eq "ssh") {
        if (-not $script:BoardHost) { throw "Set `$env:IOTC_BOARD_HOST to the board name (e.g. myboard.local) or its IP." }
        if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) { throw "ssh not found (Windows: Settings > Optional features > OpenSSH Client)." }
    } else {
        throw "IOTC_BOARD_VIA must be 'adb' or 'ssh' (got '$($script:Via)')."
    }
}

# Run one shell command on the board; output is passed through. Check $LASTEXITCODE afterwards.
function Invoke-Board([string]$Command) {
    if ($script:Via -eq "adb") { & adb shell $Command }
    else { & ssh "arduino@$($script:BoardHost)" $Command }
}

# Same, but return the output as one trimmed string.
function Get-BoardOutput([string]$Command) {
    return ((Invoke-Board $Command) | Out-String).Trim()
}

function Copy-ToBoard([string]$Local, [string]$Remote) {
    if ($script:Via -eq "adb") { & adb push $Local $Remote | Out-Host }
    else { & scp -r $Local "arduino@$($script:BoardHost):$Remote" | Out-Host }
    if ($LASTEXITCODE -ne 0) { throw "copy to board failed: $Local -> $Remote" }
}

function Copy-FromBoard([string]$Remote, [string]$Local) {
    if ($script:Via -eq "adb") { & adb pull $Remote $Local | Out-Host }
    else { & scp -r "arduino@$($script:BoardHost):$Remote" $Local | Out-Host }
    if ($LASTEXITCODE -ne 0) { throw "copy from board failed: $Remote -> $Local" }
}

# The Python side of the running App is the compose service 'main' (Architecture §19).
function Get-MainContainer([switch]$All) {
    $flag = if ($All) { "-a " } else { "" }
    return Get-BoardOutput "docker ps $flag-q --filter $($script:MainLabel)"
}

function Get-AppId([string]$Name) { return "user:$Name" }

# browser_guard.ps1
$ErrorActionPreference = "SilentlyContinue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$TargetUrl = "https://plus.buy.139.com/cloudappadmin/#/cloudAppChannelManager"
$profileDir = Join-Path $ScriptDir "browser-profile"

$chromePaths = @("C:\Program Files\Google\Chrome\Application\chrome.exe", "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe")
$edgePaths = @("C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "C:\Program Files\Microsoft\Edge\Application\msedge.exe")

# Already running?
try {
    $check = Invoke-WebRequest -Uri "http://127.0.0.1:9222/json/version" -UseBasicParsing -TimeoutSec 3
    if ($check.StatusCode -eq 200) { exit 0 }
} catch { <# not running, continue #> }

function Invoke-Browser($browserPath, $browserName, $profileDir) {
    $launchArgs = @(
        "--remote-debugging-port=9222",
        "--user-data-dir=`"$profileDir`"",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-default-apps",
        "--disable-sync",
        "--disable-popup-blocking",
        $TargetUrl
    )
    Start-Process $browserPath -ArgumentList $launchArgs
    Start-Sleep -Seconds 5

    # CDP responding?
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:9222/json/version" -UseBasicParsing -TimeoutSec 5
        if ($r.StatusCode -ne 200) { return $false }
    } catch { return $false }

    # Has 139 page?
    $has139 = $false
    try {
        $pages = (Invoke-WebRequest -Uri "http://127.0.0.1:9222/json" -UseBasicParsing -TimeoutSec 5).Content | ConvertFrom-Json
        foreach ($pg in $pages) {
            if ($pg.url -match "139|plus.buy") { $has139 = $true; break }
        }
    } catch { <# ignore #> }

    if (-not $has139) {
        # Try opening new tab via CDP
        $encoded = [uri]::EscapeDataString($TargetUrl)
        try { Invoke-WebRequest -Uri "http://127.0.0.1:9222/json/new?$encoded" -Method Put -UseBasicParsing -TimeoutSec 5 | Out-Null } catch { <# ignore #> }
        Start-Sleep -Seconds 4
        try {
            $pages2 = (Invoke-WebRequest -Uri "http://127.0.0.1:9222/json" -UseBasicParsing -TimeoutSec 5).Content | ConvertFrom-Json
            foreach ($pg in $pages2) {
                if ($pg.url -match "139|plus.buy") { $has139 = $true; break }
            }
        } catch { <# ignore #> }
    }

    if (-not $has139) {
        Write-Output "BROWSER_GUARD: $browserName stuck on welcome page"
        $procName = if ($browserName -eq "Chrome") { "chrome" } else { "msedge" }
        $procs = Get-CimInstance Win32_Process -Filter "Name='${procName}.exe'"
        foreach ($p in $procs) {
            if ($p.CommandLine -match "9222|browser-profile") {
                Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            }
        }
        Start-Sleep -Seconds 2
        return $false
    }

    Write-Output "BROWSER_GUARD: $browserName started OK"
    return $true
}

$success = $false

# Try Chrome first
$chromePath = $null
foreach ($p in $chromePaths) { if (Test-Path $p) { $chromePath = $p; break } }
if ($chromePath) {
    $success = Invoke-Browser $chromePath "Chrome" $profileDir
}

# Fallback to Edge
if (-not $success) {
    $edgePath = $null
    foreach ($p in $edgePaths) { if (Test-Path $p) { $edgePath = $p; break } }
    if ($edgePath) {
        $success = Invoke-Browser $edgePath "Edge" $profileDir
    }
    if (-not $success) {
        Write-Output "BROWSER_GUARD: all browsers failed"
        exit 1
    }
}


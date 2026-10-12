# Build smoke test. Runs on the CI Windows runner against freshly built executables.
#   -Dist <folder>       : checks the PyInstaller folder (receiver, pages, CLI, hook exe, connect/disconnect)
#   -Installer <setup>   : silent install -> checks files and settings.json -> silent uninstall -> checks cleanup
# The user profile is redirected to a temp folder so the runner's real settings are never touched.
# Comments are in English: Windows PowerShell on CI reads this file without a BOM.
param([string]$Dist, [string]$Installer)
$ErrorActionPreference = 'Stop'

function Assert($cond, $msg) { if (-not $cond) { throw "FAILED: $msg" } else { "ok  $msg" } }

$profile_ = Join-Path $env:RUNNER_TEMP ("um-profile-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force "$profile_\.claude" | Out-Null
'{"theme": "dark"}' | Set-Content -Encoding utf8 "$profile_\.claude\settings.json"
$env:USERPROFILE = $profile_
$env:USAGE_MONITOR_HOME = "$profile_\.claude\usage-monitor"
$settings = "$profile_\.claude\settings.json"

if ($Dist) {
    $cli = Join-Path $Dist 'usage-monitor.exe'
    $hook = Join-Path $Dist 'um-hook.exe'
    Assert (Test-Path (Join-Path $Dist 'UsageMonitor.exe')) 'UsageMonitor.exe exists'
    Assert ((Get-Item $cli).VersionInfo.ProductName -eq 'Usage Monitor') 'product name metadata'

    # receiver + bundled pages
    $env:USAGE_MONITOR_PORT = '4399'
    $recv = Start-Process $cli -ArgumentList 'receiver' -PassThru -WindowStyle Hidden
    try {
        $up = $false
        foreach ($i in 1..40) { try { Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:4399/api/snapshot' | Out-Null; $up = $true; break } catch { Start-Sleep -Milliseconds 500 } }
        Assert $up 'receiver answers /api/snapshot'
        foreach ($page in '/', '/web/widget.html', '/web/wall.html', '/web/app.js') {
            Assert ((Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:4399$page").StatusCode -eq 200) "page $page"
        }

        # hook exe: statusline writes the spool and prints the status text
        $sample = '{"session_id":"ci","model":{"id":"claude-opus-5","display_name":"Opus 5"},"rate_limits":{"five_hour":{"used_percentage":4,"resets_at":4102444800},"seven_day":{"used_percentage":16,"resets_at":4102444800}},"context_window":{"used_percentage":7}}'
        $out = $sample | & $hook statusline
        Assert ($out -eq 'Opus 5 | 5h 4% | 7d 16% | ctx 7%') "statusline output: $out"
        Assert (Test-Path "$env:USAGE_MONITOR_HOME\spool\statusline.jsonl") 'statusline spool written'
        $times = foreach ($i in 1..10) { (Measure-Command { $sample | & $hook statusline | Out-Null }).TotalMilliseconds }
        "    um-hook statusline: median {0:N0} ms, max {1:N0} ms (CI runner)" -f (($times | Sort-Object)[5]), (($times | Measure-Object -Maximum).Maximum)

        # the receiver picks the spooled observation up
        Start-Sleep -Seconds 2
        $snap = Invoke-RestMethod 'http://127.0.0.1:4399/api/snapshot'
        Assert ($snap.limits.'5h'.pct -eq 4) 'receiver ingested the statusline observation'

        # CLI imports every module (tray, pywebview, pystray) and prints status
        $status = & $cli status
        Assert ($LASTEXITCODE -eq 0) 'usage-monitor status exits 0'
    } finally { Stop-Process -Id $recv.Id -Force -ErrorAction SilentlyContinue }

    # connect / disconnect round trip on the temp profile
    & $cli connect | Out-Null
    $s = Get-Content $settings -Raw | ConvertFrom-Json
    Assert ($s.statusLine.command -like '*um-hook.exe" statusline') "statusLine registered: $($s.statusLine.command)"
    Assert ($s.hooks.Stop[0].hooks[0].command -like '*um-hook.exe" stop') 'Stop hook registered'
    Assert ($s.env.OTEL_EXPORTER_OTLP_LOGS_ENDPOINT -eq 'http://127.0.0.1:4399/v1/logs') 'OTel endpoint registered'
    & $cli disconnect | Out-Null
    $s = Get-Content $settings -Raw | ConvertFrom-Json
    Assert (($s.PSObject.Properties.Name -join ',') -eq 'theme') 'disconnect restores the original settings'
}

if ($Installer) {
    $target = Join-Path $env:RUNNER_TEMP 'um-install'
    $p = Start-Process $Installer -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/TASKS=""', "/DIR=$target" -PassThru -Wait
    Assert ($p.ExitCode -eq 0) "installer exit code $($p.ExitCode)"
    foreach ($f in 'UsageMonitor.exe', 'usage-monitor.exe', 'um-hook.exe', 'unins000.exe') { Assert (Test-Path (Join-Path $target $f)) "installed $f" }
    # silent install with no tasks: nothing is connected and the tray program is not started
    Assert (-not ((Get-Content $settings -Raw) -match 'um-hook.exe')) 'silent install without the connect task leaves settings.json alone'
    & (Join-Path $target 'usage-monitor.exe') connect | Out-Null
    Assert ((Get-Content $settings -Raw) -match 'um-hook.exe') 'connect from the installed copy'
    $u = Start-Process (Join-Path $target 'unins000.exe') -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -PassThru -Wait
    Assert ($u.ExitCode -eq 0) "uninstaller exit code $($u.ExitCode)"
    Start-Sleep -Seconds 2
    Assert (-not (Test-Path (Join-Path $target 'UsageMonitor.exe'))) 'files removed'
    Assert (-not ((Get-Content $settings -Raw) -match 'um-hook.exe')) 'uninstall removed our entries from settings.json'
}
'smoke test passed'

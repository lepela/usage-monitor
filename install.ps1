# usage-monitor installer (pip route)
#
#   powershell -c "irm https://raw.githubusercontent.com/lepela/usage-monitor/main/install.ps1 | iex"
#
# What it does
#   1. Finds Python 3.11+. If none, installs the python.org build for the current user with winget
#   2. pip-installs the source archive of a GitHub release (latest by default). git is not needed
#   3. Runs "usage_monitor setup": connects Claude Code settings, enables start at sign-in, starts the tray program
#
# It runs on the signed python.exe instead of our own executables, so it also works where Smart App Control is on.
# Run through "irm | iex", the script is never saved as a file, so execution policy and downloaded-file checks do not apply.
#
# This file is ASCII only on purpose: Windows PowerShell 5.1 can mis-decode non-ASCII text
# when a server does not declare a charset.
#
# Options (environment variables)
#   USAGE_MONITOR_VERSION   release tag to install, e.g. v0.1.0 (default: latest release)
#   USAGE_MONITOR_PYTHON    python.exe to use (default: found automatically)
#   USAGE_MONITOR_FORCE     1 = continue even if the installer (executable) edition is installed

function Install-UsageMonitor {
    $ErrorActionPreference = 'Stop'
    $repo = 'lepela/usage-monitor'

    function Say($text) { Write-Host "[usage-monitor] $text" }

    function Test-Python($exe) {
        # Returns the real python.exe path if it is 3.11+. Ignores the Microsoft Store alias (WindowsApps).
        if (-not $exe) { return $null }
        try {
            $out = & $exe -c "import sys; print(sys.executable if sys.version_info >= (3, 11) else '')" 2>$null
        } catch { return $null }
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
        $path = "$out".Trim()
        if ($path -like '*\WindowsApps\*') { return $null }
        return $path
    }

    function Find-Python {
        $candidates = @($env:USAGE_MONITOR_PYTHON)
        $py = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($py) {
            try { $candidates += "$(& $py.Source -3 -c 'import sys; print(sys.executable)' 2>$null)".Trim() } catch { }
        }
        foreach ($name in 'python.exe', 'python3.exe') {
            $c = Get-Command $name -ErrorAction SilentlyContinue
            if ($c) { $candidates += $c.Source }
        }
        $candidates += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | ForEach-Object { $_.FullName }
        foreach ($c in $candidates) {
            $ok = Test-Python $c
            if ($ok) { return $ok }
        }
        return $null
    }

    Say "PowerShell language mode: $($ExecutionContext.SessionState.LanguageMode)"

    # Mixing with the installer edition breaks later: uninstalling that edition also disconnects this one.
    $installed = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -eq 'Usage Monitor' }
    if ($installed -and $env:USAGE_MONITOR_FORCE -ne '1') {
        Say "Usage Monitor installed by the setup program was found ($($installed.InstallLocation))."
        Say "Remove it first in Settings > Apps > Installed apps, then run this script again."
        return
    }

    # 1. Python
    $python = Find-Python
    if (-not $python) {
        $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
        if (-not $winget) {
            Say "Python 3.11+ and winget were not found. Install Python from https://www.python.org/downloads/windows/ and run this again."
            return
        }
        Say "Python not found. Installing Python 3.12 for the current user with winget."
        & $winget.Source install --id Python.Python.3.12 --exact --scope user --silent --accept-package-agreements --accept-source-agreements
        $python = Find-Python
        if (-not $python) {
            Say "python.exe was not found after installing Python. Open a new PowerShell window and run this again."
            return
        }
    }
    Say "Python: $python"

    # 2. Package
    $tag = $env:USAGE_MONITOR_VERSION
    if (-not $tag) {
        try {
            $tag = (Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest" -Headers @{ 'User-Agent' = 'usage-monitor-install' }).tag_name
        } catch {
            Say "Could not look up the latest release: $($_.Exception.Message)"
            return
        }
    }
    $source = "https://github.com/$repo/archive/refs/tags/$tag.zip"
    Say "Installing $source"
    & $python -m pip install --upgrade --disable-pip-version-check --quiet $source
    if ($LASTEXITCODE -ne 0) {
        Say "pip install failed (exit code $LASTEXITCODE). See the messages above."
        return
    }

    # 3. Connect, start at sign-in, start now
    # -P: do not import from the current folder (a checkout of this repo would shadow the installed package)
    & $python -P -m usage_monitor setup
    if ($LASTEXITCODE -ne 0) {
        Say "setup failed (exit code $LASTEXITCODE)."
        return
    }
    Say "Done. Status:    `"$python`" -P -m usage_monitor status"
    Say "Uninstall:       `"$python`" -P -m usage_monitor uninstall, then `"$python`" -m pip uninstall usage-monitor"
}

Install-UsageMonitor

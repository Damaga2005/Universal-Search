<#
.SYNOPSIS
    Uninstalls Universal Search, removing ONLY application files.

    Uses install-manifest.json (files and shortcuts recorded at install time)
    so application binaries are never confused with user data. The index,
    configuration and logs in %LOCALAPPDATA%\Universal Search are KEPT unless
    -PurgeData is passed explicitly.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File uninstall.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File uninstall.ps1 -PurgeData

.NOTES
    Verified by tests/test_release.py (manifest-driven removal, data kept by
    default, deleted only with -PurgeData).
#>
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\UniversalSearch"),

    [string]$StartMenuPath = (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"),

    # Data directory to act on; defaults to the manifest value, then to the
    # standard per-user location. Nothing is done to it without -PurgeData.
    [string]$DataDir = "",

    # Explicitly delete the user data directory (index, config, logs)
    [switch]$PurgeData
)

$ErrorActionPreference = "Stop"
$InstallDir = $InstallDir.TrimEnd("\")

if (-not (Test-Path -LiteralPath $InstallDir)) {
    Write-Host "Nothing to uninstall: $InstallDir does not exist."
    exit 0
}

# --- read the manifest ---------------------------------------------------------
$manifestPath = Join-Path $InstallDir "install-manifest.json"
$manifest = $null
if (Test-Path -LiteralPath $manifestPath) {
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    } catch {
        $manifest = $null
    }
}

if (-not $DataDir) {
    if ($manifest -and $manifest.dataDir) { $DataDir = $manifest.dataDir }
    else {
        $DataDir = Join-Path $env:LOCALAPPDATA "Universal Search"
        if (-not $env:LOCALAPPDATA) { $DataDir = Join-Path $HOME ".universal-search" }
    }
}

# --- stop everything that keeps the binaries busy (best effort) ----------------
if ($manifest -and $manifest.backgroundWorker) {
    $cliExe = Join-Path $InstallDir "universal-search.exe"
    if (Test-Path -LiteralPath $cliExe) {
        $previousEAP = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try { & $cliExe indexer stop | Out-Null } catch { }
        try { & $cliExe indexer autostart off | Out-Null } catch { }
        $ErrorActionPreference = $previousEAP
    }
}
Get-Process -Name "UniversalSearch" -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue

# --- delete exactly the application files -------------------------------------
$foreign = @()
if ($manifest -and $manifest.files) {
    foreach ($relative in @($manifest.files)) {
        $full = Join-Path $InstallDir $relative
        if (Test-Path -LiteralPath $full) { Remove-Item -LiteralPath $full -Force }
    }
    # prune directories that became empty (deepest first)
    Get-ChildItem -LiteralPath $InstallDir -Recurse -Directory -ErrorAction SilentlyContinue |
        Sort-Object { $_.FullName.Length } -Descending |
        ForEach-Object {
            $empty = @(Get-ChildItem -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue).Count -eq 0
            if ($empty) { Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue }
        }
} else {
    # No manifest (manual copy): the folder is treated as application files.
    Remove-Item -LiteralPath $InstallDir -Recurse -Force
}

# --- shortcuts -----------------------------------------------------------------
$shortcutList = @()
if ($manifest -and $manifest.shortcuts) { $shortcutList = @($manifest.shortcuts) }
else { $shortcutList = @((Join-Path $StartMenuPath "Universal Search.lnk")) }
foreach ($shortcut in $shortcutList) {
    if ($shortcut -and (Test-Path -LiteralPath $shortcut)) {
        Remove-Item -LiteralPath $shortcut -Force
    }
}

# --- remove the manifest, then the install dir if nothing foreign remains ------
if (Test-Path -LiteralPath $manifestPath) { Remove-Item -LiteralPath $manifestPath -Force }
if (Test-Path -LiteralPath $InstallDir) {
    $remaining = @(Get-ChildItem -LiteralPath $InstallDir -Recurse -File -Force -ErrorAction SilentlyContinue)
    if ($remaining.Count -eq 0) {
        Remove-Item -LiteralPath $InstallDir -Recurse -Force
    } else {
        Write-Warning "These files are NOT in the manifest and were left untouched:"
        foreach ($file in $remaining) { Write-Warning "  $($file.FullName)" }
    }
}

# --- user data: kept unless explicitly purged ----------------------------------
if ($PurgeData) {
    if (Test-Path -LiteralPath $DataDir) {
        Remove-Item -LiteralPath $DataDir -Recurse -Force
        Write-Host "User data removed: $DataDir"
    } else {
        Write-Host "No user data found at: $DataDir"
    }
} else {
    if (Test-Path -LiteralPath $DataDir) {
        Write-Host "User data KEPT: $DataDir (index, config, logs)."
        Write-Host "Delete it later with: uninstall.ps1 -PurgeData"
    }
}

Write-Host ""
Write-Host "Universal Search uninstalled."

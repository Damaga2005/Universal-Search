<#
.SYNOPSIS
    Installs (or upgrades) Universal Search from a built dist folder.

    Application files go to -InstallDir (default: %LOCALAPPDATA%\Programs).
    User data (index database, config.json, logs) lives in
    %LOCALAPPDATA%\Universal Search and is NEVER written here: upgrades and
    uninstall preserve it unless the user explicitly deletes it.

    A manifest (install-manifest.json) records every copied file, the
    shortcuts created and the data directory, so uninstall.ps1 can remove
    exactly the application and clearly distinguish it from user data.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install.ps1 -Autostart

.NOTES
    Tested by tests/test_release.py::test_install_reinstall_uninstall_roundtrip
    (fresh install, upgrade over an existing install, uninstall keeping data).
#>
param(
    # Built output of "PyInstaller packaging/universal-search.spec"
    [string]$SourceDir = (Join-Path $PSScriptRoot "..\dist\UniversalSearch"),

    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\UniversalSearch"),

    [string]$StartMenuPath = (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"),

    # Skip the Start Menu shortcut (useful for silent/test installs)
    [switch]$NoStartMenu,

    # Register the background indexer to start with Windows
    [switch]$Autostart
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $SourceDir)) {
    throw "Source folder not found: $SourceDir (build it first: PyInstaller packaging/universal-search.spec)"
}
if (-not (Test-Path -LiteralPath (Join-Path $SourceDir "UniversalSearch.exe"))) {
    throw "UniversalSearch.exe not found in $SourceDir - expected a built dist\UniversalSearch folder."
}
$SourceDir = (Resolve-Path -LiteralPath $SourceDir).Path
$InstallDir = $InstallDir.TrimEnd("\")

# --- upgrade detection: stop a previous installation before overwriting ------
$manifestPath = Join-Path $InstallDir "install-manifest.json"
$previous = $null
if (Test-Path -LiteralPath $manifestPath) {
    try {
        $previous = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    } catch {
        $previous = $null
    }
    Write-Host "Existing installation detected - upgrading in place..."
    if ($previous -and $previous.backgroundWorker) {
        # Stop the running worker with the OLD executable (best effort).
        $oldCli = Join-Path $InstallDir "universal-search.exe"
        if (Test-Path -LiteralPath $oldCli) {
            $ErrorActionPreference = "Continue"
            try { & $oldCli indexer stop | Out-Null } catch { }
            $ErrorActionPreference = "Stop"
        }
    }
}
# The GUI must not run while its files are replaced (best effort).
Get-Process -Name "UniversalSearch" -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue

# --- copy application files ----------------------------------------------------
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item -Path (Join-Path $SourceDir "*") -Destination $InstallDir -Recurse -Force

# --- version (read from the just-installed CLI when it is runnable) -----------
$version = "unknown"
$cliExe = Join-Path $InstallDir "universal-search.exe"
if (Test-Path -LiteralPath $cliExe) {
    $previousEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $lines = & $cliExe --version 2>$null
        if ("$lines" -match '(\d+\.\d+\.\d+)') { $version = $Matches[1] }
    } catch { }
    $ErrorActionPreference = $previousEAP
}

# --- Start Menu shortcut (shared helper, overridable location) ----------------
$shortcuts = @()
if (-not $NoStartMenu) {
    & (Join-Path $PSScriptRoot "make-shortcut.ps1") `
        -TargetExe (Join-Path $InstallDir "UniversalSearch.exe") `
        -StartMenuPath $StartMenuPath
    $shortcuts += (Join-Path $StartMenuPath "Universal Search.lnk")
}

# --- optional background-indexer autostart (uses the app's own command) -------
$backgroundWorker = $false
if ($Autostart) {
    $previousEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $cliExe indexer autostart on
        $backgroundWorker = $true
    } catch {
        Write-Warning "Could not register autostart; enable it later with: universal-search.exe indexer autostart on"
        $backgroundWorker = $false
    }
    $ErrorActionPreference = $previousEAP
}

# --- user data directory (informational; created by the app on first run) -----
$dataDir = Join-Path $env:LOCALAPPDATA "Universal Search"
if (-not $env:LOCALAPPDATA) { $dataDir = Join-Path $HOME ".universal-search" }

# --- manifest ------------------------------------------------------------------
$files = Get-ChildItem -LiteralPath $InstallDir -Recurse -File |
    Where-Object { $_.Name -ne "install-manifest.json" } |
    ForEach-Object { $_.FullName.Substring($InstallDir.Length).TrimStart("\") }

$manifest = [ordered]@{
    product          = "Universal Search"
    version          = $version
    installedAt      = (Get-Date).ToString("o")
    installDir       = $InstallDir
    dataDir          = $dataDir
    files            = @($files)
    shortcuts        = @($shortcuts)
    backgroundWorker = [bool]$backgroundWorker
    upgraded         = [bool]$previous
}
$manifest | ConvertTo-Json -Depth 5 |
    Set-Content -LiteralPath $manifestPath -Encoding UTF8

Write-Host ""
Write-Host "Universal Search $version installed in: $InstallDir"
Write-Host "User data (index, config, logs): $dataDir"
if ($previous) {
    Write-Host "Upgrade complete - the previous installation and its user data were preserved."
} else {
    Write-Host "Fresh installation. Launch it from the Start Menu or: $InstallDir\UniversalSearch.exe"
}

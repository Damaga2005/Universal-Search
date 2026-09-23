<#
.SYNOPSIS
    Installs a Start Menu shortcut for Universal Search (phase 016).

.DESCRIPTION
    The installer already creates a Desktop shortcut. This script adds the
    Start Menu entry, which is what Windows Search and the taskbar use.
    It is idempotent: running it twice leaves one shortcut, not two.

    Start Menu integration is installed explicitly, never at application
    launch: an app that writes to the user's Start Menu behind their back
    is a bug report waiting to happen.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\make-start-menu.ps1
    powershell -ExecutionPolicy Bypass -File packaging\make-start-menu.ps1 -Remove
#>
[CmdletBinding()]
param(
    [string]$Target = "",
    [string]$Programs = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$Shell = New-Object -ComObject WScript.Shell

function Resolve-Target {
    if ($Target) { return $Target }
    # Frozen build first, then the development entry point.
    $frozen = Join-Path $PSScriptRoot "..\dist\UniversalSearch\UniversalSearch.exe"
    if (Test-Path $frozen) { return (Resolve-Path $frozen).Path }
    $python = (Get-Command python -ErrorAction SilentlyContinue)
    if ($python) { return $python.Source }
    throw "No target found: pass -Target <universal-search.exe>"
}

$link = Join-Path $Programs "Universal Search.lnk"

if ($Remove) {
    if (Test-Path $link) {
        Remove-Item $link -Force
        Write-Host "Removed: $link"
    } else {
        Write-Host "Nothing to remove."
    }
    return
}

$exe = Resolve-Target
New-Item -ItemType Directory -Path $Programs -Force | Out-Null
$shortcut = $Shell.CreateShortcut($link)
$shortcut.TargetPath = $exe
$shortcut.Description = "Buscador Universal - busqueda local"
$shortcut.WindowStyle = 1
$icon = Join-Path $PSScriptRoot "universal_search.ico"
if (Test-Path $icon) { $shortcut.IconLocation = "$icon,0" }
$shortcut.Save()
Write-Host "Installed: $link -> $exe"

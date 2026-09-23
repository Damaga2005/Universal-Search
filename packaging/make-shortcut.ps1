<#
.SYNOPSIS
    Creates a Start Menu shortcut for the Universal Search desktop application.

.EXAMPLE
    .\\make-shortcut.ps1 -TargetExe "dist\\UniversalSearch\\UniversalSearch.exe"
    .\\make-shortcut.ps1 -TargetExe "dist\\...\\UniversalSearch.exe" -StartMenuPath "C:\\Temp\\menu"
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$TargetExe,

    [string]$StartMenuPath = (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"),

    [string]$ShortcutName = "Universal Search.lnk"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $TargetExe)) {
    throw "Executable not found: $TargetExe"
}

$resolved = (Resolve-Path $TargetExe).Path
New-Item -ItemType Directory -Force -Path $StartMenuPath | Out-Null

$shell = New-Object -ComObject WScript.Shell
$linkPath = Join-Path $StartMenuPath $ShortcutName
$shortcut = $shell.CreateShortcut($linkPath)
$shortcut.TargetPath = $resolved
$shortcut.WorkingDirectory = Split-Path $resolved
$shortcut.Description = "Local-first universal search"
$shortcut.Save()

Write-Host "Shortcut created: $linkPath"

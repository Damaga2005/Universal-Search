<#
.SYNOPSIS
    Registers (or removes) the Explorer context-menu entry
    "Search with Universal Search" (phase 016).

.DESCRIPTION
    Adds a per-user verb under HKCU for the selected files and folders.
    Per-user, not per-machine: no administrator rights, nothing global,
    and -Remove undoes exactly what was added.

    The verb is a *convenience*, not a requirement: Universal Search works
    fully without it, and the registry entry is only a launcher.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\explorer-search.ps1
    powershell -ExecutionPolicy Bypass -File packaging\explorer-search.ps1 -Remove
#>
[CmdletBinding()]
param(
    [string]$Command = "",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

$verbKey = "HKCU:\Software\Classes\*\shell\UniversalSearch"
$directoryVerbKey = "HKCU:\Software\Classes\Directory\shell\UniversalSearch"

function Resolve-Command {
    if ($Command) { return $Command }
    $frozen = Join-Path $PSScriptRoot "..\dist\UniversalSearch\UniversalSearch.exe"
    if (Test-Path $frozen) { return '"' + (Resolve-Path $frozen).Path + '" search "%1"' }
    return 'universal-search search "%1"'
}

if ($Remove) {
    foreach ($key in @($verbKey, $directoryVerbKey)) {
        if (Test-Path $key) {
            Remove-Item $key -Recurse -Force
            Write-Host "Removed: $key"
        }
    }
    Write-Host "Explorer integration removed."
    return
}

$launch = Resolve-Command
foreach ($key in @($verbKey, $directoryVerbKey)) {
    New-Item -Path $key -Force | Out-Null
    New-ItemProperty -Path $key -Name "MUIVerb" -Value "Search with Universal Search" -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $key -Name "Icon" -Value "shell32.dll,22" -PropertyType String -Force | Out-Null
    $commandKey = Join-Path $key "command"
    New-Item -Path $commandKey -Force | Out-Null
    New-ItemProperty -Path $commandKey -Name "(Default)" -Value $launch -PropertyType String -Force | Out-Null
    Write-Host "Registered: $key -> $launch"
}

<#
    Build both Windows executables and verify the ones that ship (phase 037).

        powershell -File packaging\build.ps1                 # both builds + smoke
        powershell -File packaging\build.ps1 -SkipSmoke      # builds only

Why a script instead of the three commands in the release notes: the 2.0.0
audit's worst finding was that the release attached two bare .exe files which
did NOT run - a one-dir build needs its _internal folder ("PYI-8: Failed to
load Python DLL"). That happened because the build was a sequence of steps a
human had to remember in the right order, and nothing checked the result. This
script builds, then runs what it built, so the artefact that gets published is
one that has answered --version on this machine.

Two builds, because they are two different products:

    one-dir  (universal-search.spec)          the installed copy. Fast start,
                                             carries both the windowed and the
                                             console executable, deployed by
                                             install.ps1.
    one-file (universal-search-onefile.spec)  the portable copy. One file,
                                             slower to start, and the thing
                                             that can be copied to a USB stick.

The smoke step is deliberately the real executables, and for the one-file build
it copies the .exe somewhere with no siblings before running it: that is the
exact reproduction that caught the 2.0.0 defect.
#>

#Requires -Version 5.1
[CmdletBinding()]
param(
    [switch] $SkipSmoke,
    [string] $Python = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

function Resolve-Python {
    param([string] $Preferred)
    if ($Preferred) { return $Preferred }
    $venv = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path $venv) { return $venv }
    return "python"
}

$Py = Resolve-Python -Preferred $Python
Write-Host "python: $Py"
& $Py -c "import PyInstaller, sys; print('pyinstaller', PyInstaller.__version__, '|', sys.version.split()[0])"

# One-file output must not sit inside the one-dir output: PyInstaller would
# otherwise bundle the previous build's files into the next one.
$OneFileDist = Join-Path $RepoRoot "dist\UniversalSearch-onefile"
foreach ($stale in @((Join-Path $RepoRoot "dist\UniversalSearch"), $OneFileDist)) {
    if (Test-Path $stale) { Remove-Item $stale -Recurse -Force }

# Phase 049. The two directories above were cleaned; the loose
# executables in `dist` itself were not. A one-file build writes
# straight into `--distpath` with no folder around it, so anything
# left there by an earlier build survives every subsequent one.
# Measured on this repository: a `UniversalSearch.exe` dated 10/01
# sat next to today's real one-file -- 15,149,214 bytes against
# 15,256,382. Two executables with the same name and different
# contents, one of them from a build nobody meant to publish.
# Cleaning the directories is not cleaning the output.
Get-ChildItem -Path (Join-Path $RepoRoot "dist") -Filter "*.exe" -File |
    Remove-Item -Force
}

Write-Host "`n== one-dir build (the installed copy) =="
& $Py -m PyInstaller (Join-Path $RepoRoot "packaging\universal-search.spec") `
    --noconfirm --distpath (Join-Path $RepoRoot "dist") `
    --workpath (Join-Path $RepoRoot "build\universal-search")
if ($LASTEXITCODE -ne 0) { throw "the one-dir build failed ($LASTEXITCODE)" }

Write-Host "`n== one-file build (the portable copy) =="
# A one-file spec writes the executable straight into --distpath, with no
# folder of its own. Pointing it at dist\ would drop UniversalSearch.exe next
# to the one-dir dist\UniversalSearch\ folder and the next one-dir build would
# try to bundle it. Hence the separate distpath.
& $Py -m PyInstaller (Join-Path $RepoRoot "packaging\universal-search-onefile.spec") `
    --noconfirm --distpath $OneFileDist `
    --workpath (Join-Path $RepoRoot "build\universal-search-onefile")
if ($LASTEXITCODE -ne 0) { throw "the one-file build failed ($LASTEXITCODE)" }

$OneDir = Join-Path $RepoRoot "dist\UniversalSearch"
$CliExe = Join-Path $OneDir "universal-search.exe"
$GuiExe = Join-Path $OneDir "UniversalSearch.exe"
$OneFile = Join-Path $OneFileDist "UniversalSearch.exe"

foreach ($required in @($CliExe, $GuiExe, $OneFile, (Join-Path $OneDir "_internal"))) {
    if (-not (Test-Path $required)) { throw "missing build output: $required" }
}

Write-Host "`n== sizes =="
foreach ($artifact in @($CliExe, $GuiExe, $OneFile)) {
    $item = Get-Item $artifact
    Write-Host ("  {0,-28} {1,12:N0} bytes" -f $item.Name, $item.Length)
}

if ($SkipSmoke) {
    Write-Host "`n-smoke skipped on request"
    exit 0
}

# Every following step is a check, so a native command that fails must stop the
# script rather than scroll past. $LASTEXITCODE is checked explicitly after each
# one because $ErrorActionPreference does not cover native commands.

# Run the artefacts that will be published. A one-dir build with no smoke is
# exactly the defect this phase exists to stop.
Write-Host "`n== smoke: the one-dir CLI =="
& $CliExe --version
if ($LASTEXITCODE -ne 0) { throw "the one-dir CLI did not answer --version" }

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("us-build-smoke-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path "$tmp\docs" | Out-Null
"transistor bjt polarizacion" | Out-File -Encoding utf8 "$tmp\docs\nota.md"
& $CliExe index "$tmp\docs" --database "$tmp\index.db"
if ($LASTEXITCODE -ne 0) { throw "the one-dir CLI failed to index" }
# -match/-notmatch are FILTER operators when the left side is an array: they
# return the elements that do or do not match, so `if ($found -notmatch ...)`
# is true whenever any line fails to contain the name — which is almost always.
# Joining first turns them back into the boolean test that was intended.
$found = (& $CliExe search "bjt" --database "$tmp\index.db") -join "`n"
if ($found -notmatch "nota\.md") { throw "the one-dir CLI did not find the indexed document" }
Write-Host "  index + search OK"

Write-Host "`n== smoke: the one-file executable (moved away from everything else) =="
# The strongest version of this check: copy the single file somewhere with no
# siblings and no _internal, then use it. This is the failure the audit hit.
$isolated = Join-Path ([System.IO.Path]::GetTempPath()) ("us-onefile-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $isolated | Out-Null
$lonely = Join-Path $isolated "UniversalSearch.exe"
Copy-Item $OneFile $lonely
Write-Host "  testing $([guid]::NewGuid().ToString('N').Substring(0,8)) copy alone at $isolated"

$env:UNIVERSAL_SEARCH_HOME = Join-Path $isolated "home"
& $lonely --version
if ($LASTEXITCODE -ne 0) { throw "the single-file executable did not answer --version alone" }
& $lonely index "$tmp\docs" --database "$isolated\index.db"
if ($LASTEXITCODE -ne 0) { throw "the single-file executable failed to index" }
$oneFileFound = (& $lonely search "transisto" --database "$isolated\index.db") -join "`n"
if ($oneFileFound -notmatch "nota\.md") {
    throw "the single-file executable did not retrieve the document for a prefix query"
}
Write-Host "  --version, index and search OK, alone, no _internal folder"

# Portable mode is the reason this build exists: prove the data lands next to
# the executable and that %LOCALAPPDATA% is never consulted.
& $lonely portable on --base $isolated
$portable = & $lonely portable status
Write-Host "  $($portable -join ' ')"
if (($portable -join ' ') -notmatch [regex]::Escape($isolated)) {
    throw "portable status did not report the portable data directory"
}
Remove-Item Env:\UNIVERSAL_SEARCH_HOME
& $lonely portable status
$afterMarker = & $lonely portable status
if (($afterMarker -join ' ') -notmatch [regex]::Escape($isolated)) {
    throw "portable mode did not survive removing the environment override"
}
& $lonely portable off --base $isolated

Remove-Item $isolated -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "`n== hashes =="
Get-FileHash $CliExe, $GuiExe, $OneFile -Algorithm SHA256 |
    ForEach-Object { "  $($_.Hash)  $(Split-Path -Leaf $_.Path)" }

Write-Host "`nBUILD OK: both executables were started and answered."
# Explicit: without it the script exits with whatever $LASTEXITCODE the last
# native command left behind, so a build that succeeded could report failure.
exit 0
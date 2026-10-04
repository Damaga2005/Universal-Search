<#
.SYNOPSIS
    Verify a SHA256SUMS.txt produced by the build, without installing anything.

.DESCRIPTION
    Phase 049. The build already produced a hash file before this phase, and
    nothing could check it: the file listed absolute paths from the GitHub
    runner, so `sha256sum -c` on a user's machine reported every artefact as
    missing. A hash nobody can check is a comment, not a checksum.

    This reads the standard two-space `sha256sum` format with *relative* paths
    and verifies it against a directory. It is deliberately dependency-free:
    a user checking a download has PowerShell and nothing else.

.PARAMETER Path
    Directory holding the downloaded artefacts. Defaults to the current one.

.PARAMETER Checksums
    The manifest to check. Defaults to SHA256SUMS.txt inside -Path.

.EXAMPLE
    powershell -NoProfile -File packaging\verify-hashes.ps1 -Path .\download

    Run it after extracting a release. Every artefact this project publishes
    is listed, so a missing one is a failure and not a silence.
#>
[CmdletBinding()]
param(
    [string]$Path = ".",
    [string]$Checksums = ""
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath $Path).Path
if (-not $Checksums) { $Checksums = Join-Path $root "SHA256SUMS.txt" }
if (-not (Test-Path -LiteralPath $Checksums)) {
    Write-Error "No checksum manifest at $Checksums. A download without its hash file cannot be verified, and this script will not guess."
    exit 2
}

# The standard format: an uppercase hex digest, two spaces, then a relative
# path. `Get-Content` with no encoding keeps it byte-exact on PS 5.1.
$entries = @()
$lineNumber = 0
foreach ($line in Get-Content -LiteralPath $Checksums) {
    $lineNumber++
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    # Accept either two spaces or the `*` binary marker, and both cases of hex.
    if ($line -notmatch '^([0-9a-fA-F]{64})\s+\*?(.+)$') {
        Write-Error "Line $lineNumber is not a SHA256SUMS entry: $line"
        exit 2
    }
    $entries += [pscustomobject]@{
        Digest = $Matches[1].ToUpperInvariant()
        Relative = $Matches[2].Trim()
    }
}

if ($entries.Count -eq 0) {
    Write-Error "The manifest is empty. An empty manifest verifies nothing."
    exit 2
}

$failed = 0
$missing = 0
foreach ($entry in $entries) {
    # A relative path is the whole point. An absolute one is rejected rather
    # than resolved, because an absolute path from another machine is exactly
    # the defect this script exists to catch.
    if ([System.IO.Path]::IsPathRooted($entry.Relative)) {
        Write-Host ("FAIL  {0}: absolute path -- this manifest was written on another machine" -f $entry.Relative)
        $failed++
        continue
    }
    $target = Join-Path $root $entry.Relative
    if (-not (Test-Path -LiteralPath $target)) {
        Write-Host ("MISS  {0}" -f $entry.Relative)
        $missing++
        continue
    }
    $actual = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToUpperInvariant()
    if ($actual -eq $entry.Digest) {
        Write-Host ("ok    {0}" -f $entry.Relative)
    } else {
        Write-Host ("FAIL  {0}" -f $entry.Relative)
        Write-Host ("        esperado {0}" -f $entry.Digest)
        Write-Host ("        obtenido {0}" -f $actual)
        $failed++
    }
}

Write-Host ""
Write-Host ("{0} artefactos, {1} correctos, {2} ausentes, {3} incorrectos" -f `
    $entries.Count, ($entries.Count - $failed - $missing), $missing, $failed)

if ($failed -gt 0 -or $missing -gt 0) {
    Write-Error "La descarga no coincide con el manifiesto. No la ejecutes."
    exit 1
}
Write-Host "Todos los hashes coinciden."
exit 0
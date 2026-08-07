<#
    Downloads exactly the ffmpeg build packaging/ffmpeg-build.json describes.

        pwsh packaging\fetch-ffmpeg.ps1 -Into C:\ffmpeg-pinned

    Prints the folder holding ffmpeg.exe and ffprobe.exe, so a caller can pass
    it straight to build.ps1 -FfmpegDir.

    This exists because the Windows installer bundles ffmpeg and
    THIRD-PARTY-NOTICES.md names the exact build it bundles and offers that
    build's corresponding source. Packaging a different binary would quietly
    make that attribution false, which is why the archive is checked against the
    pin before it is unpacked and build.ps1 checks the binaries again after.

    Fetching by URL and verifying the hash is a stronger guarantee than a folder
    somebody maintains by hand: on 7 August 2026 the pinned build had vanished
    from the development machine and the local build could not run at all.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string] $Into
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$pin = Get-Content (Join-Path $PSScriptRoot "ffmpeg-build.json") -Raw |
    ConvertFrom-Json
Write-Host "Fetching $($pin.version) from $($pin.release_tag)"

New-Item -ItemType Directory -Force $Into | Out-Null
$archive = Join-Path $Into $pin.archive

if (-not (Test-Path $archive)) {
    # -UseBasicParsing and a null progress bar: the progress renderer is very
    # slow over a CI console and dominates the download time.
    $previous = $ProgressPreference
    $ProgressPreference = "SilentlyContinue"
    try {
        Invoke-WebRequest -Uri $pin.url -OutFile $archive -UseBasicParsing
    } finally {
        $ProgressPreference = $previous
    }
}

$actual = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower()
if ($actual -ne $pin.archive_sha256) {
    Remove-Item $archive -Force -ErrorAction SilentlyContinue
    throw ("The archive does not match the pin.`n" +
           "  expected $($pin.archive_sha256)`n" +
           "  found    $actual`n" +
           "THIRD-PARTY-NOTICES.md describes $($pin.version) and offers its " +
           "source. Do not package anything else.")
}
Write-Host "  archive matches the pin"

$unpacked = Join-Path $Into "unpacked"
Remove-Item $unpacked -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive -LiteralPath $archive -DestinationPath $unpacked -Force

# The BtbN archives put the binaries in <name>/bin. Found rather than assumed,
# so a change in their layout fails here with something readable instead of
# further down with "ffmpeg.exe not found".
$bin = Get-ChildItem $unpacked -Recurse -Filter "ffmpeg.exe" |
    Select-Object -First 1
if (-not $bin) {
    throw "No ffmpeg.exe anywhere in $($pin.archive)."
}
$folder = $bin.Directory.FullName

foreach ($tool in @("ffmpeg.exe", "ffprobe.exe")) {
    $path = Join-Path $folder $tool
    if (-not (Test-Path $path)) { throw "$tool is missing from the archive." }
    $hash = (Get-FileHash $path -Algorithm SHA256).Hash.ToLower()
    if ($hash -ne $pin.binaries.$tool) {
        throw ("$tool does not match the pin.`n" +
               "  expected $($pin.binaries.$tool)`n" +
               "  found    $hash")
    }
    Write-Host "  $tool matches the pin"
}

Write-Output $folder

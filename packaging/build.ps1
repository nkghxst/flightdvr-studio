<#
    Builds the Windows installer end to end.

        pwsh packaging\build.ps1

    Produces dist\installer\FlightDVRStudio-<version>-Setup.exe, a per-user
    installer of about 110 MB with ffmpeg and ffprobe bundled inside.

    Requirements: Python with PySide6 and pyinstaller, plus Inno Setup 6.
    Point -FfmpegDir at a folder holding a matching ffmpeg.exe / ffprobe.exe.
#>
[CmdletBinding()]
param(
    [string]$FfmpegDir = "C:\ffmpeg\bin",
    [switch]$SkipTests,
    [switch]$SkipFfmpegCheck
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }

# The offscreen platform leaks in from test runs and breaks the icon step.
Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue

Step "ffmpeg"
# The notices name an exact build and offer its corresponding source. Packaging
# a different binary would quietly make that attribution false, so the hashes
# are checked rather than trusted. -SkipFfmpegCheck exists for trying a new
# build; update packaging/ffmpeg-build.json before shipping one.
$pin = Get-Content (Join-Path $PSScriptRoot "ffmpeg-build.json") -Raw | ConvertFrom-Json
Write-Host "  expecting $($pin.version) from $($pin.release_tag)"

foreach ($tool in @("ffmpeg.exe", "ffprobe.exe")) {
    $path = Join-Path $FfmpegDir $tool
    if (-not (Test-Path $path)) {
        throw "$tool not found in $FfmpegDir.`nGet the pinned build from $($pin.url) and pass -FfmpegDir with its bin folder."
    }
    $actual = (Get-FileHash $path -Algorithm SHA256).Hash.ToLower()
    $expected = $pin.binaries.$tool
    if ($actual -ne $expected) {
        $message = "$tool does not match the pinned build.`n" +
                   "  expected $expected`n" +
                   "  found    $actual`n" +
                   "The notices describe $($pin.version) and offer its source. " +
                   "Use that build, or update packaging/ffmpeg-build.json and " +
                   "THIRD-PARTY-NOTICES.md to describe this one."
        if ($SkipFfmpegCheck) { Write-Warning $message } else { throw $message }
    } else {
        Write-Host "  $tool matches the pin"
    }
}
$env:FFMPEG_DIR = $FfmpegDir

# Keep the recorded configuration honest about what is actually being shipped.
& (Join-Path $FfmpegDir "ffmpeg.exe") -hide_banner -version 2>&1 |
    Set-Content (Join-Path $PSScriptRoot "ffmpeg-configuration.txt")

if (-not $SkipTests) {
    Step "Tests"
    python -m pytest tests/ -q
    if ($LASTEXITCODE -ne 0) { throw "Tests failed; not packaging a broken build." }
}

Step "Icon"
python tools/make_icon.py packaging/flightdvr.ico

Step "PyInstaller bundle"
Remove-Item "dist\FlightDVRStudio" -Recurse -Force -ErrorAction SilentlyContinue
python -m PyInstaller packaging/flightdvr_studio.spec --noconfirm --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

$exe = "dist\FlightDVRStudio\FlightDVRStudio.exe"
if (-not (Test-Path $exe)) { throw "Bundle did not produce $exe" }
$mb = (Get-ChildItem "dist\FlightDVRStudio" -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("  bundle: {0:n1} MB" -f $mb)

Step "Smoke check: does the bundle run without ffmpeg on PATH?"
$clean = ($env:PATH -split ';' | Where-Object { $_ -and $_ -notmatch 'ffmpeg' -and $_ -notmatch 'system32$' }) -join ';'

# --check first, and its exit code is the test. Judging this on "the process is
# still alive" was wrong: when ffmpeg cannot be found the app opens a modal and
# sits there, so a build with no bundled ffmpeg passed the old check.
#   0 = started and resolved ffmpeg, 3 = no ffmpeg, 4 = Qt failed, 5 = no licence
$check = Start-Process (Resolve-Path $exe) -ArgumentList '--check' -Wait -PassThru `
    -Environment @{ PATH = $clean }
if ($check.ExitCode -ne 0) {
    $why = switch ($check.ExitCode) {
        3 { "the bundled ffmpeg was not found" }
        4 { "Qt could not start" }
        5 { "a licence file is missing from the bundle" }
        default { "exit code $($check.ExitCode)" }
    }
    throw "The packaged app failed its self-check: $why."
}
Write-Host "  --check passed with ffmpeg off PATH"

# Then prove the whole window builds, which --check does not cover.
$proc = Start-Process (Resolve-Path $exe) -PassThru -Environment @{ PATH = $clean }
Start-Sleep -Seconds 12
if ($proc.HasExited) {
    throw "The packaged app exited immediately (code $($proc.ExitCode))."
}
Write-Host "  the full window built and stayed up"
Stop-Process -Id $proc.Id -Force

Step "Installer"
$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found. Install it with: winget install JRSoftware.InnoSetup" }

& $iscc "packaging\installer.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed." }

Step "Done"
Get-ChildItem "dist\installer\*.exe" |
    Select-Object Name, @{n = 'MB'; e = { [math]::Round($_.Length / 1MB, 1) } }, LastWriteTime |
    Format-Table -AutoSize

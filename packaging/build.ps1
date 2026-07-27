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
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }

# The offscreen platform leaks in from test runs and breaks the icon step.
Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue

foreach ($tool in @("ffmpeg.exe", "ffprobe.exe")) {
    if (-not (Test-Path (Join-Path $FfmpegDir $tool))) {
        throw "$tool not found in $FfmpegDir. Pass -FfmpegDir with a matching pair."
    }
}
$env:FFMPEG_DIR = $FfmpegDir

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
$proc = Start-Process (Resolve-Path $exe) -PassThru -Environment @{ PATH = $clean }
Start-Sleep -Seconds 12
if ($proc.HasExited) {
    throw "The packaged app exited immediately (code $($proc.ExitCode)); the bundled ffmpeg was probably not found."
}
Write-Host "  launched and stayed up - bundled ffmpeg resolved"
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

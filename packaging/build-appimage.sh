#!/usr/bin/env bash
#
#     packaging/build-appimage.sh
#
# Produces dist/FlightDVR_Studio-<version>-<arch>.AppImage: one executable file
# that runs on any reasonably current distribution without installing anything.
#
# ffmpeg is not bundled. Every distribution ships a maintained build and the
# app finds it on PATH, so bundling would mean shipping a second GPL binary
# with its own corresponding-source obligation for no user benefit. Set
# FFMPEG_DIR to a folder holding an ffmpeg/ffprobe pair to bundle one anyway.
#
# Requirements: python3 with PySide6 and pyinstaller, plus curl. appimagetool
# is downloaded on first run and cached under build/.
#
# Build on the oldest distribution you intend to support: an AppImage carries
# no glibc, so one built on 24.04 will not start on 22.04. CI builds on 22.04
# for that reason.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ARCH="${ARCH:-$(uname -m)}"
export ARCH
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' flightdvr/__init__.py)"
APPDIR="build/AppDir"
OUT="dist/FlightDVR_Studio-${VERSION}-${ARCH}.AppImage"

step() { printf '\n=== %s ===\n' "$1"; }

if [ "${SKIP_TESTS:-0}" != "1" ]; then
    step "Tests"
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/ -q
fi

step "Icon"
# Drawn from vectors, so this is cheap and every size is native. The offscreen
# platform is required on a build machine with no display.
QT_QPA_PLATFORM=offscreen python3 tools/make_icon.py packaging/flightdvr.ico

step "PyInstaller bundle"
rm -rf dist/FlightDVRStudio build/FlightDVRStudio
python3 -m PyInstaller packaging/flightdvr_studio.spec \
    --noconfirm --distpath dist --workpath build

BUNDLE="dist/FlightDVRStudio/FlightDVRStudio"
if [ ! -x "$BUNDLE" ]; then
    echo "Bundle did not produce $BUNDLE" >&2
    exit 1
fi
printf '  bundle: %s\n' "$(du -sh dist/FlightDVRStudio | cut -f1)"

step "AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" \
         "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp -a dist/FlightDVRStudio/. "$APPDIR/usr/bin/"

cat > "$APPDIR/flightdvr-studio.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=FlightDVR Studio
GenericName=FPV DVR converter
Comment=Browse, trim and convert HDZero goggle DVR footage
Exec=FlightDVRStudio %F
Icon=flightdvr-studio
Categories=AudioVideo;Video;AudioVideoEditing;
MimeType=video/mp2t;video/mp4;
Terminal=false
StartupWMClass=FlightDVRStudio
DESKTOP
cp "$APPDIR/flightdvr-studio.desktop" "$APPDIR/usr/share/applications/"

# appimagetool wants the icon at the AppDir root as well as in the icon theme.
cp packaging/icon_256.png "$APPDIR/flightdvr-studio.png"
cp packaging/icon_256.png \
   "$APPDIR/usr/share/icons/hicolor/256x256/apps/flightdvr-studio.png"

cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/FlightDVRStudio" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

# The GPL requires the licence to travel with the binary.
cp LICENSE THIRD-PARTY-NOTICES.md "$APPDIR/"

step "appimagetool"
TOOL="build/appimagetool-${ARCH}.AppImage"
if [ ! -x "$TOOL" ]; then
    mkdir -p build
    curl -fsSL -o "$TOOL" \
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH}.AppImage"
    chmod +x "$TOOL"
fi

step "AppImage"
mkdir -p dist
rm -f "$OUT"
# Extract-and-run avoids needing FUSE, which CI runners and several immutable
# distributions do not provide.
APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" "$APPDIR" "$OUT"
chmod +x "$OUT"

step "Smoke check"
# --check starts Qt, loads the platform plugin and resolves ffmpeg, then exits.
# Exit 3 means the build is fine but this machine has no ffmpeg installed.
set +e
APPIMAGE_EXTRACT_AND_RUN=1 QT_QPA_PLATFORM=offscreen "$OUT" --check
code=$?
set -e
case "$code" in
    0) echo "  the packaged app starts and found ffmpeg" ;;
    3) echo "  the packaged app starts; no ffmpeg on this machine to find" ;;
    *) echo "  --check failed with exit code $code" >&2; exit 1 ;;
esac

step "Done"
ls -lh "$OUT"

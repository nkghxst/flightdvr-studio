#!/usr/bin/env bash
#
#     packaging/build-macos.sh
#
# Produces dist/FlightDVR-Studio-<version>-<arch>.dmg holding the .app and a
# shortcut to Applications.
#
# The build is native-architecture only: run it on Apple Silicon for an arm64
# app and on an Intel Mac for an x86_64 one. CI does both.
#
# ffmpeg is not bundled — see build-appimage.sh for the reasoning. macOS users
# install it with `brew install ffmpeg`; the app looks in /opt/homebrew/bin and
# /usr/local/bin as well as on PATH, because an app launched from Finder does
# not inherit a shell's PATH.
#
# The result is signed ad hoc, not with a Developer ID, so Gatekeeper will
# refuse it on first launch. That is expected for an unsigned free app and the
# README documents the right-click-Open workaround. Notarising needs a paid
# Apple Developer account.
#
# Requirements: python3 with PySide6 and pyinstaller, and Xcode command line
# tools for iconutil, codesign and hdiutil.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ARCH="$(uname -m)"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' flightdvr/__init__.py)"
APP="dist/FlightDVR Studio.app"
DMG="dist/FlightDVR-Studio-${VERSION}-${ARCH}.dmg"
STAGE="build/dmg"

step() { printf '\n=== %s ===\n' "$1"; }

if [ "${SKIP_TESTS:-0}" != "1" ]; then
    step "Tests"
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/ -q
fi

step "Icons"
QT_QPA_PLATFORM=offscreen python3 tools/make_icon.py packaging/flightdvr.ico

# iconutil insists on this exact set of names. Every one is drawn at its own
# size by make_icon.py, so nothing here is an upscale.
ICONSET="build/flightdvr.iconset"
rm -rf "$ICONSET"
mkdir -p "$ICONSET"
cp packaging/icon_16.png   "$ICONSET/icon_16x16.png"
cp packaging/icon_32.png   "$ICONSET/icon_16x16@2x.png"
cp packaging/icon_32.png   "$ICONSET/icon_32x32.png"
cp packaging/icon_64.png   "$ICONSET/icon_32x32@2x.png"
cp packaging/icon_128.png  "$ICONSET/icon_128x128.png"
cp packaging/icon_256.png  "$ICONSET/icon_128x128@2x.png"
cp packaging/icon_256.png  "$ICONSET/icon_256x256.png"
cp packaging/icon_512.png  "$ICONSET/icon_256x256@2x.png"
cp packaging/icon_512.png  "$ICONSET/icon_512x512.png"
cp packaging/icon_1024.png "$ICONSET/icon_512x512@2x.png"
iconutil --convert icns --output packaging/flightdvr.icns "$ICONSET"

step "PyInstaller bundle"
rm -rf "$APP" dist/FlightDVRStudio build/FlightDVRStudio
python3 -m PyInstaller packaging/flightdvr_studio.spec \
    --noconfirm --distpath dist --workpath build

if [ ! -d "$APP" ]; then
    echo "Bundle did not produce $APP" >&2
    exit 1
fi
printf '  bundle: %s\n' "$(du -sh "$APP" | cut -f1)"

step "Ad-hoc signature"
# arm64 refuses to load an unsigned binary at all, so this is required rather
# than cosmetic. It is not a Developer ID signature and does not satisfy
# Gatekeeper; it only makes the code loadable.
codesign --force --deep --sign - --timestamp=none "$APP"
codesign --verify --deep --strict "$APP" && echo "  signature verifies"

step "Smoke check"
# --check starts Qt, loads the platform plugin and resolves ffmpeg, then exits.
set +e
QT_QPA_PLATFORM=offscreen "$APP/Contents/MacOS/FlightDVRStudio" --check
code=$?
set -e
case "$code" in
    0) echo "  the packaged app starts and found ffmpeg" ;;
    3) echo "  the packaged app starts; no ffmpeg on this machine to find" ;;
    *) echo "  --check failed with exit code $code" >&2; exit 1 ;;
esac

step "Disk image"
rm -rf "$STAGE"
mkdir -p "$STAGE" dist
cp -a "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
# The GPL requires the licence to travel with the binary.
cp LICENSE THIRD-PARTY-NOTICES.md README.md "$STAGE/"

rm -f "$DMG"
hdiutil create -volname "FlightDVR Studio" -srcfolder "$STAGE" \
    -ov -format UDZO "$DMG"

step "Done"
ls -lh "$DMG"

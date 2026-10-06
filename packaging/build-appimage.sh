#!/usr/bin/env bash
#
#     packaging/build-appimage.sh
#
# Produces dist/FlightDVR_Studio-<version>-<arch>.AppImage: one executable file
# that runs on any reasonably current distribution without installing anything.
#
# ffmpeg and ffprobe are bundled: exactly the pair packaging/ffmpeg-build-linux.json
# pins, which THIRD-PARTY-NOTICES.md names. The app looks inside its own bundle
# before PATH, so the AppImage behaves the same whatever ffmpeg the system has,
# or whether it has one at all. Set FFMPEG_DIR to a folder holding the pinned
# pair; left unset, packaging/fetch-ffmpeg.sh downloads and verifies it into
# build/ffmpeg-linux. A folder that is missing either program, or holds
# anything other than the pinned bytes, stops the build before packaging.
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

step "ffmpeg"
if [ -z "${FFMPEG_DIR:-}" ]; then
    FFMPEG_DIR="$(packaging/fetch-ffmpeg.sh build/ffmpeg-linux)"
fi
python3 packaging/verify_ffmpeg_linux.py check-dir "$FFMPEG_DIR"
# The notices point at this record of the configuration, so it has to be the
# binary actually being shipped rather than a copy that has drifted.
VERSION_TEXT="$("$FFMPEG_DIR/ffmpeg" -hide_banner -version)"
if ! diff <(printf '%s\n' "$VERSION_TEXT" | sed -n '1,3p') \
          packaging/ffmpeg-configuration-linux.txt; then
    echo "packaging/ffmpeg-configuration-linux.txt does not describe $FFMPEG_DIR/ffmpeg" >&2
    exit 1
fi
echo "  configuration matches packaging/ffmpeg-configuration-linux.txt"
export FFMPEG_DIR

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
# What PyInstaller copied, not what it was given: the pair inside the bundle is
# the one media.py will find first.
python3 packaging/verify_ffmpeg_linux.py check-dir dist/FlightDVRStudio/_internal/ffmpeg
# Listen needs QtMultimedia's PCM sink and a backend for it. What else PySide6
# brought with it is read from its bytes; anything but LGPL fails the build.
python3 packaging/check_qt_multimedia.py dist/FlightDVRStudio dist/qt-multimedia.json

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

# Licences travel with the binary. The LGPL text accompanies Qt as its section
# 4(b) requires; the GPL text is our own licence.
cp LICENSE LICENSE.LGPL-3.0.txt THIRD-PARTY-NOTICES.md \
   packaging/ffmpeg-configuration-linux.txt "$APPDIR/"

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
# The AppImage carries its own pair, so only exit 0 with the bundled copy
# passes: exit 3 (no ffmpeg found) or a system copy winning means the bundle
# is broken. CI repeats this on a machine with no system ffmpeg at all
# (packaging/check_linux_bundle.py).
set +e
REPORT="$(APPIMAGE_EXTRACT_AND_RUN=1 QT_QPA_PLATFORM=offscreen "$OUT" --check)"
code=$?
set -e
printf '%s\n' "$REPORT"
if [ "$code" -ne 0 ]; then
    echo "  --check failed with exit code $code" >&2
    exit 1
fi
if ! printf '%s\n' "$REPORT" | grep -q '^ffmpeg .*(bundled)$'; then
    echo "  --check did not resolve the bundled ffmpeg" >&2
    exit 1
fi
echo "  the packaged app starts and found its bundled ffmpeg"

step "Launch check"
# --check proves Qt started. This proves the whole window builds, which is the
# part an over-aggressive file drop in the spec would break. Offscreen, so
# nothing is displayed and no display is needed.
APPIMAGE_EXTRACT_AND_RUN=1 QT_QPA_PLATFORM=offscreen "$OUT" &
pid=$!
sleep 12
if kill -0 "$pid" 2>/dev/null; then
    echo "  the full window built and stayed up"
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
else
    wait "$pid" 2>/dev/null || echo "  exit code $?" >&2
    echo "  the packaged app exited on its own instead of opening" >&2
    exit 1
fi

step "Done"
ls -lh "$OUT"

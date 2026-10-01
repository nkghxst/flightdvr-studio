#!/usr/bin/env bash
#
#     packaging/fetch-ffmpeg.sh <folder>
#
# Downloads exactly the Linux ffmpeg build packaging/ffmpeg-build-linux.json
# describes, verifies it, and prints the folder holding the verified
# ffmpeg/ffprobe pair, so a caller can pass it straight to build-appimage.sh as
# FFMPEG_DIR.
#
# The AppImage bundles that pair and THIRD-PARTY-NOTICES.md names the exact
# build and its corresponding source, so packaging anything else would make
# the attribution false. The archive is downloaded under a temporary name and
# checked by size and SHA-256 before it is opened; verify_ffmpeg_linux.py then
# refuses unsafe or duplicate members, checks both programs against the pin,
# and only publishes <folder>/bin once all of that has passed. A failed or
# interrupted run leaves no folder that looks usable.
#
# Running it again reuses an archive that still matches the pin and a bin
# folder that still passes the check.

set -euo pipefail

if [ "$#" -ne 1 ]; then
    echo "usage: packaging/fetch-ffmpeg.sh <folder>" >&2
    exit 2
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERIFY="$HERE/verify_ffmpeg_linux.py"
INTO="$1"
mkdir -p "$INTO"
INTO="$(cd "$INTO" && pwd)"

pin() { python3 -c "import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])" \
            "$HERE/ffmpeg-build-linux.json" "$1"; }
NAME="$(pin archive)"
URL="$(pin url)"
ARCHIVE="$INTO/$NAME"
BIN="$INTO/bin"

echo "Fetching $(pin version) ($(pin variant)) from $(pin release_tag)" >&2

if [ -d "$BIN" ]; then
    python3 "$VERIFY" check-dir "$BIN" >&2
    echo "$BIN"
    exit 0
fi

if [ ! -f "$ARCHIVE" ]; then
    PART="$ARCHIVE.part.$$"
    trap 'rm -f "$PART"' EXIT
    # One attempt, no resume: a truncated transfer fails here or at the size
    # check rather than being stitched together.
    curl --fail --location --retry 0 --connect-timeout 30 --max-time 900 \
         --silent --show-error --output "$PART" "$URL"
    python3 "$VERIFY" check-archive "$PART" >&2
    mv "$PART" "$ARCHIVE"
    trap - EXIT
fi

# Checks the archive again before opening it, then unpacks and verifies.
python3 "$VERIFY" unpack "$ARCHIVE" "$BIN"

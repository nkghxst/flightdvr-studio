# Third-party notices

FlightDVR Studio is distributed under the GNU General Public License version 3.
The full text is in [LICENSE](LICENSE).

## FFmpeg

**The Windows installer and the Linux AppImage bundle FFmpeg.** The macOS app
uses Homebrew's standalone ffmpeg/ffprobe programs and redistributes neither
program by default. All three packages also carry separate FFmpeg shared
libraries through PySide6, described under Qt Multimedia below.

Where it is bundled, `ffmpeg` and `ffprobe` (`ffmpeg.exe` and `ffprobe.exe` on
Windows) are separate programs: FlightDVR Studio runs them as child processes
and contains no FFmpeg code itself.

### Windows installer

| | |
|---|---|
| Version | `n7.1.5-12-g1fdbca85aa` |
| Build | BtbN/FFmpeg-Builds, tag `autobuild-2026-07-31-14-10` |
| Build source | https://github.com/BtbN/FFmpeg-Builds |
| Upstream project | https://ffmpeg.org |
| Licence | **GNU General Public License v3 or later** |

That build is configured with `--enable-gpl --enable-version3`, which places it
under GPL v3. It includes `libx264` and `libx265`, both GPL. It is **not** a
`--enable-nonfree` build, so it is redistributable. The exact configuration is
in `ffmpeg-configuration.txt` alongside this file, and the exact binaries are
pinned by SHA-256 in `packaging/ffmpeg-build.json`; the Windows build script
refuses to package anything that does not match, so this attribution cannot
drift away from what is shipped.

#### Corresponding source

Section 6 of the GPL v3 requires the complete corresponding source: FFmpeg
itself, every library statically linked into it, and the scripts used to
build the whole thing. This build was chosen because all of that is public and
permanently addressable, rather than something this project has to mirror.

| Part | Where |
|---|---|
| FFmpeg, at the exact commit | https://github.com/FFmpeg/FFmpeg/tree/1fdbca85aa |
| The complete build system | https://github.com/BtbN/FFmpeg-Builds/tree/autobuild-2026-07-31-14-10 |
| Every dependency, with the version and source of each | https://github.com/BtbN/FFmpeg-Builds/tree/autobuild-2026-07-31-14-10/scripts.d |

Those are tagged references, not moving ones, so they describe the binary you
received rather than whatever is current.

If you would rather receive the source on physical media, contact the author and
it will be provided at no more than the cost of distribution. This offer is valid
for three years from the date you received this software.

### Linux AppImage

The AppImage bundles the Linux build of the same FFmpeg commit, from the same
BtbN release as the Windows installer.

| | |
|---|---|
| Version | `n7.1.5-12-g1fdbca85aa`, BtbN variant `linux64-gpl-7.1` |
| Build | BtbN/FFmpeg-Builds release `autobuild-2026-07-31-14-10`, build-system commit `a99e8230eae00d1cee38f23076a7a1f55cd984e2` |
| Archive | `ffmpeg-n7.1.5-12-g1fdbca85aa-linux64-gpl-7.1.tar.xz`, 119,007,364 bytes, SHA-256 `c1e6caf48923dd8e6bc5e54d51ba70c321175b8162ae9c414c392990e72f0e79` |
| `ffmpeg` | 139,397,096 bytes, SHA-256 `be59d8a5989ce0593343c0a8e3c36dd02ce523ecdc4b9ebc04437b2aa9ad2fe6` |
| `ffprobe` | 139,261,288 bytes, SHA-256 `716620defe0abbfead89c7c3895cbdebc7a530dd4646f9967c5234abc7cccbad` |
| Licence | **GNU General Public License v3 or later** |

It is configured with `--enable-gpl --enable-version3` and is **not** an
`--enable-nonfree` build. The exact configuration is in
`ffmpeg-configuration-linux.txt` alongside this file; the build refuses to
package a binary whose own version output does not match it. The pin is
`packaging/ffmpeg-build-linux.json`, and the build refuses any archive or
program whose size or SHA-256 differs from it.

Its declared dynamic dependencies are the GNU C library (`libc.so.6`,
`libm.so.6`, `libdl.so.2`, `librt.so.1`, `libpthread.so.0`, `libmvec.so.1` and
the loader `ld-linux-x86-64.so.2`, symbol versions up to `GLIBC_2.28`) and
`libgcc_s.so.1`, the GCC runtime library. The AppImage does not carry the C
library itself (`libc.so.6`) or its loader; those always come from the system
it runs on. It does carry copies of two of the others, which PyInstaller
collects for Python and Qt from the Ubuntu 22.04 system the AppImage is built
on, byte-identical to that system's files:

| Library | Ubuntu package (source package) |
|---|---|
| `libgcc_s.so.1` (GCC runtime library) | `libgcc-s1` 12.3.0-1ubuntu1~22.04.3 (`gcc-12`) |
| `libmvec.so.1` (glibc's vector maths library) | `libc6` 2.35-0ubuntu3.15 (`glibc`) |

When the app starts ffmpeg or ffprobe, those use the AppImage's copies of these
two and the system's `libc.so.6` and `libm.so.6`. This was measured from the
loader's own log, for every ffmpeg and ffprobe the packaged app started, on
Ubuntu 22.04 and Ubuntu 24.04 in CI. Other systems have not been measured.
Hardware encoding, where it works, uses the graphics drivers already installed
on the system.

#### Corresponding source

The GitHub release is not marked immutable, so the commits below, not the
release name, are the fixed references:

| Part | Where |
|---|---|
| FFmpeg, at the exact commit | https://github.com/FFmpeg/FFmpeg/tree/1fdbca85aaea513c9cc6c14d347f76543346d3da |
| The complete build system | https://github.com/BtbN/FFmpeg-Builds/tree/a99e8230eae00d1cee38f23076a7a1f55cd984e2 |
| Every dependency, with the version and source of each | https://github.com/BtbN/FFmpeg-Builds/tree/a99e8230eae00d1cee38f23076a7a1f55cd984e2/scripts.d |

The release that carries the AppImage also carries
`FlightDVR_Studio-<version>-linux-ffmpeg-source.tar`, built by the same CI run.
It contains:
- FFmpeg and the build system at the two commits above;
- the source of every dependency stage that build system enables for this
  build (linux64, gpl, FFmpeg 7.1), fetched with the build system's own
  download recipes. Every source each one declares is checked to be at that
  commit, tag or revision, in the repository fetched from its own declared
  location. That includes rav1e's Rust crates, vendored from its lock file;
- the Ubuntu source packages, with their Debian patches, for the two carried
  libraries above. They are checked against their `.dsc` and the signed
  Ubuntu archive index;
- `MANIFEST.json`, giving every file's SHA-256, origin and why it matches;
- `README.md`, explaining how to rebuild with the build system.

Two things are deliberately not in it, and the manifest says so:
- **The toolchain's own sources.** The binaries take GCC's runtime libraries
  under the GCC Runtime Library Exception and link glibc dynamically. The
  toolchain's component versions are listed.
- **The `cc` build crate.** rav1e's build updates it at build time; it
  compiles rav1e's C and assembly parts and is not linked into the binary.

## Qt / PySide6

The user interface uses Qt via PySide6, used under the **GNU Lesser General
Public License v3**. Qt is dynamically linked and unmodified. Sources are
available from https://download.qt.io and https://pypi.org/project/PySide6/.

The LGPL's own text accompanies every build as
[LICENSE.LGPL-3.0.txt](LICENSE.LGPL-3.0.txt), which section 4(b) requires with a
combined work. The LGPL v3 supplements the GPL v3 rather than replacing it, so
both texts are needed and both are included.

You may replace the Qt used by this program with your own build. Everything
needed to do so is here: the application is plain Python, the Qt libraries live
alongside it inside the package, and rebuilding is documented in
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

### Qt Multimedia, and the FFmpeg libraries it brings

From 2.0.0 the packages include **Qt Multimedia** for one job: sending sound
that the app has already decoded to your audio output. Recordings and music
are never decoded through it; that stays with the ffmpeg programs above.

PySide6 distributes Qt Multimedia together with a multimedia backend plugin
and **FFmpeg's shared libraries** (libavcodec, libavformat, libavutil,
libswresample, libswscale) as built by the Qt project, which that plugin may
load. They are separate from, and not the same build as, the ffmpeg and
ffprobe programs described under FFmpeg above.

Each of these libraries states its own licence in its compiled bytes. Every
package build reads it with `packaging/check_qt_multimedia.py`, which fails
the build unless every one says **"LGPL version 2.1 or later"** and none is a
nonfree build, and records each library's FFmpeg version tag in the build's
`qt-multimedia.json`. The FFmpeg source for that tag is at
https://github.com/FFmpeg/FFmpeg; PySide6 itself is at
https://pypi.org/project/PySide6/. The same freedom to replace them applies as
for Qt.

## Patents

H.264 and H.265 are covered by patents in some jurisdictions. This software is
provided free of charge and its authors make no patent grant. If you are
redistributing it, or using it commercially, satisfy yourself about the position
in your own jurisdiction.

## Not affiliated with HDZero

HDZero and Box Pro are the trade names of their respective owner. FlightDVR
Studio is an independent tool that reads files produced by those goggles. It is
not affiliated with, endorsed by, or supported by HDZero.

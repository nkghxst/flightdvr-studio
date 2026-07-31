# Third-party notices

FlightDVR Studio is distributed under the GNU General Public License version 3.
The full text is in [LICENSE](LICENSE).

## FFmpeg

**Only the Windows installer bundles FFmpeg.** The Linux AppImage and the macOS
app use the copy your package manager installed and redistribute no FFmpeg
binary, so the offer below applies to the Windows download alone.

Where it is bundled, `ffmpeg.exe` and `ffprobe.exe` are separate programs:
FlightDVR Studio runs them as child processes and contains no FFmpeg code
itself.

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

### Corresponding source

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

## Patents

H.264 and H.265 are covered by patents in some jurisdictions. This software is
provided free of charge and its authors make no patent grant. If you are
redistributing it, or using it commercially, satisfy yourself about the position
in your own jurisdiction.

## Not affiliated with HDZero

HDZero and Box Pro are the trade names of their respective owner. FlightDVR
Studio is an independent tool that reads files produced by those goggles. It is
not affiliated with, endorsed by, or supported by HDZero.

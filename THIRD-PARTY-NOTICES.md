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
| Version | 7.1.1 |
| Build | `7.1.1-full_build-www.gyan.dev` |
| Build source | https://www.gyan.dev/ffmpeg/builds/ |
| Upstream project | https://ffmpeg.org |
| Licence | **GNU General Public License v3 or later** |

That build is configured with `--enable-gpl --enable-version3`, which places it
under GPL v3. It includes `libx264` and `libx265`, both GPL. It is **not** a
`--enable-nonfree` build, so it is redistributable.

### Written offer for the source code

As required by section 6 of the GPL v3, the complete corresponding source for
the bundled FFmpeg binaries is available:

- FFmpeg 7.1.1 upstream source: https://ffmpeg.org/releases/ffmpeg-7.1.1.tar.xz
- The exact build configuration used is printed by running `ffmpeg -version`
  from the installation folder, and is reproduced in `ffmpeg-configuration.txt`
  alongside this file.

**Known gap.** That build links a number of libraries statically, and the link
above covers FFmpeg's own source rather than every dependency and the scripts
used to assemble them. Corresponding source under section 6 means all of it. If
you want any part of it and cannot obtain it, ask the author and it will be
provided; meanwhile a complete versioned bundle is being prepared to sit beside
the installer. Tracked at
https://github.com/nkghxst/flightdvr-studio/issues.

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

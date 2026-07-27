# Third-party notices

FlightDVR Studio is distributed under the GNU General Public License version 3.
The full text is in [LICENSE](LICENSE).

## FFmpeg

The installer bundles `ffmpeg.exe` and `ffprobe.exe`. They are separate
programs: FlightDVR Studio runs them as child processes and contains no FFmpeg
code itself.

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

If you would rather receive the source on physical media, contact the author and
it will be provided at no more than the cost of distribution. This offer is valid
for three years from the date you received this software.

## Qt / PySide6

The user interface uses Qt via PySide6, used under the **GNU Lesser General
Public License v3**. Qt is dynamically linked and unmodified. Sources are
available from https://download.qt.io and https://pypi.org/project/PySide6/.

## Patents

H.264 and H.265 are covered by patents in some jurisdictions. This software is
provided free of charge and its authors make no patent grant. If you are
redistributing it, or using it commercially, satisfy yourself about the position
in your own jurisdiction.

## Not affiliated with HDZero

HDZero and Box Pro are the trade names of their respective owner. FlightDVR
Studio is an independent tool that reads files produced by those goggles. It is
not affiliated with, endorsed by, or supported by HDZero.

# Changelog

## 1.1.0

- **Linux support.** Ships as an AppImage: one executable file, no install.
  Finds cards by parsing `/proc/mounts`, treating anything under `/media`,
  `/run/media` or `/mnt` as removable alongside the sysfs removable flag.
- **macOS support.** Ships as a `.dmg` for Apple Silicon. Finds cards by
  listing `/Volumes`, and looks for ffmpeg in both Homebrew prefixes because an
  app launched from Finder inherits neither on PATH.
- Preview now uses `open` on macOS and finds VLC, IINA or mpv inside
  `/Applications` where nothing is on PATH.
- New `--check` flag on every packaged build: reports the Qt platform plugin in
  use and the ffmpeg it resolved, then exits. Exit code 3 means no ffmpeg, 4
  means Qt could not start.
- Builds for Linux and macOS are produced by GitHub Actions, which also runs the
  test suite on all three platforms.
- Neither the AppImage nor the macOS app bundles ffmpeg — both platforms have a
  package manager that supplies a maintained one. The Windows installer is
  unchanged and still carries its own copy.

## 1.0.0

First public release.

- Browse an HDZero card with thumbnails, sorting and preview in your usual player
- Per-clip trimming with keyframe filmstrip scrubbing
- Four export presets: Edit (DNxHR/ProRes mezzanine), Master (H.264 by quality),
  Social (two-pass, size-targeted), Remux (lossless rewrap)
- Corrects the full-range colour tagging that makes this footage look crushed in
  most editors
- Copy originals off the card into dated folders, with a hand-entered flight date
  because the goggles have no clock battery
- Hardware encoding on NVIDIA, Intel, AMD or Apple, detected by running a test
  encode rather than trusting what ffmpeg advertises
- Export queue with overall progress, time remaining, and guards against
  overwriting files, filling the disk or leaving a half-written export behind
- Works with any HDZero goggle: resolution and frame rate options are built from
  the footage rather than assumed

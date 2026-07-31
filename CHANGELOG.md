# Changelog

## 1.1.1

Export integrity. Everything here was found by an independent review of the
repository, and every fix was verified against real Box Pro footage rather than
by inspecting arguments.

- **Trimmed exports no longer start with corrupt video.** A seek into an
  MPEG-TS lands on an estimated byte offset rather than a keyframe, so decoding
  began without a reference picture and every frame from the in point to the
  next keyframe was garbage — up to a full second, silently, with the correct
  frame count and no ffmpeg error. Measured at 12 dB PSNR against an accurate
  reference. The fix decodes a two-second lead-in and discards it, which
  measured bit-identical to an accurate seek and is still faster than one.
  This affected every trim that did not begin exactly on a second boundary.
- **A failed export can no longer destroy the file it was replacing.** ffmpeg
  was pointed at the final path with `-y`, so it truncated the previous export
  on open; if the encode then failed or was cancelled, the old file was gone
  and cleanup deliberately refused to remove the wreckage. Exports are now
  written beside the target and moved into place only after they pass
  validation.
- **Exports are checked before being called finished.** Copying without
  re-encoding a selection that contains no keyframe exits successfully having
  written a container header and nothing else. That 261-byte file with no video
  in it was reported as "Done, 0 MB"; it is now a failure that explains itself.
- **The export queue can no longer hang.** ffmpeg's stderr was only read after
  its stdout closed, so a chatty encode could fill the stderr pipe, block
  ffmpeg, and deadlock the export with no timeout on either side. A DVR file
  truncated by a mid-flight power loss produces exactly that volume of warnings.
- **Cancel now stops the encode.** It previously stopped reading progress and
  then waited for ffmpeg to finish anyway, and could miss a process that was
  still starting.
- **Failures say what went wrong.** ffmpeg reports the cause first and then
  cascades generic wrappers ending in "Conversion failed!", which is the line
  the app used to show. It now reports the cause.
- Qt's licence text ships with every build, as LGPL v3 section 4(b) requires
  with a combined work. It was in none of them.
- About no longer claims to bundle ffmpeg on Linux and macOS, where it uses the
  system copy, and finds the licence in whichever package format is running.
- **The Windows build now bundles ffmpeg from BtbN/FFmpeg-Builds**, pinned by
  checksum. The previous notice offered only FFmpeg's own source, while the
  binary statically linked dozens of libraries — section 6 of the GPL covers
  all of them. BtbN publishes its entire build system publicly under immutable
  tags, so the corresponding source is now a set of exact links rather than
  something this project would have to host. Verified as a drop-in first: the
  same trim and colour chain produces bit-identical output on both builds.
- The Windows build script refuses to package an ffmpeg that does not match the
  recorded hashes, and regenerates the recorded configuration from the binary
  it is actually shipping, so the attribution cannot drift.
- The Windows smoke test no longer passes when ffmpeg is missing. It judged
  success on the process still running, and a missing-ffmpeg dialog kept it
  running; it now requires `--check` to exit cleanly first.
- `--check` reports where it found each licence file, so a build that cannot
  find its own licence fails in CI rather than surprising someone in About.

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

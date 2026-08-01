# Changelog

## 1.2.0

**Joining clips works properly now.** Earlier versions produced a file that
played, was the right length, and was quietly wrong — so the advice was to
export clips separately. That advice no longer applies.

- **A joined export is built from every clip rather than the first one.** The
  size target, audio, frame rate, dimensions and colour handling were all read
  off the first clip and applied to the rest: a joined 45 MB target produced
  roughly 45 MB per clip, and putting a silent clip first removed the sound
  from all of them. Each clip is now decoded on its own, cut, brought to a
  common format, given silence if it has none, and only then joined.
- **Clips that differ can be joined.** Different sizes, frame rates, codecs and
  colour ranges are all handled. Clips are brought to the largest size and rate
  among them, so nothing is thrown away to suit the smallest.
- **A trimmed join no longer starts each clip on a damaged frame.** Every clip
  gets the accurate two-part seek that a single-clip export already had.
- **Joining with Remux still refuses clips that differ**, because copying
  without re-encoding cannot change anything — and it now says which presets
  can.

Everything else here was found by an independent review of the code.

- **Editing the queue during an export no longer disturbs it.** Clearing the
  queue while encoding left the app working through jobs that were no longer on
  screen, and removing a row could move the wrong progress bar or stop the
  queue with an error.
- **Starting a new scan no longer lets the previous one interfere.** A scan of
  a slow card kept running after a new one began, and its clips and its
  "finished" could arrive in the middle of the new scan.
- **An interrupted copy to the library leaves nothing behind.** Pulling the
  card or filling the disk mid-copy left a `.part` file among the recordings.
- **Free space is checked properly for a new folder.** Choosing a destination
  two levels below anything that existed skipped the check entirely.
- **Two exports can no longer quietly overwrite each other** on Windows and
  macOS, where `hdz_001.mp4` and `HDZ_001.mp4` are the same file.
- **Footage that is not an even number of pixels across now exports.** Nothing
  a goggle records is, but the app opens any folder.
- **An export folder whose name begins with a dash now works.**

## 1.1.2

**Linux users on Ubuntu 22.04, Debian 11 or Linux Mint 21 should update.**
Nothing changes on Windows or macOS, or on Linux with ffmpeg 5.1 or newer.

- **Exports failed outright on ffmpeg older than 5.1.** ffmpeg 5.1 renamed
  `-vsync` to `-fps_mode`, and every re-encoding export used the new name — so
  on distributions still shipping ffmpeg 4.4 every export stopped with
  `Unrecognized option 'fps_mode'`, and the trim panel showed no filmstrip for
  the same reason. The option is now asked for rather than assumed, the same
  way hardware encoders already are. Ubuntu 22.04 is exactly the version the
  AppImage is built for, so this affected the distribution it most needed to
  work on.
- **Failures no longer hide ffmpeg's explanation.** The message shown was
  chosen by matching against a list of words, and anything phrased differently
  was reduced to "exit code 1" — which is precisely when ffmpeg's own account
  of the problem is worth the most.

Behind the scenes, exports are now tested by running ffmpeg and inspecting the
file that comes out, rather than by checking the arguments the app assembled.
That suite found the defect above on its first run, on a machine none of this
had been tried on.

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

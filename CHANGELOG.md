# Changelog

## 1.5.0

**You can get through a whole card now.** 1.4 made one clip worth opening; this
one is about the other hundred and twenty. Every decision you make is written
down as you make it, so closing the window no longer throws the afternoon away,
and the list tells you what you have already decided without opening anything.

- **Sessions, autosaved.** Trim ranges, review states, range names, join order
  and the export settings you chose all belong to the folder you are working
  on, and come back when you open it again. There is nothing to press. The
  `Session` menu has *Save session as…* when one deserves a name, *Open
  session…*, and the last few under *Recent sessions*. A session references your
  footage rather than containing it, so it is small enough to back up and can be
  moved without taking the video with it.

  **Clips are identified by path, size and modification time together, never by
  name alone.** Cards get wiped and reused, and `hdz_004.ts` next month is a
  different flight from `hdz_004.ts` today. A session that confidently attached
  last week's trim points to this week's footage would be worse than one that
  remembered nothing, so a clip it cannot match is reported rather than guessed
  at.

- **Review states: Unreviewed, Keep, Maybe, Reject.** A key each — `U` `K` `M`
  `R` with the clip list focused — a filter, and a count of how far through you
  are. *Maybe* matters more than it sounds: on a long card most of the decisions
  are "not now", and without somewhere to put them they become Keep and the job
  comes back later.

  The row is tinted faintly to match, blended from the table's own background so
  it works in both the light and dark Windows themes rather than one of them.
  The colour is reinforcement and never the message — the letter in the `State`
  column is what says the state, so nothing is lost if the greens and reds look
  alike to you.

- **Several ranges out of one recording.** A four-minute flight usually holds
  two or three moments worth keeping. Each gets its own in and out points and an
  optional name, and each can be exported on its own or joined with the others —
  `hdz_048_1_Launch_upload.mp4` rather than a counter. `N` adds one with the
  picture focused.

  The button used to say *Add select*. "Select" is film-editing jargon, and it
  already meant two other things in that window: ticking a clip for export, and
  highlighting a row. It says **range** now, everywhere you can read it. The
  session file still uses the old key internally, deliberately: renaming it
  would cost a schema migration for a word nobody reading the file will see.

- **Frame-by-frame trimming.** The filmstrip is one frame per second, which is
  right for finding a moment and wrong for cutting on one. Pause and use `,` and
  `.` to step through real source frames — ten with Shift — and the app decodes
  a small window around the playhead rather than the whole recording.

  Seeking used to paint the nearest filmstrip still, which is extracted 160
  pixels wide because that is all a strip tile needs, and the preview box is up
  to 780. A quarter of a second after you stop dragging, the real frame replaces
  it.

- **The app reads what the footage is doing.** From the keyframes already
  extracted for the filmstrip it separates moving from stopped, and will offer
  its longest useful run as a range when that would remove dead time. It offers
  and never applies; a wrong guess that quietly trimmed your footage would cost
  far more than no guess at all.

  It also says when it cannot tell. A feed whose own noise is louder than the
  difference between flying and crashed reports no reading rather than a
  confident wrong answer.

- **The `State` column says what is in a clip without opening it.** Under the
  review letter it shows how many ranges you have saved, and roughly how many
  arm-fly-land cycles the footage looks like it holds — `2 ranges`, `3 flights`.
  A clip that was read and holds none says `no flying`; one that has not been
  read yet, or whose feed could not be read at all, says nothing rather than
  inventing an answer.

  That reading happens on its own, after scanning and thumbnails have finished,
  one clip at a time and at below-normal priority, reusing the filmstrips of
  clips you have already opened before decoding anything new. Clicking a clip
  takes the card back from it immediately, and it resumes where it left off.

- **The keyboard shortcuts are in the app.** `F1`, or `Help ▸ Keyboard
  shortcuts`, grouped by what has to have focus — because several keys depend on
  it. `K` is Keep in the clip list and Play on the picture; `Space` ticks a clip
  in one place and plays in the other. The README's list had been wrong for some
  time, so the dialog is checked against the real bindings by a test rather than
  kept by hand. `Help ▸ About` is there too, beside the button it has always had.

- **Every launch was checking for updates with no way to stop it.** The About
  box's *Check for updates* tickbox was built without an owner, so Python
  collected it before the dialog appeared: the control was missing, and reading
  it back was an access violation. The setting defaults to on and that was the
  only thing that could turn it off — while the README said unticking it stopped
  the request entirely. It is back, and it works.

Under the surface, the Windows installer is built in CI alongside the AppImage
and the macOS app, from an ffmpeg archive pinned by URL and hash so a changed
binary fails the build instead of shipping quietly. Where this is all going is
written down in [docs/ROADMAP.md](docs/ROADMAP.md).

## 1.4.0

**You can watch the footage now.** Until this release the app could tell you a
clip was 3 minutes 33 and 599 MB, and nothing about what was in it. Trimming
meant guessing at two numbers and finding out after the export. There is a
player in the window now, and the trim points are set on the frame in front of
you.

- **A preview player, permanently in the window.** Double-click a clip and it
  plays. Under it is a filmstrip of every keyframe in the recording, a second
  apart — click anywhere to jump there, drag either end to set where the export
  starts and ends. With the picture focused, Space or K plays, I and O set the
  in and out points at the playhead, the arrow keys move a second (five with
  Shift), Home and End jump to the trim points, Esc stops.

  It is silent, and deliberately so: audio would need a second synchronised
  pipeline to buy very little on footage most people mute anyway.

  Decoding is ffmpeg's rather than Qt's, because Windows Media Foundation
  cannot decode HEVC in an MPEG-TS container — which is exactly what the
  goggles write. *Open in player…* is still there if you would rather use your
  own.

- **A joined remux that would come out corrupt is now refused instead of
  written.** Joining trimmed clips without re-encoding produced a file that
  played, reported the right length, and had torn frames at every join: a
  stream copy can only start where a keyframe already is. The message says both
  ways out — reset the trims to join them untouched, or pick a re-encoding
  preset to keep them. Single-clip trimmed remuxes were never affected and are
  unchanged.

- **The window no longer freezes when you stop something.** Filmstrip
  extraction could not be cancelled, so a slow card or a damaged recording
  could leave the window refusing to close. Selecting the same clip twice while
  the first extraction was still running had the two of them overwriting each
  other's cache. Stopping the preview blocked the interface until ffmpeg
  actually exited.

- **A crash on closing the window is fixed**, where the hardware probe outlived
  the window it reported to.

- **The low-space warning no longer crashes.** Asking to export more than the
  free space raised an error instead of the warning — so the guard against
  filling your disk was the one thing guaranteed to break when it mattered.
  Present since the first public release. Found by Codex while extracting the
  panels below.

- **A trimmed export could begin at the wrong second** when audio was turned
  off and the recording's sound started fractionally before its picture. No
  error, no corrupt frames, correct length, just the wrong moment. HDZero
  recordings start at zero and never showed it; the fix pins the timeline so
  the seek means one thing either way.

Under the surface, `ui.py` is now five modules rather than one, which is
groundwork for the sessions and selects work in 1.5. Where this is all going is
written down in [docs/ROADMAP.md](docs/ROADMAP.md).

## 1.3.0

- **A new Upload preset, for sites that re-encode what you send them.**
  YouTube, Instagram and Reddit decide how much bitrate to spend based on the
  resolution you arrived at, so uploading at 1080p wins a bigger allowance than
  720p and more of the footage survives their encode. It does not add detail
  the goggles never recorded — the picture is the same, the difference is how
  the platform treats it. Quality-based only; a size target and an upscale pull
  against each other. Asked for by TheFunkLovinCriminal.
- **The app can tell you when a new version exists.** One request a day to this
  project's own releases page, showing a dismissible line when there is
  something newer. Nothing is downloaded or installed — it links to the release
  and you decide. It can be turned off in the About box, and then nothing is
  requested at all.

  This is the only network access the app makes, and the README now says so
  plainly rather than claiming there is none.

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

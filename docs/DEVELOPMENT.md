# Development notes

Everything a new contributor — or a future me — needs to pick this up without
rediscovering the same things the hard way. The [README](../README.md) covers
using the app; this covers building it and the reasoning behind the parts that
look arbitrary.

---

## Getting set up

```bash
pip install -r requirements.txt pytest pyinstaller
python -m flightdvr
```

Running from source needs `ffmpeg` and `ffprobe` findable — on PATH, or in one
of the fallback directories listed in `media.py`.

```bash
python -m pytest                        # everything, about 15 seconds
python -m pytest -m "not integration"   # the fast loop, under a second
python -m pytest -m integration         # real encodes only
```

There are two kinds of test here and the difference matters.

**Unit tests** check the commands the app *would* issue. They need neither
ffmpeg nor a display and run in well under a second. `QT_QPA_PLATFORM` is set
in `conftest.py` before any Qt import, which is why some imports sit below an
`os.environ.setdefault` with `# noqa: E402`.

**Integration tests** (`tests/test_integration.py`) run ffmpeg and inspect the
file that comes out. They exist because the review found eighteen defects and
**not one was visible in the arguments** — several were guarded by unit tests
that passed. A command can be perfectly well-formed and still produce half a
second of corrupt video, or an empty container reported as a success.

Fixtures in `conftest.py` imitate a Box Pro: MPEG-TS, HEVC, 60fps, full range,
and a keyframe every 1.000 s, because the trim defect depended on that GOP
length. They are 320x180 and cached per session, so the whole thing costs about
fifteen seconds.

### xfail is the known-defects list

Every `xfail(strict=True)` in the integration module describes a defect that is
still real. `xfail_strict` is on, so a test that starts passing becomes an
error rather than a quiet success — whoever fixes the defect is told to delete
the marker. The list cannot silently go stale.

### Assert your fixtures

Three separate attempts at the odd-dimensions fixture tested nothing and passed
while doing it. `testsrc2` works internally in yuv420p and silently emits
126x94 when asked for 127x95; H.264 crops to macroblock boundaries and does the
same. The working version uses `testsrc` and FFV1, and the fixture checks its
own output and skips loudly rather than pretending. If a fixture is meant to
have an awkward property, verify it has it.

**Compare paths as `Path` objects, never as strings.** Several constants hold
Windows and macOS locations regardless of which platform is running — the
preview player list is the obvious one. To POSIX, `C:\Program Files\...` is a
single long filename rather than something with separators in it, so a string
comparison silently never matches and the test passes for the wrong reason.
That is exactly how the first CI run caught a test which had only ever run on
Windows.

---

## Layout

| File | Responsibility |
|---|---|
| `flightdvr/media.py` | Finding ffmpeg, probing clips, detecting hardware encoders |
| `flightdvr/presets.py` | Colour modes, export presets, ffmpeg command construction |
| `flightdvr/jobs.py` | The export queue, the worker thread, progress parsing |
| `flightdvr/scan.py` | Drive detection, clip discovery, copying off the card |
| `flightdvr/thumbs.py` | Thumbnail extraction and caching |
| `flightdvr/trim.py` | Filmstrip extraction and the scrubbing bar widget |
| `flightdvr/browser_panel.py` | Clip table construction and thumbnail sizing |
| `flightdvr/preview_panel.py` | Preview, transport controls and filmstrip view |
| `flightdvr/export_panel.py` | Export controls, settings and source-derived choices |
| `flightdvr/queue_panel.py` | Queue widgets, summaries and progress rendering |
| `flightdvr/ui.py` | MainWindow orchestration and workflows spanning panels |
| `packaging/` | Per-platform build scripts, the PyInstaller spec, the icon |
| `tools/` | Developer utilities: icon, screenshots, demo GIF, colour comparison |

Panel modules own their widgets and local presentation behaviour. They report
user actions through signals; `MainWindow` coordinates scanning, playback and
export work across those panels. Compatibility properties on `MainWindow`
preserve the established integration surface without duplicating widget
ownership. The preview is a `QObject` composition because its picture and
full-width filmstrip deliberately live in different parent layouts.

---

## The colour finding, and why not to "fix" it

**This is the single most important thing in the project. Do not change the
default colour mode without re-running the measurement.**

HDZero DVR files decode as `yuvj420p` with `color_range=pc` — the luma really
does span 0–255. They are also tagged `bt470bg` primaries and `smpte170m`
transfer, which is almost certainly a firmware default rather than a genuine
colorimetry claim.

Four candidate filter chains were measured against the source by rendering both
to RGB and comparing. `tools/compare_colour.py` reproduces this on any clip:

| Chain | PSNR | Max delta |
|---|---|---|
| Passthrough (no colour handling) | 99.00 dB | 0 |
| **Range only, original matrix tags kept** | **50.08 dB** | **3** |
| Matrix retag to bt709 | 38.45 dB | 75 |
| Full bt709 conversion | 34.26 dB | 108 |

So the default is range conversion alone —
`scale=in_range=full:out_range=limited`. It fixes the one defect that is
provably real (full-range data being clipped by anything that assumes limited
range) and changes nothing else. Rewriting the colour tags measurably shifts
the picture, because a conversion is only as trustworthy as the tags it reads,
and these tags are not trustworthy.

The path to this answer was not straight. `signalstats` and raw pixel dumps
disagreed with each other, and both were misleading. What settled it was a
split filtergraph proving x264 actually received 16–235, and then an RGB
comparison against the source as the real test. If you find yourself measuring
YUV values and drawing conclusions, that is the trap.

Rec.709 conversion is still offered as a mode, honestly labelled, because
somebody with a colour-managed pipeline may want standards-clean tags more than
they want fidelity.

---

## Traps in this footage

These all cost real time to diagnose. Each has a test guarding it.

**MPEG-TS seeking lands mid-GOP.** `-ss` on a `.ts` input seeks to an estimated
byte offset, not a keyframe, so frames taken immediately after a seek are torn
or plain grey. Thumbnails and filmstrips use an input seek followed by a second
output-side seek that decodes ~1.5 s past the target (`RESYNC_SECONDS` in
`trim.py`). This was the cause of the grey-thumbnail bug.

**It applies to trimming too, and that took a version to notice.** Until 1.1.1
the export path used a single input seek, under a docstring asserting the
encode "still lands exactly". It does not. Measured on real Box Pro footage,
which writes a keyframe every 1.000 s at 60 fps:

| Trim method | Frames below 30 dB vs an accurate reference | Time |
|---|---|---|
| Input seek only (what shipped) | **30 of 120** — 0.50 s of garbage at 12 dB | 1.1 s |
| Input seek to a lead-in, then output seek | **0** — bit-identical | 1.2 s |
| Output-only accurate seek | reference | 2.6 s |

The corrupt run is exactly the distance from the in point to the *next*
keyframe, so the worst case is a full GOP. ffmpeg reports no error and the
frame count is correct, which is why nothing caught it: the test asserted that
`-ss` preceded `-i`. `SEEK_LEAD_IN` in `presets.py` is the fix, and the lesson
is that a documented trap needs checking everywhere it could apply, not only
where it was first found.

**Joined exports carried this defect too, and the two halves ended
differently.** Re-encoding joins were reworked to decode each clip separately
and trim in the filter graph, which is exact. A joined *remux* cannot be fixed
the same way and is now refused instead.

A stream copy can only begin where a keyframe already is. The concat demuxer's
`inpoint` hands the muxer frames whose reference picture was never written, and
the result decodes with `Could not find ref with POC 64` and torn macroblocks —
while reporting success. Measured on the 1.000 s GOP fixture; the integration
suite deliberately builds that file, around the guard, and asserts it really is
broken. If a future ffmpeg handles it correctly that test fails, which is the
only honest way to hold a restriction in place.

A trimmed *single-clip* remux is fine and was measured to be: there the trim is
a seek, ffmpeg snaps to the keyframe before it, and the result is the "second
or so out" the README already promises rather than a torn picture.

**And the second seek was not measured from where it looked.** Found in 1.4.0,
while measuring the preview player — which always passes `-an`, and so hit it
constantly where the export only hit it sometimes.

Whether the first seek rebases the timeline depends on the file *and* on
whether audio is being written. So `-ss 0.5 -i clip.ts -ss 2.0` sometimes means
2.5 s and sometimes means 2.0 s. Measured on a fixture whose audio begins 23 ms
before its video, with the sound turned off, asking for 2.5 s produced a clean,
correctly lengthed export of **2.0 s onwards** — 15.5 dB against the same
footage, no ffmpeg error, right frame count. The same species of silent
wrongness as the mid-GOP bug, in the fix for the mid-GOP bug.

Nothing caught it because it needs a file whose format start time is not zero.
HDZero recordings start at zero, so real footage was exact at every position
tested, and every other trim test keeps the sound.

`-copyts -start_at_zero` pins the timeline so both seeks are measured from the
start of the file, and the second seek is now the in point itself rather than
the distance from the first. Verified exact on both file types at the same
speed. Untrimmed exports do not get the flags at all: nothing to seek to means
nothing to pin, and their command is unchanged.

| Candidate | Fixture with a 1.41 s start time | Real HDZero footage |
|---|---|---|
| Two seeks, as they were | 0.5 s early | exact |
| `-copyts -start_at_zero` | exact | exact |
| `-copyts` with an absolute target | 3.2 mean abs diff | exact |

The lesson repeats the one above it: a documented trap needs checking
everywhere it could apply, including inside the fix for it.

**Two-pass x264 needs identical stream configuration in both passes.** The
common advice to pass `-an` on the first pass shifts the video framing by one
frame on these files, and the second pass then dies with *"2nd pass has more
frames than 1st pass"*. Audio arguments go in both passes. There is a test
asserting this that checks argument *tokens*, not substrings — an earlier
version passed for the wrong reason because `-an` matches inside
`-analyzeduration`.

**ffprobe's default probe size is the right one.** Forcing
`-analyzeduration 100M -probesize 100M` costs about eight times as much when
reading from a card over USB (88 s versus 8.1 s for 122 clips) and returns
identical information on these files. `probe()` tries the defaults first and
only escalates when the result is incomplete.

**Hardware encoders must be detected by test-encoding, not by asking.** ffmpeg
reports three h264 hardware encoders as available on a machine where only one
works. `_encoder_runs()` performs a real three-frame encode. Hardcoding
`h264_amf` because it worked on the development machine would have failed on
the laptop, which is how this was found.

**ffmpeg options are not stable, and the app does not bundle one on Linux or
macOS.** `-fps_mode` replaced `-vsync` in ffmpeg 5.1. Ubuntu 22.04 ships 4.4,
the AppImage is built for 22.04 on purpose, and every re-encoding export plus
the filmstrip extraction used `-fps_mode` — so on that distribution every
export failed with `Unrecognized option 'fps_mode'` and the trim panel stayed
empty. `frame_rate_mode()` in `media.py` probes for it once, the same way the
hardware encoders are probed, and falls back to `-vsync`.

The general rule: the Windows build knows exactly which ffmpeg it has because
the binary is pinned, and the other two know nothing at all. Anything added to
a command that is newer than the oldest supported distribution's ffmpeg has to
be probed. The integration suite runs on `ubuntu-22.04` during the AppImage
build precisely so this class of problem shows up.

**The goggles have no clock battery.** The Box Pro's own log reports
`rtc_init has NOT detected a battery`, so the clock restarts from the same
stored value on every power-up and every clip carries nearly the same
timestamp. `timestamps_are_unreliable()` detects this and the UI offers a
manual flight date instead. The clip name's DVR counter is the real recording
order — that is why `natural_key()` exists.

**An export is a transaction, not a file being written.** ffmpeg gets `-y`, so
aiming it at the final path truncates whatever was there the moment it opens
the file. Until 1.1.1 a failed or cancelled overwrite therefore destroyed a
finished export, and cleanup made it worse by refusing to remove the wreckage
on the grounds that the file had existed beforehand. Encodes now go to
`<name>.flightdvr-part<ext>` beside the target — same directory, so `replace()`
is atomic — and land only after `_validate()` has proved there is video in
them. Exit code zero is not proof: a copy-without-re-encoding of a selection
containing no keyframe exits happily having written a 261-byte header.

**ffmpeg reports the cause first and the noise afterwards.** Rejecting a
127×95 encode emits `width not divisible by 2` and then nine lines of cascading
wrappers ending in `Conversion failed!`. Showing the user the last line, which
is what the app did, told them nothing. `_describe_failure()` prefers the first
component-tagged line that is not in `CASCADE_NOISE`.

**Both ffmpeg pipes must be drained at once.** Reading stderr only after stdout
closed meant a chatty encode could fill the stderr pipe, block ffmpeg writing
to it, and so stop the stdout the reader was waiting on. Neither side had a
timeout. Corrupt DVR footage — a recording cut short by a power loss — produces
exactly that volume of decoder warnings.

**Applying the flight date at queue time is wrong.** It has to be re-applied
when the date changes, or clips already queued silently keep the old one.
`Job.retarget()` and `_retarget_pending()` exist for this. It presents as user
error and is not.

---

## Platform differences

Three separate implementations of "what drives are there", chosen at runtime:

| Platform | Mechanism | Removable detected by |
|---|---|---|
| Windows | `GetLogicalDrives` / `GetDriveTypeW` via ctypes | `DRIVE_REMOVABLE` |
| Linux | Parsing `/proc/mounts` | sysfs `removable` flag, or a mount under `/media`, `/run/media`, `/mnt` |
| macOS | Listing `/Volumes` | anything that is not the startup disk |

`list_drives()` dispatches on `hasattr(ctypes, "windll")` then `sys.platform`.
The parsing is factored into pure functions — `parse_linux_mounts()` and
`macos_volumes_to_drives()` — so the Linux and macOS logic is tested on the
Windows development machine, from fixtures, without either OS present.

Two things worth knowing about the macOS path. The startup disk appears in
`/Volumes` as a symlink back to `/`, which is how it is identified. And a
second internal APFS volume gets its own device id, so it is reported as
removable; that is a harmless extra row rather than a bug worth shelling out
to `diskutil` for.

Elsewhere:

- **ffmpeg lookup** checks the PyInstaller bundle first, then PATH, then a
  fallback list. `/opt/homebrew/bin` matters more than it looks: a macOS app
  launched from Finder inherits a bare PATH containing neither Homebrew prefix.
- **Preview** uses `xdg-open` on Linux and `open` on macOS (`DESKTOP_OPEN` in
  `ui.py`), falling back from a directly-executed player. macOS installs
  players as app bundles with nothing on PATH, so `PLAYER_PATHS` lists the
  binaries inside `/Applications/*.app/Contents/MacOS/`.

---

## Building and releasing

### `--check`

Every packaged build answers `--check`: it starts Qt, reports the platform
plugin in use, resolves ffmpeg and exits. Exit codes are `0` fine, `3` no
ffmpeg found, `4` Qt raised on the way up. A platform-plugin failure usually
aborts the process rather than raising, so `4` is a backstop — the build
scripts treat anything that is not `0` or `3` as a failure either way.

Both build scripts and CI use it to prove a bundle works without a display or a
person, and it is the first thing to ask a user whose install will not start.
On Windows a GUI build has no stdout, so it reports through its exit code only;
`_say()` swallows the resulting `AttributeError` rather than crashing.

`--check` is not the whole story, because it never constructs the main window.
The spec drops files by name pattern, and dropping one too many produces a
window that will not build rather than an app that will not start. So every
build script follows `--check` by launching the packaged app offscreen for
twelve seconds and requiring it to still be running.

### Per platform

| Platform | Script | Output | Built where |
|---|---|---|---|
| Windows | `packaging/build.ps1` | per-user installer, ~95 MB | GitHub Actions, `windows-latest` |
| Linux | `packaging/build-appimage.sh` | AppImage | GitHub Actions, `ubuntu-22.04` |
| macOS | `packaging/build-macos.sh` | `.dmg` holding the `.app` | GitHub Actions, `macos-latest` |

All three share `packaging/flightdvr_studio.spec`, which branches on
`sys.platform` for the icon format, the macOS `BUNDLE` step, and whether ffmpeg
is bundled.

**ffmpeg is bundled on Windows only.** Windows users have no package manager to
supply one, and the app has to work on a machine that never had it. Linux and
macOS both do, so bundling there would mean redistributing a second GPL binary
and carrying a second corresponding-source offer for no user benefit.

**The AppImage is built on the oldest supported LTS on purpose.** An AppImage
carries no glibc; one built on Ubuntu 24.04 will not start on 22.04. If the
`ubuntu-22.04` runner label is ever retired, `ubuntu-latest` works but raises
the floor and will break users on older distributions.

**The macOS app is signed ad hoc, not with a Developer ID.** arm64 refuses to
load unsigned code at all, so `codesign --sign -` is required rather than
cosmetic — but it does not satisfy Gatekeeper, and users still need the
right-click-Open step. Proper notarisation needs a paid Apple Developer
account.

**The bundled Windows ffmpeg is pinned, and that is a compliance control
rather than tidiness.** `packaging/ffmpeg-build.json` records the exact archive
URL, its SHA-256, and the SHA-256 of both binaries. `build.ps1` refuses to
package anything that does not match, and regenerates
`ffmpeg-configuration.txt` from the binary it is actually shipping.
`packaging/fetch-ffmpeg.ps1` downloads that archive by URL and checks it before
unpacking, which is how the CI job gets one — fetching and verifying is a
stronger guarantee than a folder maintained by hand, and on 7 August 2026 the
pinned build had quietly vanished from the development machine. The notices
name that build and offer its corresponding source, so a silent swap would make
the attribution false; a test asserts the pin and the notices agree.

The build comes from BtbN/FFmpeg-Builds rather than a binary-only distributor,
because GPL v3 section 6 wants the source of every statically linked library
and the scripts that assembled them — and BtbN publishes all of it in a public
repository under an immutable tag. That turns corresponding source into a link
instead of a gigabyte this project would have to host forever.

Verified as a drop-in before switching: same trim and colour chain, both
builds, **PSNR `inf`** — bit-identical output. Anything less would have made
the measured findings above stale.

**The Windows installer is not in CI yet, but nothing blocks it now.** The
reason it was excluded — a CI job would fetch an unknown ffmpeg — is solved by
the pin above: a workflow can download the recorded URL, check the hash, and
fail if it drifts. What is left is installing Inno Setup on the runner.

### Releasing

1. Bump `__version__` in `flightdvr/__init__.py` and `AppVersion` in
   `packaging/installer.iss`. They are separate strings; both need changing.
2. Update `CHANGELOG.md`.
3. Push to `main` and let CI go green.
4. Tag `vX.Y.Z` and push the tag. CI drafts a release and attaches the AppImage
   and the DMG.
5. Build the Windows installer locally with `pwsh packaging\build.ps1`, attach
   it, and add its SHA-256 to the table in the README.
6. Publish.

The release is drafted rather than published precisely because step 5 is manual.

---

## The July 2026 review

An independent review found 18 confirmed defects. **All of them are fixed**,
across 1.1.1, 1.1.2 and the work after it. The integration suite was built
second, and it immediately found a nineteenth — `-fps_mode` breaking every
export on ffmpeg older than 5.1 — which is the argument for having built it.

Worth keeping in mind, because they are the shapes that recurred:

- **A well-formed command is not a working one.** Most of the eighteen were
  invisible to argument inspection, and several were guarded by tests that
  passed. Anything touching ffmpeg needs an integration test.
- **Exit code zero is not success.** A copy without re-encoding can write a
  container header and nothing else and exit happily.
- **The first clip is not the job.** Bitrate, audio, frame rate, dimensions and
  colour were all read off `clips[0]` and applied to everything.
- **A worker and the window must not share mutable state.** The queue race, the
  stale scan and the orphaned ffmpeg all came from that.
- **Options are not stable across ffmpeg versions**, and only the Windows build
  knows which ffmpeg it has.

<details>
<summary>What they were</summary>

Fixed in 1.1.1: failed overwrites destroying the previous export; mid-GOP
corruption at the start of every trim; the queue deadlocking on a full stderr
pipe; cancel not stopping ffmpeg; empty output reported as success; failures
reported as "Conversion failed!"; the missing LGPL text; the incomplete
corresponding-source offer; the unverified bundled ffmpeg; the Windows smoke
test that passed with no ffmpeg; the wrong About dialog.

Fixed in 1.1.2: `-fps_mode` on ffmpeg older than 5.1.

Fixed after: joined size targets sized from one clip; joined audio dropped by a
silent first clip; joined exports taking every property from the first clip;
concat lists overwriting each other; odd source dimensions; relative output
paths beginning with a dash; the queue mutating under a running worker;
case-insensitive output collisions; `.part` files left by interrupted copies;
free space measured against a folder that does not exist; superseded scans
updating a newer one.

</details>

## Known imprecisions

- **A joined segment can come out one frame short** — 359 where 360 were
  expected across two three-second cuts. The seam content is correct: frames
  either side match a standalone export at 44.7 dB. Likely rounding in the
  `fps` or `concat` filter.

- **A library copy cannot be stopped part way through a single file.** Cancel
  is checked between files, so a large one runs to completion. Nothing is left
  behind either way.

## The in-app preview player

Shipped in 1.4.0. Requested by boomz on Discord: play a clip in the window and
set trim points with a hotkey while it runs, the way avidemux does.

### Why not Qt's video widget

`QtMultimedia` decodes through Media Foundation on Windows — the same decoder
behind Windows Media Player, which the README already documents as unable to
play HEVC inside an MPEG-TS. That is the only format this app exists for, so
the obvious approach would pass every test on synthetic footage and fail on
every real recording. It is excluded from the packaged build, and
`test_nothing_reaches_for_qt_multimedia` asserts nothing imports it. **Do not
"simplify" the player by reaching for it.**

### How it fits together

`DecodeWorker` runs one ffmpeg per playback position, emitting raw `rgb24`
down a pipe at a size this module chose, and pushes `(seconds, bytes)` into a
bounded `queue.Queue`. `PreviewPlayer` drains that queue on a 30 Hz timer,
paced against `time.monotonic`, and emits the frame it decided to show.
`FrameView` paints it.

Three things that look like implementation detail and are not:

- **Frames travel by queue, lifecycle by signal.** A signal per frame piles up
  in Qt's event queue the moment the window stops draining. The bounded queue
  is also the back-pressure: UI stops taking frames → reader blocks → pipe
  fills → ffmpeg blocks. Do not "optimise" the bound away.
- **Late frames are dropped, not shown.** `PreviewPlayer._pick` takes the
  newest queued frame that is not still in the future and throws the rest
  away. Painting them would be catch-up in slow motion; the whole point of
  pacing against a clock is that a slow repaint costs one frame rather than a
  permanent lag.
- **The playhead comes from the frame that was painted**, not from the clock,
  so `I` means the picture on screen by construction.

`rgb24` rather than MJPEG down the same pipe: a frame is exactly
`width * height * 3` bytes, so framing is arithmetic and a short read
unambiguously means the stream ended. MJPEG would mean scanning for markers, a
JPEG round trip per frame, and generation loss in a preview whose job is to
predict the export.

### Two things measured that contradicted the design

**The preview needs no range conversion.** The plan called for
`scale=in_range=full:out_range=limited`, matching the export. Measured: in a
chain that ends in `rgb24` the range filters are inert, because the conversion
out of YUV already reads the source's range tag. Applying full-to-limited,
applying its opposite, and applying neither all produce **byte-identical**
frames, and all three match the export decoded back to RGB to within H.264's
own loss (1.46 mean absolute difference per byte). The filter was removed. The
export still needs its own, because the export's output *is* YUV.

**The second seek was not measured from where it looked.** See the entry in
"Traps in this footage" below — it turned out to be a live defect in the export
path too.

### Measured on real footage

`F:\FPV clips\hdz_022.ts`, 720p60 full range, through `PreviewPlayer` with a
real event loop:

| | result |
|---|---|
| First frame, from 0 s | 0.16 s |
| First frame, seek to 90 s | 0.38 s, landing exactly on 90.00 s |
| Clock against picture | −12 ms of drift over 2.3 s |
| A 1 s skip forward | served from the queue, no respawn, 0.00 s wait |
| An accurate seek to 90 s, for comparison | 13.5 s — 42× slower |
| Decoders left running after close | 0 |

### Traps that were predicted, and held

- **`_show_frame` is guarded with `if self.player.is_playing: return`.**
  Without it every press of `I` goes through `_on_trim_changed` and paints a
  stale filmstrip JPEG over the live video. `verify_window.py` in the
  scratchpad exercises exactly this.
- **`TrimBar.set_playhead` does not emit `playhead_moved`**, or the window
  rebounds into `_show_frame`. It also repaints only when the marker changes
  pixel: `paintEvent` rescales every visible tile with `SmoothTransformation`,
  so at 30 Hz the bar would cost more than the decoder and the decoder would
  get the blame.
- **Retired workers are retained** until they stop running. A collected
  `QThread` takes its process with it.
- **Shutdown order is flag → stop the process → `wait()`.** The reader blocks
  inside `readinto`, where a flag is never seen.

### Two hazards the permanent panel created

Making the panel permanent removed the checkbox that used to gate all of this,
which turned two dormant problems into live ones:

- Holding the down arrow through the clip list started a **full filmstrip
  decode for every row it passed**. A 250 ms single-shot timer
  (`_select_timer`) now debounces selection.
- The previous `FilmstripLoader` was **dropped rather than retained** when a
  new clip was selected. `_retired_strips` holds them, same as `_retired_scans`.

### The layout around it

The first run on a full card showed three things eating the same space, and
they compound. Each fix is small and none is obvious from the code alone.

**The preview's height follows from its width.** `PreviewPanel.resizeEvent`
sets its own height to `useful_height(width)` — the height at which the picture
exactly fills the box. Past that every pixel is a black bar; short of it every
missing pixel is black down the sides. There is one right answer, so there is
no vertical splitter: a handle could only choose how much black to look at.
Widening the left column is what makes the picture bigger, and the clip list
takes whatever the picture cannot use.

Two attempts failed before this one, and both are worth not repeating:

- Computing it in `MainWindow.resizeEvent` made the answer depend on which
  resize Qt delivered first. The picture came out at 63% of the width it could
  have had, and the number changed between runs.
- `heightForWidth` looked like the Qt-native answer, but the layout took the
  height from `sizeHint()`, which was computed from a stale `self.width()`.

Driving it from the panel's own `resizeEvent` works because the panel's width
is the only input and setting its height cannot change it, so it settles in one
pass. `useful_height` measures the inset and the chrome off the picture rather
than deriving them from `contentsMargins`: a group box's title and frame cost
about twenty pixels more than the margins report, and deriving them left the
picture short of the width every time.

**Thumbnails are bounded by the list's height, not just its width.**
`_sync_thumbnail_size` sized rows from the column width alone, and with
`THUMB_WIDTH` at 240 that gave 141 px rows — two clips visible however much
vertical space the list had. `MIN_VISIBLE_CLIPS` is the second bound. It is
called deferred, via `QTimer.singleShot(0, ...)`, because at the moment the
window resizes the list's viewport still reports the height it is about to stop
having.

**The queue starts collapsed.** An empty queue was holding two hundred pixels.
`Open output folder` and `About` live on the always-visible header strip rather
than in the body — About carries the GPL and LGPL notices, and a licence you
can only reach by opening a queue you have no jobs in is not much of a notice.
`_rebuild_queue` is the single funnel that opens it and writes the summary;
`_start` opens it too, so Cancel is never hidden at the moment it is wanted.

### Two kinds of stop

`media.py` has both `request_stop` and `stop_process`, and which one to call
depends entirely on the thread you are on.

`stop_process` is the escalation: terminate, wait, kill, wait. Two bounded
waits. That is right on a worker thread and is a frozen window on the UI one —
and cancelling an export, seeking, changing clip, pressing Escape and closing
all arrive on the UI thread. `request_stop` only asks.

It is safe to only ask because every worker runs the full escalation in its own
cleanup. The ask exists to unblock the read the worker is sitting in, which
terminating does by closing the pipe; the worker then tidies up after itself.
A test asserts that none of the three UI-facing stops reaches `stop_process`.

### Filmstrip extraction

Rewritten after the PR #9 review. Three things it now does that it did not:

**It can be stopped.** It was a blocking `subprocess.run(timeout=600)` with no
handle on the process, so on a slow card or a damaged recording the window
would not close. It is a `Popen` now, registered with the loader.

**Frames are staged and moved into place.** Select A, then B, then A again, and
a second extraction of A starts while the first is still going. Both used to
delete and rewrite the same cache directory underneath each other. Each one
now builds in a directory of its own and renames it into place at the end;
whoever gets there first wins, and the loser reads what is already there. The
rename is the whole mechanism — no locks.

**A cancelled extraction publishes nothing.** This one was introduced by the
rewrite and caught by running it: terminating ffmpeg makes `communicate()`
return *normally*, so a cancelled extraction ran straight on into the
publishing code with whatever frames it had reached. The cache check only
counts frames against times, so that truncated filmstrip would then be believed
for as long as the cache survived. A genuine decode *failure* still publishes
what it managed — half a filmstrip of a damaged recording is worth having, half
of one nobody waited for is not.

Loaders also carry a generation, for the same reason `ScanWorker` does: matching
on the clip's path alone accepts an overtaken extraction of the clip you are
back on.

### Precise frame window

Playback is deliberately 30 fps; precise trimming is deliberately not. Comma
and period decode 121 native-rate frames around the paused playhead — sixty
steps either side — and Shift moves ten source frames. That is roughly one
second each way at 60 fps and two thirds of a second at 90 fps. The whole-clip
filmstrip remains the coarse navigation path.

`FrameWindowWorker` emits 480x270 raw RGB frames, the smallest playback size,
with `showinfo` in the filter chain. Precise stepping used to drop to 320x180,
so asking for the exact cut made the picture softer than playback. Each cached
picture carries the PTS ffmpeg reported rather than a timestamp invented by the
UI. The source frame number is derived from that PTS and the probed source rate.
A clip whose rate could not be probed is refused honestly; there is no exact
frame number to show.

The cache has two bounds: the command receives `-frames:v 121`, and
`FrameCache` independently stops accepting at 121 frames. That is 44.87 MiB of
pixels at 480x270. A refill replaces the dictionary rather than extending it.
Measured on real 720p60 footage (`D:\movies\hdz_047.ts`), two windows decoded in
1.16–1.29 s and returned exactly their planned ranges, 540–660 and 600–720.
Source frame 630 was byte-identical in both at PTS 10.499989 s.

One ffmpeg boundary trap needed a different shape rather than a larger fudge.
Output-side `-ss` disagrees across ffmpeg builds about the picture on its
boundary: a planned 180–420 cache held 181–421 on real footage, and aiming half
a frame earlier fixed the pinned Windows build but still returned N+1..M+1 in
all three CI builds. The precise command now does only the fast input seek,
decodes the two-second resync lead-in, and uses a timestamp selection filter
halfway between frames N-1 and N. `showinfo` comes after that filter, so the
current 10 s real-footage window produces 121 pictures and 121 timestamps rather
than logging discarded frames too.

Hosted ffmpeg builds exposed one more ordering detail: `showinfo` can process
one to six frames beyond `-frames:v` before output stops, so they log 122–127
timestamps for 121 pictures. Because selection is before `showinfo`, those can
only be trailing lookahead; pictures pair with the *head* of the PTS list.
Pairing requires at least one timestamp per picture, the planned first frame,
and a contiguous run. A filter-order change or broken timeline therefore fails
visibly instead of publishing plausible but wrong source-frame numbers. The
real measurements still returned 540–660 and 600–720 exactly after this change.

### Deliberately not done

No audio — a second pipe, a second clock and an output device, for footage
whose soundtrack is motor whine. No reverse play: a forward-only pipe cannot do
it honestly. Both are said in the UI so their absence is not filed as a bug.

## Outstanding

Planned work, and what is deliberately not planned, is in
[ROADMAP.md](ROADMAP.md). What follows is the smaller stuff: known gaps with no
release attached to them yet.


- **VAAPI hardware encoding on Linux.** The current design swaps encoder
  arguments; VAAPI needs `-vaapi_device` before the input and
  `format=nv12,hwupload` in the filter chain, which does not fit that shape.
  Note that WSL cannot test this — it exposes `/dev/dxg`, not `/dev/dri` — so
  it needs real hardware.
- **Intel macOS builds.** One more matrix entry; `build-macos.sh` already names
  its output by architecture. Left out because the Intel runners are being
  retired and every Mac sold since late 2020 is arm64.
- **Flatpak packaging.** Would need preview rerouted through the xdg-open
  portal rather than executing a player directly.
- **Windows installer in CI.** See above — blocked on pinning ffmpeg properly.
- **Splitting `ui.py`.** See above.

## Deliberately not done

- **Bundling ffmpeg on Linux or macOS.** Reasoning above.
- **Converting colour tags by default.** Reasoning above.
- **Parallel exports.** ffmpeg already saturates every core; running two at
  once makes both slower and the progress display meaningless.
- **A codec-detection cache keyed on the file path.** Cards get reused and
  rewritten with the same names.

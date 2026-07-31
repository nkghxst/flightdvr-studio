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
| `flightdvr/ui.py` | The window, and everything that is not one of the above |
| `packaging/` | Per-platform build scripts, the PyInstaller spec, the icon |
| `tools/` | Developer utilities: icon, screenshots, demo GIF, colour comparison |

`ui.py` is by far the largest file. It has not been split because the widgets
are heavily interdependent and every attempted seam so far has been arbitrary.
If you do split it, the natural line is the trim panel and the queue table,
which touch the rest through a small number of signals.

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

**Joined exports still carry this defect**, because concat `inpoint` seeks the
same way. That is why they are on the outstanding list rather than merely
imperfect.

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
| Windows | `packaging/build.ps1` | per-user installer, ~110 MB | by hand |
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
`ffmpeg-configuration.txt` from the binary it is actually shipping. The notices
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

## Known defects, still unfixed

An independent review in July 2026 found 18 confirmed defects. 1.1.1 fixed the
ones that make an export untrustworthy. These remain, roughly in order of how
much they matter:

- **Joined exports are unreliable and should be treated as unsupported.**
  `build_commands()` receives `job.clips[0]`, so a joined job takes its
  bitrate, audio presence, frame rate, dimensions and colour handling from the
  first clip alone. A size-targeted join of two clips produces roughly double
  the target; joining a silent clip with one that has audio drops all audio.
  Queueing a second join whose first clip matches an existing job also
  overwrites that job's concat file. Fix by refusing incompatible joins with a
  clear message before attempting anything cleverer.
- **The queue can be edited while the worker is running it.** Remove and Clear
  mutate the list the worker is iterating, so clearing mid-export still encodes
  pending jobs and progress signals can index a shortened list.
- **Interrupted library copies leave `.part` files** and cannot be cancelled
  part way through a single file.
- **Duplicate-output detection compares paths as case-sensitive strings**, so
  on Windows and default macOS two differently-cased names collide silently.
- **A relative output folder beginning with `-` is parsed as an ffmpeg option.**
  Make output directories absolute when they are chosen.
- **Odd source or output dimensions fail** — only the computed width is forced
  even, and only during an explicit downscale. Cannot occur with HDZero
  footage, which is always even, but imported files can trigger it.
- **Scan shutdown does not cancel in-flight probes**, so a stale worker can
  emit `done` during a later scan.
- **Free-space checking skips nested new destinations**, walking up only one
  level rather than to the first existing ancestor.
Fixed since that review, beyond the 1.1.1 list: the corresponding source offer
for the bundled Windows ffmpeg, the unverified ffmpeg going into the installer,
and the Windows smoke test that passed when ffmpeg was missing because the
resulting modal kept the process alive.

The review's sharpest point was about the tests, and it stands: they assert
that a command *contains the right arguments*, never that it *produces the
right media*. Every defect above and every one fixed in 1.1.1 would have been
caught on the day it was written by a small integration suite running real
ffmpeg against synthetic fixtures. Build that before adding features.

## Outstanding

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

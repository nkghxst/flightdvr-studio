# FlightDVR Studio

*Browse, trim and convert HDZero goggle DVR footage.*

Pull a card out of your goggles and you have a folder of `.ts` files with names
like `hdz_047.ts`, no way to tell them apart, and a format most editors refuse to
open. FlightDVR Studio turns that into something you can use: browse the card
with thumbnails, preview and trim clips, then export them as an edit-ready
mezzanine, a high-quality MP4, or something small enough for WhatsApp.

It also fixes the colour problem these recordings have, which is the reason
footage converted with a plain ffmpeg command often comes out with crushed
blacks.

![Ticking clips, trimming one, and exporting](docs/demo.gif)

Free and open source under the GPL v3. Nothing is gated and there is no
account, no telemetry, and nothing to sign up for.

The one thing it does over the network is check whether a newer version exists:
a single request, at most once a day, to this project's own releases page on
GitHub. Nothing is sent beyond what any web request unavoidably reveals — an IP
address and the app's version — and nothing is downloaded or installed; a newer
release just shows a link. Untick **Check for updates** in the About box and no
request is made at all.

> Not affiliated with, endorsed by, or supported by HDZero. It is an independent
> tool that reads files their goggles produce.

---

## Installing

### Windows

Run the downloaded **`FlightDVRStudio-<version>-Setup.exe`**. It installs
per-user, so there is no administrator prompt, and it puts ffmpeg inside the
install folder — the machine needs nothing else installed.

About 95 MB to download, 340 MB installed. Uninstalls from Add/Remove Programs.

Windows 10 or 11, 64-bit.

Windows will warn you that it "protected your PC", because the installer is not
code-signed. Click **More info**, then **Run anyway**. See
[troubleshooting](#troubleshooting) for why.

### Linux

Download **`FlightDVR_Studio-*.AppImage`**, make it executable, and run it:

```bash
chmod +x FlightDVR_Studio-*.AppImage
./FlightDVR_Studio-*.AppImage
```

About 63 MB. One file, nothing installed, delete it to uninstall. It is built on
Ubuntu 22.04 and needs glibc 2.35 or newer. Some supported enterprise
distributions still carry an older glibc; `ldd --version` reports yours.

Unlike the Windows build it does **not** carry its own ffmpeg, because your
distribution already ships a maintained one:

```bash
sudo apt install ffmpeg      # Debian, Ubuntu, Mint, Pop!_OS
sudo dnf install ffmpeg      # Nobara; Fedora after enabling RPM Fusion
sudo pacman -S ffmpeg        # Arch, Manjaro
```

Fedora's own `ffmpeg-free` package has limited codec support and does not cover
every FlightDVR export. Enable [RPM Fusion](https://rpmfusion.org/Configuration)
and install its full `ffmpeg` package first.

Some image-based systems already include a suitable ffmpeg. Run the app with
`--check` before installing anything to see exactly what it found.

Playback happens in the window and needs nothing else. Install VLC or mpv as
well if you want **Open in player…** to have somewhere to send a clip.

### macOS

Download **`FlightDVR-Studio-*-arm64.dmg`**, open it, and drag the app to
Applications. About 30 MB. Apple Silicon, macOS 11 or newer. Intel Macs are not
built for today — [ask](https://github.com/nkghxst/flightdvr-studio/issues) if
you need one and it is a small change to the build.

The app is not signed with an Apple Developer certificate, so the first launch
is refused. **Right-click the app and choose Open**, then confirm — you only
need to do this once. If macOS still refuses:

```bash
xattr -dr com.apple.quarantine "/Applications/FlightDVR Studio.app"
```

ffmpeg is not bundled here either. Install it with
[Homebrew](https://brew.sh):

```bash
brew install ffmpeg
```

### Verifying your download

Every release publishes the SHA-256 of each file, so you can confirm that what
you downloaded is what was built. None of these are code-signed, so this is the
only check that means anything.

```powershell
Get-FileHash FlightDVRStudio-1.5.0-Setup.exe -Algorithm SHA256
```

```bash
sha256sum FlightDVR_Studio-1.5.0-x86_64.AppImage    # Linux
shasum -a 256 FlightDVR-Studio-1.5.0-arm64.dmg      # macOS
```

**1.5.0**

| File | SHA-256 |
|---|---|
| `FlightDVRStudio-1.5.0-Setup.exe` | `686f0ec87413d59e9aad2e47a3e84ff60367723b9ebd7e1a3563334445f36c6d` |
| `FlightDVR_Studio-1.5.0-x86_64.AppImage` | `086dac7f008fee4481384d1acac64a52cd538d32a0056f26ca90401265048b7d` |
| `FlightDVR-Studio-1.5.0-arm64.dmg` | `98526cc451c655b74be69f5462006e78f4b55f7ec90359473a9445617fe2a175` |

<details>
<summary>Earlier releases</summary>

**1.4.0** — no sessions, so closing the window loses every trim and review; one
range per clip; no review states; and every launch checks for updates with no
way to turn it off.

| File | SHA-256 |
|---|---|
| `FlightDVRStudio-1.4.0-Setup.exe` | `a8b67589e472fb91f22846fba6b50d43fae43c209d2dd2fb5c11233fe04d6c9e` |
| `FlightDVR_Studio-1.4.0-x86_64.AppImage` | `5ce81819683fc70e3a5153e40c915033eb26ce9165545cdd66fbf8c62634e54c` |
| `FlightDVR-Studio-1.4.0-arm64.dmg` | `62f4828723f1b9ad62cee152bc3fc5c4fc6a72dad124246f58c848f7956f2fef` |

**1.3.0** — no player in the window, so trimming means setting two numbers and
finding out afterwards. Joining trimmed clips with Remux writes a corrupt file
instead of refusing, and the low-space warning raises an error rather than
warning.

| File | SHA-256 |
|---|---|
| `FlightDVRStudio-1.3.0-Setup.exe` | `9c3de6763e383e4f3bd4eb1be85918683bcdd695bc353f75a4611c814a4845d0` |
| `FlightDVR_Studio-1.3.0-x86_64.AppImage` | `ad1fd20398fbba66ace8f88ac6518365ac8c0c0e4ab01d1d58b4ca255bc98600` |
| `FlightDVR-Studio-1.3.0-arm64.dmg` | `b883f2e83afc0bb96c386d8745197e7a41c3e16d8856a2b1711ba758aaeecc55` |

**1.2.0** — no Upload preset and no update check, so it never tells you a newer
version exists. Everything else works.

| File | SHA-256 |
|---|---|
| `FlightDVRStudio-1.2.0-Setup.exe` | `6f8d8c1b90a601b3bbb4bb10fbdb95f3efae4dc976fd673a52eba990abfdf7e5` |
| `FlightDVR_Studio-1.2.0-x86_64.AppImage` | `c432130ad29aae7c5172538a72e8a3bae4000c58496045d3a6aad7b9440616ab` |
| `FlightDVR-Studio-1.2.0-arm64.dmg` | `8eabb6c3230aa0c4599d4773d65759d513d7fb27a5c28f233a2c6bcf5a166928` |

**1.1.2** — joining clips into one file produced a result that was quietly
wrong. Everything else works.

| File | SHA-256 |
|---|---|
| `FlightDVRStudio-1.1.2-Setup.exe` | `a0262b6aa7809757e7f83b29da77deeacf8d1a81c056df6be1ea4a2a7cdd81ea` |
| `FlightDVR_Studio-1.1.2-x86_64.AppImage` | `3c34cc807709c32ec76f67e373731d29ff574dbae590b2ab0215a73ea373827c` |
| `FlightDVR-Studio-1.1.2-arm64.dmg` | `da548b3f9f1289943a7ec7b7bc8894a44c9994f6b8eee954f033c8bfb6fb1b31` |

**1.1.1** — fine on Windows and macOS, and on Linux with ffmpeg 5.1 or newer.
On older ffmpeg, which Ubuntu 22.04 and Debian 11 ship, exports fail and the
filmstrip is empty. 1.1.2 fixes that and changes nothing else.

| File | SHA-256 |
|---|---|
| `FlightDVRStudio-1.1.1-Setup.exe` | `f0ed2872217fa831c593ab052ffab7ac9b6205952609472368d6f49a3306e8e0` |
| `FlightDVR_Studio-1.1.1-x86_64.AppImage` | `0d53c8677cbd090c00b66e7c5c4a01329a79e0101e6d4da1036d5eb2e350134b` |
| `FlightDVR-Studio-1.1.1-arm64.dmg` | `77aeb1edba0f8fa173a479cba74d54d4da1d533802ab057a1581a15caf926035` |

**1.1.0** — superseded by 1.1.1, which fixes corrupt trimmed exports and a
failed export destroying the file it was replacing. Still downloadable, but
there is no reason to choose it.

| File | SHA-256 |
|---|---|
| `FlightDVRStudio-1.1.0-Setup.exe` | `228b5a4bd214be4a54143e7fd2d08b1281d480a735da508bba4f9211e148ebea` |
| `FlightDVR_Studio-1.1.0-x86_64.AppImage` | `8526a1cc19be7813a4b2b55b41a36cf14913cc95258e0ddf40761b493de3ffe4` |
| `FlightDVR-Studio-1.1.0-arm64.dmg` | `1d9f0493150406a36c9f330a215a5fa20471fc45a1248ce7dc8248e8b7aa30d3` |

**1.0.0** — Windows only.

| File | SHA-256 |
|---|---|
| `FlightDVRStudio-1.0.0-Setup.exe` | `02800a47b6d214d223988a35ffc8fba31e726c4291c872d525d0eeda479e1ce5` |

</details>

## Quick start

1. Put the goggle card in a reader and press **Find SD card**, or **Browse** to
   any folder of recordings.
2. Press **Scan**. Clips appear with thumbnails, length, size and format.
3. Tick the ones you want. Double-click any clip to watch it first.
4. Pick an export preset on the right.
5. **Add to queue**, then **Start export**.

That is the whole loop. Everything below is detail.

---

## The clip browser

![The clip list, with thumbnails, review states and export settings](docs/01-browse.png)

Clips are listed with a thumbnail taken from a representative frame, plus
length, size, card date and format. Click any column heading to sort by it. The
default order is by clip name, which is the DVR's own counter and the only
reliable record of the order things were filmed in — see
[the clock problem](#the-goggles-cannot-keep-time).

Double-clicking a row plays it in the preview below the list. **Open in
player…** hands the highlighted clip to your usual video player instead, for
when you want a full screen or a scrubbing bar of your own. VLC or mpv is used
when either is installed, because Windows registers `.ts` to Media Player,
which opens the file and then often cannot decode the video inside.

Clips that already have an export for the current preset are marked
**✓ exported**, so on a full card you can see at a glance what is left to do.

Widening the window makes the thumbnails bigger rather than leaving an empty
filename column.

## Reviewing a card

A full card is a hundred-odd clips, most of which you will not keep. The
**State** column is how you get through them without opening each one twice.

Click a clip and press a key, or use the **Mark:** buttons:

| | | |
|---|---|---|
| `U` | **Unreviewed** | the starting state |
| `K` | **Keep** | worth exporting |
| `M` | **Maybe** | not now |
| `R` | **Reject** | no |

*Maybe* earns its place. On a long card most decisions are "not now", and
without somewhere to put them they end up as Keep and you sort it out later,
which is the same job again.

The row is tinted faintly to match — green, amber, red, and nothing for
unreviewed. The colour is reinforcement, not the message: the letter in the
column is what actually says the state, so nothing is lost if the greens and
reds look alike to you.

**Show:** filters the list to one state, which is the fastest way through a
card: mark everything once, then show only Keep. The count beside it says how
far through you are.

Two more things appear under the letter when they are known:

- **`2 ranges`** — how many parts of that clip you have marked for export. See
  [several ranges from one clip](#preview-and-trimming).
- **`2 flights`** — roughly how many arm-fly-land cycles the footage looks like
  it holds, read from the filmstrip rather than from any telemetry. This is a
  guess from motion, and the tooltip says so. A readable clip with none says
  **`no flying`**. Until the clip has been read — or when its feed is too noisy
  to tell flying from stopped — the line stays blank rather than inventing an
  answer.

That reading happens on its own, after scanning and thumbnails have finished,
one clip at a time and at low priority. Clips you have already opened are free,
because it reuses the same filmstrip. Clicking a clip stops it immediately, so
it never makes the app feel slower — and it starts again where it left off.

## Sessions

The work you do on a card is saved as you go. Trim ranges, review states, range
names and the export settings you chose all belong to the folder you are
working on, and come back when you open it again. Outputs still present for
those settings are recognised as already exported. There is nothing to press.

The **Session** menu is for the cases that need more than that:

| | |
|---|---|
| **Save session as…** | give this one a name and put it where you like |
| **Open session…** | reopen one from anywhere |
| **Recent sessions** | the last few, by name |

A session references your footage rather than containing it, so it is small,
and it can be backed up or moved without taking the video with it.

**Clips are identified by path, size and modification time together — never by
name alone.** Cards get wiped and reused, and `hdz_004.ts` next month is a
different flight from `hdz_004.ts` today. A session that confidently attached
last week's trim points to this week's footage would be worse than one that
remembered nothing, so a clip it cannot match is reported rather than guessed
at.

## Preview and trimming

![The preview panel, editing the second of three ranges in one recording](docs/02-trim.png)

Select a clip and it loads into the preview under the list, with its filmstrip
across the bottom of the window. **Play** runs it there — no other program, no
handing the file to something that cannot decode it.

Trimming chooses the useful part of a recording. HDZero DVR starts at arming,
so there is not normally a long quiet section before take-off. Drag either
handle on the filmstrip, or press **I** and **O** while the clip plays to cut at
the picture in front of you. **Set in** / **Set out** do the same from the
buttons, and **Reset** restores the whole clip. The list then shows the range
next to the length, and the size estimate, progress and time remaining all
follow the trimmed length.

The reading below the filmstrip says roughly how much movement it found. When
one run looks useful, **Trim to the flying** offers it as a starting point; the
app never applies that guess unless you press the button.

**Add range** (or **N** with the picture focused) keeps another part of the same
recording. When a clip has several ranges, the preview says which range you are
editing and offers a name that can distinguish its exported filename. Each
range can be exported separately or kept in order when clips are joined.

For the exact cut, pause and use **,** / **.** to step through real source
frames (hold **Shift** for ten). The preview decodes only a small window around
the playhead, shows its exact timestamp and source frame number, and replaces
that window when you seek elsewhere rather than decoding the whole recording.

**Grab still…** saves the frame you are paused on as a full-resolution PNG. It
uses the frame number and timestamp the preview decoder has already established
rather than picking a nearby picture from the wall clock, and it saves at the
source's own dimensions — what the preview scaled to fit the window has no
effect on the file.

It offers a name built from the naming template and the Output box, with a
`still` suffix, then lets you change it: the save dialog is where the still
finally lands, unlike an export, which goes where the queue said it would. The
button only does anything when a real decoded frame is on screen. The PNG is
written beside its target and moved into place only once ffmpeg has produced a
readable image, so a cancelled or failed capture leaves nothing behind and never
damages a file already there.

The preview is silent. Sound would need a second pipe and a second clock, and
DVR audio is motor whine — an in point is something you find by eye.

Playback decodes through ffmpeg rather than through Qt's video widget. On
Windows that widget uses Media Foundation, the same decoder behind Media
Player, which cannot handle HEVC inside an MPEG-TS — so it would work
perfectly on test footage and fail on every real recording.

Frames are pulled from the clip the first time you select it — a three minute
recording takes about four seconds, and is cached afterwards, so dragging is
instant rather than waiting on a seek.

Trims survive into a joined export: each clip keeps its own in and out points.
*Remux* is the exception and will say so — see below.

**Joining clips into one file** is the *Join the ticked clips into one file*
box, for when the DVR split a single flight across several recordings. Clips
are joined in DVR counter order rather than by timestamp, because
[the goggles cannot keep time](#the-goggles-cannot-keep-time).

Clips that do not match each other are fine. Different sizes, frame rates and
codecs are brought to a common format — the largest size and rate among them,
so nothing is thrown away to suit the smallest — and a clip with no sound gets
silence rather than removing the audio from the rest. Only *Remux* refuses a
mismatched set, because copying without re-encoding cannot change anything.

> **Remux is the exception.** It does no re-encoding, so it can only cut where a
> keyframe already is, and a trimmed rewrap may be a second or so out. Every
> other preset is frame-accurate.
>
> Joining *trimmed* clips with Remux is refused rather than done badly: the
> result plays and reports the right length while every join is torn. Reset the
> trims to join them untouched, or pick a re-encoding preset to keep them.

## Export presets

| Preset | Produces | Roughly | Use it for |
|---|---|---|---|
| **Edit** | DNxHR or ProRes in `.mov` | 58 GB/hour | DaVinci timelines, handing to an editor |
| **Master** | H.264 `.mp4`, quality-based | 11 GB/hour | archiving, sending to editors online |
| **Social** | H.264 `.mp4`, size-targeted | you choose | WhatsApp, Instagram, Discord |
| **Upload** | H.264 `.mp4` at 1080p or above | 20 GB/hour | YouTube, Instagram, Reddit |
| **Vertical** | H.264 `.mp4`, 9:16 crop | 11 GB/hour | Reels, Shorts, TikTok, sending to a phone |
| **Remux** | `.ts` → `.mp4`, no re-encode | same as source | instant lossless rewrap |
| **Slow motion** | H.264 `.mp4` at half speed | 18 GB/hour | showing the moment something went wrong |

Sizes are for 720p60; other resolutions are scaled accordingly. Slow motion is
per hour of *recording* — the file it writes runs for two.

**Edit** exists because the free version of DaVinci Resolve on Windows cannot
decode H.265, so the original files cannot go on a timeline at all. Mezzanine
codecs are also intra-frame, so scrubbing is instant instead of stuttering.
DNxHR SQ, HQ and LB and ProRes 422 and LT are all available.

**Master** quality is chosen by name — Archive, High, Good, Compact — with the
underlying CRF number shown alongside. High is visually indistinguishable from
the original.

**Upload** is the only preset that will make the picture *bigger*, and it is
worth being clear about why, because upscaling usually deserves suspicion. It
does not recover detail the goggles never recorded — nothing can. What it does
is arrive at a site in a higher resolution tier. YouTube, Instagram and Reddit
re-encode everything they receive and decide how much bitrate to spend based on
the resolution you sent, so a 1080p upload gets a bigger allowance than a 720p
one and more of your footage survives their encode. The picture going in is the
same; the difference is how kindly the platform treats it.

It is quality-based and never size-targeted. Spreading a fixed byte budget over
2.25 times as many pixels is worse than doing neither, so that combination is
not offered.

**Social** hits an exact file size using a two-pass encode, landing within about
one percent of the number you ask for. It can also downscale and halve the frame
rate. Only sizes smaller than the source are offered, so it never upscales.

**Vertical** crops a 9:16 slice out of the recording for phone feeds. It crops
rather than padding, so the picture fills the screen instead of sitting in a
letterbox — and because a 16:9 recording is much wider than 9:16 is, *which*
slice you keep is a real decision. A slider chooses it from left to right, or
drag the crop on the preview itself; the shaded area is what will be thrown
away.

The frame it takes is the largest exact 9:16 rectangle in the source. A 720p60
recording gives a 396×704 crop, delivered at 720×1280; a 1080p one gives
594×1056 at 1080×1920. That costs eight lines top and bottom at 720p, which is
the price of an exactly square pixel: the alternative that keeps the full height
is 0.25% out, and ffmpeg hides that difference in a sample-aspect flag rather
than in the picture, so it looks correct until a platform ignores the flag.
Taking the eight lines is the version that is true everywhere.

A source too narrow to hold a 9:16 crop is refused with both numbers rather than
padded with bars.

Worth knowing before you use it: **the goggle OSD lives at the edges of the
frame**, so a vertical crop cuts off the timer, battery and warnings at the
sides. That is unavoidable in a 9:16 slice of a 16:9 recording, and the preview
shows exactly what goes.

**Slow motion** plays the recording at half speed while keeping every frame that
was recorded. Nothing is invented between frames: a 60 fps source becomes 30 fps,
and a 90 fps source becomes 45 fps. The output rate comes from the recording
rather than being rounded to the frame rates the Social preset offers.

It has its own quality setting, independent of Master, so changing one does not
silently change the other. Sound is dropped, whatever the **Keep the audio
track** tickbox says: audio at half pitch is not slow motion, and normal-speed
audio over slowed video drifts apart within seconds.

It refuses a clip whose frame rate cannot be read, and a joined set recorded at
different rates. Every other preset may reasonably guess at a rate; this is the
only one that promises to keep every frame exactly once, and neither case can
keep that promise without inventing or discarding frames. That refusal is worth
having: before it existed, falling back to 60 threw away a third of the frames
of a 90 fps recording and reported a successful export.

**Keep the audio track** can be turned off on any preset. DVR audio is mostly
motor noise and wind, and dropping it buys bitrate on a size-targeted export.

### Hardware encoding

If your machine has a usable hardware encoder, a checkbox offers it and names
which one — NVIDIA NVENC, Intel Quick Sync, AMD AMF or Apple VideoToolbox. It is
usually faster than software encoding, but the speed, quality and file-size
trade-offs depend on the encoder, GPU and driver.

Availability is decided by *running* a test encode at startup, not by asking
ffmpeg what it supports. A build can advertise three encoders on a machine that
has none of the hardware.

## Copying originals off the card

**Copy originals to library** copies the supported source files unchanged into
folders named after the flight date. Each one is written through a temporary
`.part` file, checked to be the same size as the source and then moved into
place; an existing same-size file is skipped. Nothing is converted, and nothing
is deleted from the card — clearing it is left to you deliberately.

## The queue

![The export queue partway through, with progress and time remaining](docs/03-queue.png)

Rows show the filename that will be **written**, so the effect of your settings
is visible before anything starts. Changing an output setting re-targets jobs
that have not begun yet; jobs already running or finished are left alone.

**Remove selected** drops rows and **Clear queue** empties it. Neither touches a
job that is currently encoding — cancel that instead. Double-clicking a finished
row opens what it produced.

Below the queue is an overall progress bar with elapsed and estimated remaining
time, weighted by footage length rather than job count.

### Naming what comes out

The queue shows the filename that will be written, and the **Name** field under
Output decides how that name is built. It takes a template and shows an example
as you type. The default is:

```
{date}_{clip}_{range_number}_{range}_{preset}
```

| Field | Means |
|---|---|
| `{date}` | the flight date, as used when copying originals to the library |
| `{session}` | the session name, when one has been given |
| `{clip}` | the recording's filename without its extension |
| `{range_number}` | the one-based number, when a clip has more than one range |
| `{range}` | the name of that range, when a clip has more than one |
| `{preset}` | the preset suffix — `master`, `upload`, and blank for Remux |

**An empty field takes its separator with it**, which is what lets one string
cover every case without leaving gaps: an untitled single range is
`hdz_048_upload`, a named second range out of several is
`hdz_048_2_Tree-dive_upload`, and a Remux export, whose suffix is deliberately
blank, is just `hdz_048`.

A template names a file, never a folder — the Output box still decides where it
is written. Slashes, unknown fields, unpaired braces and characters the
operating system will not accept are reported as you type; the finished name is
checked again before anything is queued, which is what catches a reserved name
that only appears once the fields are filled in.

### What it will not do quietly

- **Overwrite.** If an export would replace a file that already exists, it says
  which ones and offers to skip them, overwrite, or stop.
- **Fill the disk.** Exports are estimated and checked against free space first.
  A full card at Edit quality runs to several hundred gigabytes.
- **Leave a broken file behind.** An encode is written to a temporary
  `.flightdvr-part` file and checked for video before it is moved into place.
  Cancelling removes the temporary file; an output that was already there is
  never touched.
- **Queue the same output twice**, or re-encode work that has already finished.

## Keyboard shortcuts

**`F1`, or `Help ▸ Keyboard shortcuts`.**

They are in the app rather than here because the list here was wrong. It filed
`Delete` under the clip list when `Delete` only works in the export queue, gave
seven of the nine keys that then worked anywhere — missing `Ctrl` `O` and
`Ctrl` `Shift` `S` — and never mentioned `U` `K` `M` `R` for reviewing at all.
Nothing compared it to the code, so nothing said so.

The dialog is compared to the code: a test walks the shortcuts the window
actually installs and fails if the two disagree in either direction. A second
copy of the list in this file would be free to rot again, which is how the
first one got like that.

The keys are grouped in it by what has to have focus, because several of them
depend on it — `K` is Keep in the clip list and Play on the picture, and
`Space` ticks a clip in one place and plays in the other.

---

## Which goggles it works with

HDZero goggles can record MPEG-TS (the recommended container) or MP4 to
`movies/` on the card. FlightDVR scans those recordings and also accepts `.mov`
and `.mkv` files. They do not all record at the same *size* — the Box Pro does
720p60, other modes do 720p90 and 1080p30, and the Goggle 2 goes to 1080p.

Nothing in the app assumes a resolution or frame rate. Downscale and frame rate
options are built from the clips you have selected, and size estimates scale
with the real pixel rate:

| Footage | DNxHR SQ |
|---|---:|
| 720p60 | 58 GB/hour |
| 720p90 | 88 GB/hour |
| 1080p30 | 66 GB/hour |
| 1080p60 | 132 GB/hour |

## Two things worth knowing about HDZero footage

### The colour is tagged oddly, and that matters

These recordings store **full-range** video — the luma spans 0–255, where normal
video occupies 16–235. Anything assuming the usual range, which is most players
and every editor, clips the blacks and the highlights. That is why footage run
through a plain ffmpeg command often looks crushed.

They are also tagged `bt470bg` primaries and `smpte170m` transfer, which is PAL
standard definition — not something a 720p digital camera has any business
claiming. It looks like a firmware default.

The obvious response is to convert everything to Rec.709 and tag it properly.
That turns out to make things worse. Four approaches were encoded losslessly,
rendered to RGB and compared against the source frame by frame:

| Approach | Difference from source | Worst pixel error |
|---|---:|---:|
| Leave the colour alone | none | 0 |
| **Fix the range only** (default) | **imperceptible** | **3** |
| Retag the matrix as bt709 | visible | 75 |
| Full Rec.709 conversion | worst | 108 |

A colour conversion is only as trustworthy as the tags it reads, and these tags
are not trustworthy. So the default corrects the range — the one thing that is
provably wrong — and leaves everything else alone.

All three modes are available if you disagree, and you can reproduce the
comparison on your own footage:

```bash
python tools/compare_colour.py "E:\movies\hdz_001.ts"
```

### The goggles cannot keep time

Every clip on a card tends to carry the same date, minutes apart, whatever day
it was actually filmed. That is not a fault in the card or this app. The goggle
firmware reports:

```
rtc_init has NOT detected a battery
```

There is a socket for a **CR2032** on the board but no cell fitted, so the clock
restarts from the same stored value on every power-up.

**This is fixable in hardware.** Fitting a CR2032 gives you real timestamps from
then on. The Goggle 1 ships without a cell; the Goggle 2 ships with one.

For footage already recorded, nothing can recover the true date, so the app:

- labels the column **Card date**, not "Recorded", and says why on hover
- shows a note above the list when the timestamps are obviously clustered, which
  stays hidden on goggles whose clock works
- asks for the flight date when copying originals to the library
- can start exported filenames with that date, set in the Output box

Sort by clip name for true recording order — the DVR's counter is reliable even
when its clock is not.

## Troubleshooting

**"Windows protected your PC" when you run the installer.** Expected. The
installer is not code-signed — a certificate costs several hundred pounds a
year, which is hard to justify for something given away. Click **More info**,
then **Run anyway**. If you would rather verify it first, the SHA-256 of each
release is published on its release page.

**Your antivirus flags it.** Occasionally happens to anything built with
PyInstaller, which looks structurally like a self-extracting archive. It is a
false positive; report it to your vendor if you like, and check the SHA-256
against the release page.

**"macOS cannot verify that this app is free from malware."** Expected, for the
same reason — signing needs a paid Apple Developer account. Right-click the app
and choose **Open** rather than double-clicking it, then confirm. If that does
not work, clear the quarantine flag:

```bash
xattr -dr com.apple.quarantine "/Applications/FlightDVR Studio.app"
```

**"Could not find ffmpeg" at startup.** On Linux and macOS the app uses your
system ffmpeg, so install it — see [Installing](#installing) for the command.
On Windows this only happens when running from source; the installed build
carries its own copy.

If you have installed it and the app still cannot see it, ask the app what it
found:

```bash
./FlightDVR_Studio-*.AppImage --check
```

```bash
"/Applications/FlightDVR Studio.app/Contents/MacOS/FlightDVRStudio" --check
```

That prints the version, the Qt platform in use and the exact ffmpeg paths it
resolved, then exits. It is the quickest thing to paste into a bug report.

**A clip will not preview.** Windows maps `.ts` to Media Player, which usually
cannot decode the video inside; macOS hands it to QuickTime, which cannot
either. Install VLC, mpv or IINA and it will be used automatically. On Linux
any of VLC, mpv, mplayer or totem is picked up, including Flatpak versions.
Alternatively the Remux preset rewraps a clip to `.mp4` in seconds without
re-encoding.

**DaVinci Resolve will not import the files.** The free version on Windows
cannot decode H.265. Use the **Edit** preset, which produces a mezzanine it can
read. Remux will not help — it keeps the original video.

**Exports look washed out or crushed.** Check the Colour setting is on *Fix
levels*. If you have built a grade around untouched footage, use *Leave colour
alone* instead.

**Every clip has the same date.** Expected — see
[above](#the-goggles-cannot-keep-time).

---

## Building from source

```bash
pip install -r requirements.txt
python -m flightdvr
```

Running from source needs ffmpeg and ffprobe on PATH, or unpacked into
`C:\ffmpeg\bin`.

```bash
python -m pytest -m "not integration"  # unit tests; no ffmpeg required
python -m pytest                       # full suite; integration needs ffmpeg
```

Each platform has its own packaging script. All three run the tests, regenerate
the icon, build the bundle and prove the result starts before packaging it.

| Platform | Command | Produces | Also needs |
|---|---|---|---|
| Windows | `pwsh packaging\build.ps1` | per-user installer | Inno Setup 6 (`winget install JRSoftware.InnoSetup`) |
| Linux | `packaging/build-appimage.sh` | AppImage | `curl`; appimagetool is fetched and cached |
| macOS | `packaging/build-macos.sh` | signed-ad-hoc `.app` in a `.dmg` | Xcode command line tools |

All three are produced by
[GitHub Actions](.github/workflows/build.yml) on relevant code and packaging
pushes and on version tags; documentation-only pushes are skipped. You do not
need any of those machines to release for them. The commands above are for
building one yourself.

There is more detail on the internals, the measured findings behind the colour
handling, and the traps in this footage in
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

Where this is going next — and what it deliberately will not do — is in
[docs/ROADMAP.md](docs/ROADMAP.md).

### How it is put together

| Area | Files | Contains |
|---|---|---|
| Media and export | `media.py`, `presets.py`, `jobs.py` | ffmpeg discovery, probing, export commands and the worker queue |
| Browsing and analysis | `scan.py`, `thumbs.py`, `trim.py`, `motion.py` | clip discovery, cached frames, trimming and flight readings |
| Playback and decisions | `player.py`, `stills.py`, `session.py` | bounded in-window playback, full-resolution stills and saved review decisions |
| Interface | `browser_panel.py`, `preview_panel.py`, `export_panel.py`, `queue_panel.py`, `ui.py` | panel-local behaviour and cross-panel workflows |

The module-by-module layout is maintained in
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#layout).

A few decisions that are not obvious from the code:

- **ffmpeg runs as a child process**, never linked. The app looks inside its own
  bundle before it looks at PATH, so a packaged copy always uses its own
  known-good pair rather than whatever is installed.
- **Scanning uses ffprobe's default probe size.** Forcing a large one costs
  about eight times as much reading from a card over USB and returns identical
  information on these files. The expensive settings remain as a fallback.
- **Thumbnails and filmstrips decode past the seek point.** Seeking into an
  MPEG-TS lands on an estimated byte offset rather than a keyframe, so frames
  taken straight after a seek are torn or grey.
- **Filmstrips decode keyframes only**, which is five times quicker than a full
  decode for finer granularity than anyone needs.
- **Two-pass encoding keeps audio in both passes.** The usual advice to pass
  `-an` on the first pass shifts the video framing by one frame here, and x264
  then rejects the stats file.
- **Exports run one at a time.** ffmpeg already uses every core.

---

## Licensing

FlightDVR Studio is **GPL v3** — see [LICENSE](LICENSE).

Matching the licence of the bundled ffmpeg is deliberate. It removes any
question about whether the app and the binary form a combined work, which is the
one genuinely murky part of shipping ffmpeg inside an installer.

Only the Windows installer bundles ffmpeg. The AppImage and the macOS app use
whatever your package manager installed, so they redistribute no ffmpeg binary
and carry no obligation for its source.

The bundled Windows build is `n7.1.5-12-g1fdbca85aa` from
[BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds), tag
`autobuild-2026-07-31-14-10`, configured `--enable-gpl --enable-version3`. It is
**not** an `--enable-nonfree` build, which could not be redistributed at all.
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) records the version, origin and
licence, along with a written offer for the corresponding source as required by
section 6 of the GPL.

Qt is used via PySide6 under the **LGPL v3**, dynamically linked and unmodified.

**H.264 and H.265 are patent-encumbered** in some jurisdictions. This software is
provided free of charge and makes no patent grant. If you redistribute it or use
it commercially, satisfy yourself about the position where you are.

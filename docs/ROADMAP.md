# Roadmap

Where FlightDVR Studio is going, and why. A direction rather than a promise:
dates are not given, order may change, and anything here can be dropped if it
turns out not to earn its place.

The aim is to cover everything between the goggles and either an editor, an
archive or a sharing platform — and nothing beyond that. This is not trying to
become a smaller Resolve.

## Where it is now

Through 1.5, the app can scan a card, tell you honestly what is on it, play a
clip in the window, trim it on the frame in front of you, and convert it
through five presets that each say what they are for. Exports are atomic and
verified, and the trim lands on the second it claims to.

It can also get you *through* a card, which 1.4 could not. A real one is
120-odd clips. Every decision is now written down as you make it: review states
with a filter and a count, several named ranges out of one recording, and a
session that remembers all of it plus the export settings. Outputs still
present for those settings are recognised as already exported, and clips are
matched by path, size and modification time rather than by a filename a card
will reuse. The State column says which clips have ranges saved and roughly how
many flights each one looks like it holds.

The next releases are about what happens after that: getting the footage out,
and getting it in.

---

## 1.5 — Ranges and sessions — shipped

*Getting through a card.*

**Sessions.** The work you do on a card becomes a document — clip identities,
trim ranges, review states, join order and export settings. The app also
recognises outputs still present for those settings. Autosaved so a quick
review needs no ceremony, with Save As when it deserves a name. It references
your footage rather than containing it, so it can be moved, backed up and
reopened.

Clips are identified by path, size and modification time together, never by
name alone. Cards get reused and rewritten with the same filenames, and a
session that confidently attached last week's trim points to this week's
footage would be worse than one that remembered nothing.

**Review states.** Unreviewed, Keep, Maybe, Reject — a key each, a filter, and
a count of how far through you are. *Maybe* matters more than it sounds: on a
long card, most of the decisions are "not now".

**Several ranges per clip.** A four-minute flight usually has two or three
moments worth keeping. Each gets its own range and an optional name, and can be
exported on its own or joined with the others.

**Precise trimming.** The filmstrip is one frame per second, which is right for
finding a moment and wrong for cutting on one. A small window of frames either
side of the playhead gets decoded on demand so you can step frame by frame,
without ever decoding a long recording in full.

**An activity reading and an offered range.** Measurement on real cards showed
that the DVR records from arming, so there is no dependable quiet minute before
take-off to find. The keyframes already extracted for the filmstrip can instead
separate moving and stopped spans, report roughly how many flights a recording
holds, and offer its longest useful run as a range when that would remove dead
time.

It offers, and never applies. A wrong guess that quietly trimmed your footage
would cost far more than no guess at all.

---

## 1.6 — Delivery

*Making something worth posting.*

- **Grab a still** from the preview, full resolution, for a thumbnail.
- **A vertical preset** that crops to 9:16 with a position you choose. The
  platforms crop a widescreen upload anyway, and not where you would have.
- **Slow motion**, using the frames a 60 fps recording already has.
- **Assembly** — an ordered list of clips and ranges, cuts only, exported as
  one file. DVR counter order stays the default, because the goggles' clock
  cannot be trusted.
- **Naming templates**, so exports come out named after the flight and the
  moment rather than after a counter.
- **Delivery bundles** — one range, several presets, one action.
- **A music bed**, with a fade, for anyone who would rather not share motor
  whine. The most involved item here and the most likely to slip.

---

## 1.7 — Health and salvage

*The recordings that went wrong.*

FPV recordings get cut short by flat batteries, crashes and cards pulled while
writing. Right now the app just fails on those, which is the least useful thing
it could do with them.

Each clip gets a health result — healthy, ends abruptly but decodable,
timestamp discontinuities, decoder warnings, unusable audio, partly
recoverable, unreadable — and where something can be done, the app offers to do
it: rewrap the decodable packets into a clean container, rebuild timestamps,
keep the video when the audio is beyond saving, salvage the longest good
stretch, and say plainly what it did.

The original file is never modified.

Alongside it, duplicate detection based on content rather than filename,
because DVR counters wrap around and the same name comes back.

---

## 1.8 — Ingest and library

*Card in, archive out.*

An explicit ingest: find the card, name the flight, confirm its date, copy,
verify every copy, record a manifest, and offer to eject. The date you give is
the one that is kept — the goggles' own timestamps are not reliable.

Then a library that is just folders and manifests, with no database:

```
FPV Library/
└─ 2026/
   └─ 2026-08-06 Hampstead Heath/
      ├─ originals/
      ├─ exports/
      └─ session.flightdvr.json
```

Your footage stays understandable without this program, ordinary backup tools
work on it, and an index that gets damaged can be rebuilt from what is on disk.

This is also the release for the remaining packaging work: VAAPI encoding on
Linux and Flatpak.

---

## Not planned

Some of these are asked for often enough to be worth answering directly.

**Colour grading, titles, transitions, multicam.** This is a tool for getting
footage out of goggles in good condition. Past that point you want an editor,
and there are good ones.

**Stabilisation.** HDZero DVR files carry no gyro data, so anything here would
be guesswork on pixels. Gyroflow does this properly, from the flight
controller's own logs.

**Reverse playback.** The preview decodes forward down a pipe. Playing
backwards would mean decoding forward and buffering, and pretending otherwise
would make it look like a feature rather than a compromise.

**Audio in the preview.** A second decode path, an output device and a second
clock, so that you can hear motor and wind noise while looking for a visual cut
point. Trim points are found by eye.

**Parallel exports.** ffmpeg already uses every core. Two at once makes both
slower and the progress display meaningless.

**An arbitrary-settings profile editor.** The presets each state what they are
for and why, which is what lets the app explain what it produced. A free-form
editor answers that question with "whatever you configured".

---

## Suggestions

Several things here came from people using it — the preview player and the
upload preset both did. If something is missing that would change how you use
this, open an issue.

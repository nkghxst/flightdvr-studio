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

2.0, below, brings the delivery and music work after that together. It is a
release candidate, not yet published.

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

## 2.0 — from card to finished video — planned, not yet released

Bring the existing card-review workflow together with Classic/Flow,
output-owned choices, Assembly, visual music editing, supported sound exports
and compatible bundles. The work once staged as 1.6 Delivery and 1.6.1 Music —
stills, Vertical, Slow motion, the Assembly, naming templates, delivery bundles
and the music bed — and the post-1.5 browser workspace are part of it, along
with a Linux AppImage that carries its own FFmpeg. Complete packaging and
integrated acceptance, reconcile the user guide, and qualify the candidate
through the three agreed journeys. Publication follows acceptance; a feature on
main is not itself a released download.

What is in it, and its known limits, are in the
[changelog](../CHANGELOG.md#200--unreleased-candidate) and the
[user guide](USER_GUIDE.md).

---

## After 2.0 — focused workspace improvements

Breakout or detached windows are a later workspace improvement, potentially a
2.1 topic. They are not a 2.0 prerequisite and have no promised delivery date.

---

## Later direction — health and salvage

Explore clearer health reporting and recovery options for interrupted or
damaged recordings, together with content-based duplicate detection. Keep the
original footage untouched. This is direction, not a claim that those tools
exist in 2.0.

---

## Later direction — ingest and library

Explore explicit copy-and-verify ingest, manifests and a library understandable
as ordinary folders. Earlier versions of this roadmap numbered this and health
and salvage as 1.8 and 1.7; the direction is kept, without a release number.
VAAPI and Flatpak remain future packaging topics, not added 2.0 commitments.

---

## Scope stays focused

FlightDVR prepares footage for an editor or delivery. It does not promise a
general editor with titles, transitions, multicam or grading, a new playback
engine, or selective inner-range Slow. No new architecture or feature package
is introduced by changing the release name.

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

**Sound in Slow motion.** Listening now auditions the selected output's sound,
but Slow motion exports silently and a smooth timed Slow audition is not
promised. Judge Slow motion in the completed file.

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

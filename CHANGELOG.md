# Changelog

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

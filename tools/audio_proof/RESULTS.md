# Synthetic proof evidence — 8 September 2026

Maker evidence for Sol's independent verdict; no review verdict is claimed.
Environment: CPython 3.12.10, Windows 11 AMD64 (10.0.26340). Numeric results,
dependency metadata/notices and wheel provenance are under `evidence/`. Original
captures: `D:/Dev/test-output/flightdvr-audio-proof-20260908/final/`.

| Check | Observed outcome |
| --- | --- |
| Focused unittest suite | 16 passed, 0.235 s |
| Restored no-DVR attenuation mutant | Test failed: 0.400000006 instead of 0.5 |
| Reset music phase/fade mutant | Test failed: (0, 1.0) instead of (2, 0.375) |
| Stereo output | 96,000 frames, 48 kHz; 2 seconds |
| Mix vs independent FFmpeg float reference | Maximum absolute sample error 2.98023224e-8; limit 1e-5 |
| 13 nonzero seeks vs reference suffix | Same maximum error; all lengths exact |
| Default monitor captures | All samples zero while mix remains nonzero |
| Queue / source cache / music cache high-water | 4 / 4 / 4 blocks |
| Generate two-second captured mix | 1.0694 s wall, 0.5781 s process CPU |
| 80 paced control updates | 1.733 ms p95, 13.627 ms maximum control-to-captured-block |
| Pull time / scheduler lateness | 1.818 ms p95 / 3.755 ms maximum |
| Re-prime / stop | 28.797 ms / 0.320 ms |
| Cancellation | Worker exited, queue empty |
| Obsolete blocks discarded in control experiment | 7 |
| Paced process CPU / wall | 0.15625 / 0.82274 s (19.0% of one core) |

The preceding run measured 83.9% process CPU/wall, p95 1.634 ms and maximum
12.361 ms. Short-run variability is material; the lower final CPU number does
**not** establish stable production headroom. Maximum control latency exceeds
one 10 ms block on both runs. No audible-performance verdict is established.

Before block-boundary span lookup, a two-second capture took 4.4038 s wall and
4.3594 s process CPU. Removing per-sample Fraction/timeline reconstruction made
later captures faster than real time. Longer loading/callback profiling remains
an integration gate, without blocking this bounded proof.

The first reference comparison failed at 2.44230032e-5. Error was concentrated in
fades; the steady section agreed within 2.98e-8. FFmpeg faded PCM16 before later
float conversion. Explicit `aformat=sample_fmts=fltp` before `afade` corrected the
reference to the normalized floating-signal contract; tolerance stayed 1e-5.

Fixture review IDs (first 16 of full-file SHA-256; full hashes in JSON):

- `synthetic_source.wav` (clip `494e6ebce19fa83d`), 96,000 stereo frames.
- `synthetic_music.wav` (clip `d8eba6f7cdafff412`), 12,000 stereo frames.
- Pinned ffmpeg executable SHA-256:
  `efabedca4b599c13e073bb08f66369619e9deb1db1dc1f32822583379d6513de`.

## Dependency feasibility

Only dedicated-venv install/import was exercised. No NumPy, QtMultimedia, device
enumeration request, stream open, speaker playback or microphone/loopback capture.

| Component | Installed version | Supplied license evidence |
| --- | --- | --- |
| sounddevice | 0.5.6 | MIT; LICENSE retained in inventory |
| CFFI | 2.1.1 | MIT-0; LICENSE retained in inventory |
| pycparser | 3.0 | BSD-3-Clause; LICENSE retained in inventory |
| Bundled PortAudio, non-ASIO DLL | V19.7.0-devel, revision unknown | Binary README identifies MIT and Ross Bencina/Phil Burk |

`evidence/install-report.json` records published wheel URLs and SHA-256 hashes.
`evidence/dependency_inventory.json` records project URLs, supplied license texts,
PortAudio notice and loaded DLL hash. The wheel also includes unused ASIO
variants; their presence is not a redistribution decision. The report does not
expose an exact PortAudio source revision or include its full license text.
Before packaging, resolve provenance and required upstream notices through a
scoped packaging decision. No app dependency or binary is shipped by this proof,
so shared requirements and third-party-notice files remain untouched.

Not checked: arbitrary compressed media, device recovery, physical sync, audible
latency, long-run underflows, acoustic level, native app UI, real footage or
cross-platform packaging. Source-only proof does not close #83.

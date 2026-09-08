# Isolated live-audio proof (#83)

Maker: Astra (GPT-6). Independent verdict owner: Sol, after section A of the
approved execution plan. Base: `8e40667a7ab563b382074d26b41086f7f1a50a74`.
Owned paths: this directory and `tests/test_audio_proof.py`.

This proof mixes normalized synthetic PCM in bounded blocks and captures files.
It opens **no audio stream**, imports no app code and changes no app settings.
`Levels` starts muted with a 25% monitor level. Explicit unmute in the control
experiment changes captured numbers only. Live audio is not integrated or ready
for product use.

## Reproduce

From this checkout, using Python 3.12 on Windows:

```powershell
$proofRoot = 'D:/Dev/test-output/flightdvr-audio-proof-20260908'
python -m venv "$proofRoot/venv"
& "$proofRoot/venv/Scripts/python.exe" -m pip install --only-binary=:all: --no-cache-dir --report "$proofRoot/install-report.json" -r tools/audio_proof/requirements-proof.txt
& "$proofRoot/venv/Scripts/python.exe" -m unittest discover -s tests -p test_audio_proof.py -v
& "$proofRoot/venv/Scripts/python.exe" -m tools.audio_proof.verify_mutants
& "$proofRoot/venv/Scripts/python.exe" -m tools.audio_proof.run --output-dir "$proofRoot/reproduction" --ffmpeg 'D:/Dev/tools/ffmpeg/ffmpeg-n7.1.5-12-g1fdbca85aa-win64-gpl-7.1/bin/ffmpeg.exe' --inventory
```

Use an unused output directory: the command replaces its named synthetic WAV,
F32 and JSON files. Do not use Python `-O`: proof assertions must run. Without
`--inventory`, the mixer and tests need only the standard library; comparison
also needs ffmpeg. Install only into a dedicated venv. Tests remove temporary
files; optionally set `AUDIO_PROOF_TEST_ROOT` to an existing isolated directory.

## Reusable contract and boundaries

- Time is integer 48 kHz stereo samples. Frozen `Span` records preserve occurrence
  identity, half-open source endpoints and rational speed. `Timeline` freezes
  cumulative output endpoints. Ten source seconds at half speed take 20 output
  seconds. Repeated occurrences remain distinct.
- Audio-bearing source spans are 1x. Injected 0.5x spans explicitly omit DVR
  audio, retaining output duration and music continuity. Slow-source audio is
  refused; pitch/time transformation needs separate proof.
- `AudioPlan` takes **effective, already-clamped** fade lengths. Absolute output
  time determines music loop phase and linear fades. Play-once silence begins at
  the audible end. Seek/re-prime preserves phase and envelope position.
- Input is already-normalized PCM16 WAV. `PcmStem` reads only required 10 ms
  blocks, caching at most four per stem. Compressed MP3/AAC/TS decoding and
  first-audio loading latency are not implemented.
- One producer queues at most four unweighted stem pairs. One further block may
  be in production and one held by the consumer. Queue payload is at most 30,720
  bytes; two PCM caches total at most 15,360 bytes, excluding Python objects and
  in-flight blocks. The CLI separately collects its fixed two-second test result;
  that comparison capture is not the bounded streaming interface.
- Where the timeline contains DVR audio, gains divide by `max(1, music + dvr)`
  and remain consistent through silent spans. With no DVR audio anywhere,
  requested music gain is retained. This provides peak headroom for normalized
  input, not loudness normalization or an acoustic level guarantee.
- `set_levels` affects the next pulled block, including already queued stems,
  without decoding again. Music/DVR changes ramp over 480 samples (10 ms).
  Monitoring and mute are separate from the mix. Mute gates returned monitor
  samples. This ramp is not export automation.
- `reprime` replaces the immutable plan, increments generation, clears queued
  blocks and rejects old in-flight work. `Buffering` means starvation or a
  generation changed during mixing, not planned silence or EOF. Decode errors
  raise; `stop` cancels and clears the queue.
- One caller owns `pull`. Returned generation/revision/start identify its data.
  A future hardware sink must fence generation at submission: a re-prime can
  occur after `pull` returns. This allocating Python consumer is not a real-time
  device callback. Pause, device loss and app/video clock ownership remain open.

## Independent checks

See [RESULTS.md](RESULTS.md) and [evidence/result.json](evidence/result.json).
The FFmpeg graph is handwritten for the fixture, not generated from `AudioPlan`.
The fixture contains 0.5 seconds normal source, 0.5 source seconds slowed/muted
to one output second, then 0.5 seconds normal source. Music loops every 0.25
seconds; output fade in/out are 0.1/0.2 seconds. Source tones are 440/660 Hz
(left/right), music 880/1100 Hz. Tests count observed crossings and assert RMS,
peak, format and frames. Constant-stem oracles separately check gains and mute.

FFmpeg normalizes to float before fading, matching this PCM contract. An initial
integer-fade reference exposed quantization, documented with measured values.
This export-equivalent synthetic check does not establish behavior of production
exporters. [DEVICE_PROCEDURE.md](DEVICE_PROCEDURE.md) lists the proposed next
acceptance session. Fake-sink timings establish no audible latency, physical A/V
sync, OS device behavior, compressed loading or stable CPU headroom.

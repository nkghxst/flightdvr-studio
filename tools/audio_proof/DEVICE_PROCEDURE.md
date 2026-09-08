# Proposed physical-device acceptance session — not executed

This needs a new coordinator floor and an Nk-aware scheduled session. The
current harness cannot open a stream. Build and review the smallest device
adapter only after authorization.

1. Name maker, independent verdict owner, exact adapter commit, one output
   device/host API, synthetic fixtures and a finite run length. Keep microphone,
   loopback and app closed unless the new floor includes them. Record backend,
   device rate/block size and reported output latency.
2. Confirm with Nk that sound is expected. Use a dedicated output with low
   hardware volume. Open paused/muted at the proposed 25% monitor level; verify
   no output before explicit Play and Unmute. Digital percentage is not acoustic
   loudness. Use the existing sub-full-scale tones and end initial bursts within
   one second automatically.
3. Test one explicit burst and Stop first. Provide an immediate mute/stop path
   independent of the worker and close output on failure. Verify stream/worker
   settle. Abort on unexpected loudness, wrong device, stuck stream or unintended
   output; do not automatically retry or unmute.
4. Exercise gain, mute, nonzero seeks across loop/fade/span boundaries, structural
   replacement, pause/resume, EOF and cancellation. Record command, submitted
   frame/generation, callback and DAC timestamps. Move allocating Python mixing
   off the real-time callback; the adapter should drain bounded ready buffers
   with a tested mute and generation gate.
5. Deliberately test device change/loss and underflow. Required behavior: pause,
   mute, discard stale generation, explicit recovery; no automatic switch or
   restart. Later app integration must separately verify new card/session starts
   paused/muted, selection never autoplays, monitoring differs from export
   inclusion and remembered volume stays machine-local.
6. Measuring action-to-audible latency or physical A/V sync requires separately
   authorized external measurement or loopback capture. Use timestamped clicks
   and a synchronized visual marker; measure at least 30 events plus drift over
   a bounded longer run. Agree numeric acceptance limits before verdict evidence.
   Callback timestamps alone are not physical sync or audible latency evidence.
7. Separately bound cold/warm compressed-stem loading with approved synthetic
   media: first valid block, seek, cancel, CPU/RSS, underruns and duration scaling.
   Preserve bounded memory. Include the oldest supported platform/backend before
   a packaging claim.
8. Close the device and verify silence/no workers. Hand off one report naming
   exact commit, device/fixture IDs, checks, failures and limits. The independent
   owner gives one verdict; only Nk merges. Import or one burst is insufficient
   for feature-ready or release claims.

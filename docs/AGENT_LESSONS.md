# Agent lessons and evidence traps

This file keeps the technical incidents that shaped FlightDVR's safeguards.
They are rationale, not current assignments. The development notes contain the
long-form design explanations; the [workflow](WORKFLOW.md) contains current
coordination and publication procedure.

## Settle the claim with the right evidence

Source reading, execution, measurement and native acceptance answer different
questions. A command can be well formed while the output is empty or wrong; a
green assertion count can be followed by a process failure; an offscreen widget
can have plausible geometry while every readable label is unusable. State the
kind of evidence and the limit beside the result.

Tests are strongest when the assertion comes from the reported failure and a
measured fixture, not from repeating the implementation in a test. Revert the
fix and watch the regression fail, or measure the output independently. A
behavioral oracle must exercise the failure mode rather than permit a false
branch or an unexplained tolerance.

Synthetic data is useful for awkward shapes, but its numbers should come from
the world where possible. One still-period detector passed fourteen invented
curves and failed on every real DVR clip because these recordings begin at
arming. The fixture was not wrong; the model of the recording was.

Fixtures must assert their own promised shape. An odd-dimension fixture once
asked an encoder for 127x95 and silently received 126x94, so all of its tests
passed while testing the wrong dimensions. Compare paths as `Path` objects;
Windows paths are not portable strings on POSIX.

For real media, identify the source by its filename and the first 16 lowercase
characters of its full-file SHA-256. Do not substitute `ClipInfo.fingerprint`:
it includes the local path and modification time for cache invalidation, so it
changes when the same clip is copied to another machine.

## Output and ffmpeg

Unit tests can check the command the app would issue. Only an integration test
or a measured output can settle whether ffmpeg produced the intended media.
The project once accumulated eighteen defects invisible in arguments, including
empty containers reported as success, a trim that lost frames, a blocked stderr
pipe and cancellation that left a partial result. A process exit of zero is not
enough: inspect the resulting container, extent, frame count and relevant audio
or colour properties.

Keep output preparation atomic. A failed or cancelled operation must not publish
whatever partial bytes happened to exist. Terminating ffmpeg can make
`communicate()` return normally, so cancellation needs an explicit gate before
publishing. Stage temporary files and lists, clean them on failure, and mutate
the queue only after preparation succeeds.

The supported ffmpeg range matters. The Ubuntu 22.04 packaging job exercises
ffmpeg 4.4, where `-fps_mode` does not exist; a current Windows build has
removed `-vsync`. Ask the code's `frame_rate_mode()` policy and wait for the
packaging jobs when arguments change. A current Ubuntu test is not evidence for
the oldest supported binary.

Joined output must derive bitrate, audio, frame rate, dimensions and colour
from each source rather than `clips[0]`. A historical measurement found 360
frames in and only 241 out while the export reported success. A separate seam
measurement found 359 frames where 360 were expected; the seam matched a
standalone export at 44.7 dB. Keep the limitation visible instead of claiming
exactness the measurement did not establish.

## Workers, cancellation and state

The bounded preview frame queue is back-pressure. Removing the bound makes a
slow repaint turn a decoder into an unbounded memory producer. A worker and the
window must not share mutable state: the queue race, stale scan and orphaned
ffmpeg all came from that boundary being blurred.

`request_stop` is the UI-thread request. `stop_process` performs terminate,
wait, kill and wait and belongs to the worker that owns the process. A UI call
that waits twice freezes the window. Retain retired workers until they stop;
destroying a running `QThread` can take the interpreter down after the last
assertion has passed. A successful test command therefore includes process exit
zero and confirmation that test-owned workers have stopped.

Keep hardware and updater probes hermetic in tests that only inspect layout or
state. Correctly scoped fixtures prevent unrelated ffmpeg/network workers from
surviving interpreter shutdown; diagnose the named leaking worker or fixture
before expanding production shutdown.

Cancelled extraction must publish nothing. A loader that is terminated while
`communicate()` returns normally can otherwise publish the frames it reached.
Build in a private directory and move into the cache only after successful,
uncancelled completion. The same staging rule applies to copies, concat lists
and export overwrites.

## Qt and native acceptance

Do not use `QtMultimedia`: its Windows backend cannot decode HEVC in MPEG-TS,
the only format this app exists for. It can pass synthetic tests and fail on
every real recording.

Offscreen Qt is useful for geometry and state transitions, not for native
readability, keyboard focus, pointer behavior or live Studio acceptance. A
screenshot claim must name the capture method, Qt platform and actual window
dimensions. `QWidget.grab()` is a widget pixmap, not a desktop photograph; the
repository screenshot helper requires a normal desktop because offscreen fonts
render as empty boxes. Deferred layout measurements must happen after Qt has
laid out the changed widget, and any visual change needs both themes checked.

For interaction claims, use the native gate that can actually settle the claim.
Model/mime tests do not prove a physical drag; an offscreen shortcut test does
not prove a real keyboard mapping. Say explicitly when the native step remains
pending rather than converting a synthetic pass into approval.

## Durable reporting habits

Use exact Git objects, paths and observed results. Keep issue references neutral
until acceptance is complete, and inspect GitHub's closing references rather
than trusting prose. Keep a correction review to the named failure and delta.
Keep an interrupted run as an explicit checkpoint with a next action; a clean
source tree or an authenticated reconnect is not evidence that an unfinished
task completed.

The examples above are intentionally short: they preserve why a safeguard
exists without turning a historical incident into a permission to broaden the
next task.

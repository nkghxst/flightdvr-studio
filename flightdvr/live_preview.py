# FlightDVR Studio - browse, trim and convert HDZero goggle DVR footage.
# Copyright (C) 2026 Isadu Nkemi
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# this program. If not, see <https://www.gnu.org/licenses/>.

"""Monitoring for one output, driven by the video clock.

Separate from the window because the decisions here are worth testing without
one: which output is being listened to, what happens when it changes, and what
is said when the sound cannot be trusted.

There is one player and one clock. This does not advance anything; it is told
where the picture is and asks the stream for that. Nothing here is written to a
session or reaches an export — monitoring lives for as long as the window is
open and no longer.

The honesty rule, in one place: `AudioOutput.observe()` reports what the
backend says it has *processed*, which is not sound anybody has heard. This
schedules against it, bounds how far apart the two may drift, and when it
cannot tell — or the gap is too wide, or the speed is not 1x — it **stops
monitoring and says why** rather than playing something it cannot vouch for.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum

from .audio_device import AudioOutput, DeviceUnavailable
from .audio_plan import OUTPUT_RATE
from .audio_stream import Buffering, StreamFailed

# How far the backend's processed count may sit from where the picture is
# before monitoring stops. A fifth of a second is past the point where a person
# stops hearing "the music" and starts hearing "the music is late".
DRIFT_LIMIT_SAMPLES = OUTPUT_RATE // 5

# Consecutive starved ticks tolerated before giving up. One is ordinary — a
# block arrived late. A run of them means we are not keeping up, and carrying
# on would be a stutter nobody asked to listen to.
STARVED_LIMIT = 8


class Listening(str, Enum):
    """What the person asked to hear."""

    SOURCE = "source"
    MIX = "mix"


@dataclass(frozen=True)
class Status:
    """What the transport is doing, in words the panel can show."""

    playing: bool = False
    muted: bool = True
    offered: bool = False
    reason: str = ""

    @property
    def available(self) -> bool:
        return self.offered and not self.reason


class LivePreview:
    """One output's monitoring, and the reasons it is not running."""

    def __init__(self, *, stream_factory=None, output=None) -> None:
        # Both injected. The window supplies the real ones; the tests supply
        # stand-ins, so none of this needs a device or a decoder to be settled.
        self._make_stream = stream_factory
        self._output: AudioOutput | None = output
        self._stream = None
        self._target = None
        self._generation = 0
        self._listening = Listening.MIX
        self._muted = True
        self._level = 0.25
        self._playing = False
        self._speed = 1.0
        self._reason = ""
        self._target_reason = ""
        self._reap_timeout = 5.0
        self._reapers: list[threading.Thread] = []
        self._target_reason = ""
        self._starved = 0

    # -- what is on offer ------------------------------------------------------

    @property
    def status(self) -> Status:
        return Status(playing=self._playing, muted=self._muted,
                      offered=self._stream is not None, reason=self._reason)

    @property
    def listening(self) -> Listening:
        return self._listening

    @property
    def level(self) -> float:
        return self._level

    @property
    def generation(self) -> int:
        return self._generation

    # -- which output ----------------------------------------------------------

    def set_target(self, target, *, reason: str = "") -> None:
        """Listen to a different output, or to none.

        Silence first, then rebuild. The old stream's sound is fenced before
        anything new is built, so a single-target slice cannot emit sound
        belonging to the output that was just left — which is the one mistake
        here that a person would hear rather than read.

        `reason` names why nothing can be offered: an unsupported context, or
        music whose track is still being read. It is shown rather than
        swallowed, and it leaves the transport paused and muted.
        """
        self._silence()
        self._target = target
        self._target_reason = reason
        self._reason = reason
        self._stream = None
        if target is None or reason:
            return
        if self._make_stream is None:
            self._reason = "monitoring is not available"
            return
        try:
            self._stream = self._make_stream(target)
        except Exception as exc:
            self._stream = None
            self._reason = f"this output cannot be monitored: {exc}"
            return
        if self._stream is None:
            # Not a refusal. An output nobody gave music to has nothing to
            # listen to, which `offered` already says; inventing a reason for
            # it would put an explanation where the ordinary standing note
            # belongs, and train people to stop reading both.
            return
        # Anchor the device to the new stream's generation. Without this the
        # output still holds the one it was given for the previous target, so
        # it would refuse the sound that is now correct and accept the sound
        # that is now stale — the wrong way round in both directions.
        self._generation = int(getattr(self._stream, "generation", 0))
        if self._output is not None:
            self._output.reset(self._generation)

    def set_speed(self, speed: float) -> None:
        """Monitoring is for 1x only, and says so at any other speed.

        Nothing merged stretches audio in time, so the choice is between
        playing it at the wrong pitch and not playing it. Saying so is the
        only honest third option.
        """
        self._speed = float(speed)
        if self._speed != 1.0:
            self._silence()
            self._reason = "monitoring is off while the speed is not 1x"
        elif self._reason.startswith("monitoring is off while the speed"):
            self._reason = ""

    # -- the controls ----------------------------------------------------------

    def play(self) -> None:
        """Start both sides, then let them run.

        `AudioStream.resume()` refuses before `start()`, and `AudioOutput` has
        no sink until it is started. Neither begins by itself, because both
        begin paused and silent on purpose — so the first play is where they
        are opened, and where a machine with no audio output says so.
        """
        if not self.status.available:
            return
        try:
            self._stream.start()
        except RuntimeError as exc:
            self._stop_with(f"this output cannot be monitored: {exc}")
            return
        if self._output is not None:
            try:
                self._output.start()
            except DeviceUnavailable as exc:
                self._stop_with(f"there is nothing to listen on: {exc}")
                return
        self._playing = True
        self._starved = 0
        self._stream.resume()
        if self._output is not None:
            self._output.resume()

    def pause(self) -> None:
        self._playing = False
        if self._stream is not None:
            self._stream.pause()
        if self._output is not None:
            self._output.pause()

    def restart(self) -> None:
        """Start from the beginning of this output.

        The commonest thing to want after changing a track, and tedious to
        reach by scrubbing. Returns the transport to the start and leaves it
        exactly as paused or playing as it was.
        """
        if self._stream is None:
            return
        self._generation = self._stream.restart()
        if self._output is not None:
            self._output.reset(self._generation)
        self._starved = 0

    def seek(self, output_sample: int) -> None:
        """Follow the picture somewhere else in the same output."""
        if self._stream is None:
            return
        self._generation = self._stream.reprime(int(output_sample))
        if self._output is not None:
            self._output.reset(self._generation)
        self._starved = 0

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        self._apply_monitor()

    def set_level(self, level: float) -> None:
        self._level = min(1.0, max(0.0, float(level)))
        self._apply_monitor()

    def set_listening(self, listening: Listening) -> None:
        """Source alone, or the finished mix.

        A different thing to listen to is a different plan, so the stream is
        rebuilt rather than the choice merely remembered — recording it and
        carrying on would leave the control looking as though it did something.
        """
        chosen = Listening(listening)
        if chosen is self._listening:
            return
        self._listening = chosen
        if self._target is not None:
            was_playing = self._playing
            self.set_target(self._target, reason=self._target_reason)
            if was_playing:
                self.play()

    def _apply_monitor(self) -> None:
        if self._stream is not None:
            # The level is rendered into the block by the stream. The device's
            # own volume is left alone, or it would be applied twice.
            self._stream.set_monitor(level=self._level, muted=self._muted)

    # -- being driven ----------------------------------------------------------

    def tick(self, output_sample: int) -> int:
        """The picture is at this sample. Feed what belongs there.

        Returns the bytes handed to the device, which is a number about
        *submission* and about nothing else.
        """
        if not self._playing or not self.status.available:
            return 0
        if self._output is None:
            return 0
        sent = self._drain()
        report = self._output.observe()
        if report.error or self._output.failure:
            self._stop_with(
                f"the audio device stopped: "
                f"{report.error or self._output.failure}")
            return sent
        if not report.usable:
            self._stop_with("the audio device stopped reporting its state")
            return sent
        self._judge_drift(output_sample)
        self._judge_starvation()
        return sent

    def _drain(self) -> int:
        sent = 0
        while True:
            try:
                block = self._stream.pull()
            except Buffering:
                # Ordinary: nothing ready this tick, including while paused.
                break
            except StreamFailed as exc:
                # Not ordinary, and it used to be swallowed here — the
                # transport went on saying it was playing while the producer
                # had given up behind it.
                self._stop_with(f"the sound could not be produced: {exc}")
                break
            except Exception as exc:
                self._stop_with(f"the sound could not be produced: {exc}")
                break
            if block is None:
                break
            taken = self._output.present(block)
            if taken == 0:
                break
            sent += taken
            self._output.pump()
        return sent

    def _judge_drift(self, output_sample: int) -> None:
        """Bounded, and an estimate. Never a claim about what was heard."""
        processed = self._output.processed_bytes()
        if processed is None:
            self._stop_with("the audio device stopped reporting its progress")
            return
        from .audio_device import FRAME_BYTES
        heard_ish = processed // FRAME_BYTES
        if abs(int(output_sample) - heard_ish) > DRIFT_LIMIT_SAMPLES:
            self._stop_with(
                "monitoring stopped: the sound had drifted too far from the "
                "picture to be worth hearing")

    def _judge_starvation(self) -> None:
        cause = self._output.starvation()
        if cause == "backend":
            self._stop_with("the audio device reported a problem of its own")
        elif cause == "starved":
            self._starved += 1
            if self._starved >= STARVED_LIMIT:
                self._stop_with(
                    "monitoring stopped: the sound could not be kept up with")
        else:
            # "ended" is the material finishing, which is not a fault, and an
            # empty cause is an ordinary tick.
            self._starved = 0

    # -- going quiet -----------------------------------------------------------

    def _stop_with(self, reason: str) -> None:
        self._silence()
        self._reason = reason

    def _silence(self) -> None:
        """Paused, muted, and nothing of the old output left to play."""
        self._playing = False
        self._starved = 0
        self._reason = ""
        if self._stream is not None:
            self._stream.pause()
            self._reap(self._stream)
        if self._output is not None:
            self._output.reset(self._output.generation)
            self._output.pause()

    def _reap(self, stream) -> None:
        """Ask a stream to stop, and wait for it somewhere else.

        `wait_stopped` joins a plain `threading.Thread`, and its own docstring
        says callers must keep that off the UI thread. Asking here and waiting
        on a daemon of our own means a window closes at once while the
        producer finishes settling its readers in its own time.
        """
        stream.request_stop()
        waiter = threading.Thread(
            target=stream.wait_stopped, args=(self._reap_timeout,),
            name="flightdvr-live-preview-reaper", daemon=True)
        waiter.start()
        self._reapers.append(waiter)
        self._reapers = [t for t in self._reapers if t.is_alive()]

    def close(self) -> None:
        """Let go of everything, without waiting on this thread.

        The stream's worker is an ordinary thread and reaps its own readers;
        the sink is released by the adapter, which drops its buffer first so a
        backend that would otherwise drain cannot hold the window open.
        """
        stream, self._stream = self._stream, None
        self._playing = False
        if stream is not None:
            self._reap(stream)
        if self._output is not None:
            self._output.stop()

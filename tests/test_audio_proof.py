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

"""The proof must not reset music phase or lose a repeated source occurrence."""
from fractions import Fraction
from array import array
from pathlib import Path
import math
import os
import sys
import tempfile
import threading
import time
import unittest
import wave

from tools.audio_proof.model import AudioPlan, Span, Timeline
from tools.audio_proof.stream import BLOCK, RATE, Buffering, Cancelled, Levels, PcmStem, ProofStream
from tools.audio_proof.run import fixture


class TimelineTests(unittest.TestCase):
    def test_ten_seconds_at_half_speed_adds_ten_seconds(self):
        timeline = Timeline((Span("first", 0, 10),
                             Span("slow", 10, 20, Fraction(1, 2), False),
                             Span("last", 20, 30)))
        self.assertEqual(timeline.duration, 40)
        span, source = timeline.locate(25)
        self.assertEqual((span.occurrence, source), ("slow", Fraction(35, 2)))
        self.assertEqual(timeline.locate(30)[0].occurrence, "last")

    def test_repeated_source_position_retains_assembly_occurrence(self):
        timeline = Timeline((Span("one", 3, 7), Span("two", 3, 7)))
        self.assertEqual(timeline.locate(1)[1], timeline.locate(5)[1])
        self.assertNotEqual(timeline.locate(1)[0], timeline.locate(5)[0])

    def test_nonzero_seek_keeps_absolute_music_loop_and_fade_phase(self):
        plan = AudioPlan(Timeline((Span("one", 0, 40),)), 7, fade_out=8)
        self.assertEqual(plan.music_at(37), (2, 3 / 8))
        once = AudioPlan(plan.timeline, 7, loop=False, fade_out=2)
        self.assertEqual(once.music_at(8), (None, 0.0))
        self.assertEqual(once.music_at(6), (6, 0.5))

    def test_slow_audio_is_refused_instead_of_playing_at_the_wrong_rate(self):
        with self.assertRaises(ValueError):
            Span("slow", 0, 10, Fraction(1, 2), True)

    def test_fractional_sample_coordinates_are_rejected_before_decode(self):
        with self.assertRaises(ValueError):
            Span("bad", .5, 10)
        self.assertEqual(Span("slow", 0, 10, .5, False).duration, 20)
        with self.assertRaises(ValueError):
            AudioPlan(Timeline((Span("a", 0, 10),)), 3.5)


class ConstantStem:
    frames = 10000

    def __init__(self, value):
        self.value = value

    def at(self, position):
        return self.value, self.value


class StreamTests(unittest.TestCase):
    """Numeric oracles are stated constants, independent of the mixer formula."""

    def stream(self, *, source=None, audio=True, start=0):
        plan = AudioPlan(Timeline((Span("one", 0, 9000, source_audio=audio),)), 10000)
        stream = ProofStream(plan, source or ConstantStem(.25), ConstantStem(.5), start=start)
        self.addCleanup(stream.stop)
        stream.start()
        return stream, plan

    def test_default_mute_gates_monitor_but_retains_known_mix(self):
        stream, _ = self.stream()
        block = stream.pull()
        self.assertFalse(any(block.monitored))
        for value in block.mix:
            self.assertAlmostEqual(value, .45, places=6)  # .25 * .2 + .5 * .8

    def test_all_silent_dvr_keeps_music_at_requested_gain(self):
        stream, _ = self.stream(audio=False)
        self.assertEqual(set(stream.pull().mix), {.5})

    def test_queued_stems_use_new_levels_without_restarting_decode(self):
        stream, _ = self.stream()
        deadline = time.monotonic() + 2
        while stream.blocks.qsize() < 4 and time.monotonic() < deadline:
            time.sleep(.001)
        self.assertEqual(stream.blocks.qsize(), 4)
        revision = stream.set_levels(music=.5, dvr=0, muted=False, monitor=.25)
        first = stream.pull()
        self.assertEqual((first.revision, first.generation, first.start), (revision, 1, 0))
        self.assertAlmostEqual(first.mix[-1], .25)
        settled = stream.pull()
        self.assertEqual(set(settled.mix), {.25})
        self.assertEqual(set(settled.monitored), {.0625})
        self.assertEqual(settled.start, BLOCK)
        stream.set_levels(muted=True)
        self.assertFalse(any(stream.pull().monitored))

    def test_reprime_discards_inflight_old_generation_and_buffering_is_explicit(self):
        entered, release = threading.Event(), threading.Event()

        class GatedStem(ConstantStem):
            def at(self, position):
                if not entered.is_set():
                    entered.set()
                    if not release.wait(2):
                        raise RuntimeError("test gate timeout")
                return position / 10000, position / 10000

        stream, plan = self.stream(source=GatedStem(0))
        # Cleanup releases the gate BEFORE the registered stream stop.
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(1))
        with self.assertRaises(Buffering):
            stream.pull(timeout=.01)
        generation = stream.reprime(plan, 1234)
        release.set()
        block = stream.pull()
        self.assertEqual((block.generation, block.start), (generation, 1234))
        self.assertAlmostEqual(block.mix[0], .42468, places=6)
        self.assertGreaterEqual(stream.discarded, 1)

    def test_full_queue_cancellation_settles_without_a_consumer(self):
        stream, _ = self.stream()
        deadline = time.monotonic() + 2
        while stream.blocks.qsize() < 4 and time.monotonic() < deadline:
            time.sleep(.001)
        self.assertEqual(stream.blocks.qsize(), 4)
        stream.stop()
        self.assertFalse(stream.thread.is_alive())
        self.assertTrue(stream.blocks.empty())
        with self.assertRaises(Cancelled):
            stream.pull()

    def test_structural_reprime_replaces_duration_fades_loop_and_gain_policy(self):
        stream, _ = self.stream()
        stream.pull()
        changed = AudioPlan(Timeline((Span("slow", 0, 100, .5, False),)),
                            10000, loop=False, fade_out=100)
        generation = stream.reprime(changed, 150)
        block = stream.pull()
        self.assertEqual((block.generation, block.start, len(block.mix)), (generation, 150, 100))
        self.assertAlmostEqual(block.mix[0], .25)   # half of constant .5 music
        self.assertAlmostEqual(block.mix[-1], .005)
        self.assertIsNone(stream.pull())

    def test_decode_error_is_not_reported_as_silence_or_success(self):
        class BrokenStem(ConstantStem):
            def at(self, position):
                raise ValueError("injected corrupt source")
        stream, _ = self.stream(source=BrokenStem(0))
        with self.assertRaisesRegex(ValueError, "injected corrupt source"):
            stream.pull()

    def test_nonfinite_and_excessive_levels_are_rejected(self):
        for bad in (float("nan"), float("inf"), -1, 1.01):
            with self.assertRaises(ValueError):
                Levels(music=bad)


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("AUDIO_PROOF_TEST_ROOT"))
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic.wav"

    def test_generated_fixture_has_asserted_format_frequency_peak_and_cache_bound(self):
        fixture(self.path, RATE, (440, 660))
        with wave.open(str(self.path), "rb") as source:
            self.assertEqual((source.getframerate(), source.getnchannels(),
                              source.getsampwidth(), source.getnframes()), (RATE, 2, 2, RATE))
            samples = array("h")
            samples.frombytes(source.readframes(RATE))
        if sys.byteorder != "little":
            samples.byteswap()
        for channel, frequency in enumerate((440, 660)):
            values = samples[channel::2]
            # Count observed rising crossings, independent of the sine generator.
            crossings = sum(a <= 0 < b for a, b in zip(values, values[1:]))
            self.assertEqual(crossings, frequency)
            self.assertEqual(max(values), 12000)
            rms = math.sqrt(sum(v * v for v in values) / RATE)
            self.assertAlmostEqual(rms, 12000 / math.sqrt(2), delta=1)
        stem = PcmStem(self.path)
        self.addCleanup(stem.close)
        for pos in range(0, RATE, BLOCK):
            stem.at(pos)
        self.assertEqual(stem.high_water, 4)
        self.assertEqual(len(stem.cache), 4)

    def test_truncated_pcm_fails_instead_of_publishing_a_partial_block(self):
        fixture(self.path, 1000, (440, 660))
        with self.path.open("r+b") as target:
            target.truncate(self.path.stat().st_size - 16)
        stem = PcmStem(self.path)
        self.addCleanup(stem.close)
        with self.assertRaisesRegex(ValueError, "truncated PCM"):
            stem.at(999)

    def test_wrong_rate_and_empty_stems_are_refused(self):
        for rate, frames in ((44100, 10), (RATE, 0)):
            with wave.open(str(self.path), "wb") as target:
                target.setparams((2, 2, rate, frames, "NONE", "not compressed"))
                target.writeframes(b"\x00" * frames * 4)
            with self.assertRaises(ValueError):
                PcmStem(self.path)


if __name__ == "__main__":
    unittest.main()

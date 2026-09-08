# FlightDVR Studio - browse, trim and convert HDZero goggle DVR footage.
# Copyright (C) 2026 Isadu Nkemi
# SPDX-License-Identifier: GPL-3.0-or-later
# This program is free software under GNU GPL v3 or later, without warranty.
# See LICENSE and <https://www.gnu.org/licenses/>.
"""The proof must not reset music phase or lose a repeated source occurrence."""
from fractions import Fraction
import unittest

from tools.audio_proof.model import AudioPlan, Span, Timeline


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


if __name__ == "__main__":
    unittest.main()

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

"""One editor, and what its gestures do to a real stream's samples."""

from __future__ import annotations

import os
import time
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from flightdvr.audio_plan import (
    OUTPUT_RATE, AudioAsset, AudioMode, MusicChoice, SampleSpan,
    resolve_monitor_audio_plan,
)
from flightdvr.audio_stream import (
    BLOCK_FRAMES, AudioStream, Buffering, LiveAudioMapping, MonitorState,
)
from flightdvr.music_edit import EditKind
from flightdvr.music_timeline import LiveMusicBinding, MusicEditor

OUTPUT = 40 * BLOCK_FRAMES


@pytest.fixture(scope="module")
def app():
    yield QApplication.instance() or QApplication([])


def a_choice(level=Fraction(1), fade_in=0, fade_out=0, *, sha="a" * 64):
    asset = AudioAsset(Path("song.wav"), sha, 0, 48_000, 2, 480_000)
    return MusicChoice(mode=AudioMode.REPLACE, asset=asset,
                       passage=SampleSpan(48_000, 96_000, 48_000),
                       music_level=level, fade_in_samples=fade_in,
                       fade_out_samples=fade_out)


def plan_of(choice):
    return resolve_monitor_audio_plan(choice, OUTPUT, source_has_audio=False,
                                      preset_key="master")


class ConstantReader:
    """Every music sample is 1.0, so a block's samples are its gain."""

    frames = 480_000

    def read(self, start, frames, cancelled):
        return [1.0] * (frames * 2)

    def request_stop(self):
        pass

    def close(self):
        pass


class Monitor:
    """One stream per output, routed the way the transport routes updates."""

    def __init__(self, streams):
        self.streams = streams
        self.updates = []

    def update_parameters(self, target, plan):
        self.updates.append((target, plan.music_gain))
        return self.streams[target].update_parameters(plan)


def a_stream(choice):
    stream = AudioStream(
        LiveAudioMapping(plan_of(choice), SampleSpan(0, OUTPUT, OUTPUT_RATE)),
        music_reader=ConstantReader(), monitor=MonitorState(level=1))
    stream.start()
    stream.resume()
    return stream


def next_gain(stream):
    """The gain of a block rendered after now. The queued blocks and the one
    in flight were made before, so pull past more than all of them."""
    deadline = time.monotonic() + 2
    seen = []
    while time.monotonic() < deadline:
        try:
            block = stream.pull()
        except Buffering:
            time.sleep(0.002)
            continue
        seen.append(round(tuple(block.planned)[0], 6))
        if len(seen) > 6:
            return seen[-1]
    raise AssertionError("the stream produced nothing")


def stop(*streams):
    for stream in streams:
        stream.request_stop()
        stream.wait_stopped(2)


def editing(choice, pin):
    editor = MusicEditor()
    editor.pin_source = lambda: pin[0]
    editor.load(choice, output_samples=OUTPUT)
    return editor


# -- the editor: quiet loads, one edit per gesture -------------------------------

def test_loading_is_not_choosing(app):
    heard = []
    editor = MusicEditor()
    editor.committed.connect(lambda *a: heard.append(a))
    editor.drafted.connect(lambda *a: heard.append(a))
    editor.load(a_choice(), output_samples=OUTPUT)
    editor.set_envelope(None, "Waveform not available yet")
    assert heard == []


def test_a_drag_is_many_drafts_and_one_edit(app):
    drafts, edits = [], []
    editor = MusicEditor()
    editor.drafted.connect(lambda c, k: drafts.append(k))
    editor.committed.connect(lambda c, k: edits.append((c, k)))
    editor.load(a_choice(), output_samples=OUTPUT)
    assert editor.begin_gesture()
    for fade in (800, 1600, 2400):
        editor.draft(replace(editor.choice, fade_in_samples=fade))
    editor.end_gesture()
    assert drafts == [EditKind.PARAMETER] * 3
    assert len(edits) == 1 and edits[0][0].fade_in_samples == 2400
    assert edits[0][1] is EditKind.PARAMETER
    assert editor.stored.fade_in_samples == 2400


def test_a_drag_that_ends_where_it_began_is_no_edit(app):
    edits = []
    editor = MusicEditor()
    editor.committed.connect(lambda *a: edits.append(a))
    editor.load(a_choice(), output_samples=OUTPUT)
    editor.begin_gesture()
    editor.draft(replace(editor.choice, fade_in_samples=800))
    editor.draft(replace(editor.choice, fade_in_samples=0))
    editor.end_gesture()
    assert edits == []


def test_escape_puts_the_stored_value_back_and_edits_nothing(app):
    edits, cancels = [], []
    editor = MusicEditor()
    editor.committed.connect(lambda *a: edits.append(a))
    editor.cancelled.connect(cancels.append)
    stored = a_choice()
    editor.load(stored, output_samples=OUTPUT)
    editor.begin_gesture()
    editor.draft(replace(stored, music_level=Fraction(1, 4)))
    editor.cancel_gesture()
    assert edits == [] and cancels == [stored]
    assert editor.choice == stored


def test_a_read_only_editor_takes_no_gesture(app):
    editor = MusicEditor()
    editor.load(a_choice(), output_samples=OUTPUT, read_only=True)
    assert not editor.begin_gesture()
    edits = []
    editor.committed.connect(lambda *a: edits.append(a))
    editor.commit(replace(editor.choice, music_level=Fraction(1, 2)))
    assert edits == []


# -- what is heard: a real stream's samples ----------------------------------------

def test_escape_after_a_level_drag_puts_back_what_is_heard(app):
    """Correction 1: an auditioned gain must not keep playing after Escape."""
    stored = a_choice(Fraction(1))
    stream = a_stream(stored)
    pin = ["A"]
    editor = editing(stored, pin)
    binding = LiveMusicBinding(Monitor({"A": stream}),
                               lambda target, choice: plan_of(choice))
    editor.drafted.connect(lambda c, k: binding.drafted("A", c, k))
    editor.cancelled.connect(lambda c: binding.cancelled("A", c))
    try:
        assert next_gain(stream) == 1.0
        editor.begin_gesture()
        editor.draft(replace(stored, music_level=Fraction(1, 4)))
        assert next_gain(stream) == 0.25, "the drag was not heard"
        editor.cancel_gesture()
        assert next_gain(stream) == 1.0, "Escape left the auditioned gain playing"
    finally:
        stop(stream)


def test_a_rollback_never_lands_on_the_output_switched_to(app):
    """Drag A's level, switch to B mid-drag, press Escape: B keeps its own
    gain, and nothing of the gesture on A is applied to it."""
    a_stored = a_choice(Fraction(1))
    b_stored = a_choice(Fraction(1, 2), sha="b" * 64)
    streams = {"A": a_stream(a_stored), "B": a_stream(b_stored)}
    monitor = Monitor(streams)
    pin = ["A"]
    current = ["A"]
    editor = editing(a_stored, pin)
    binding = LiveMusicBinding(monitor, lambda target, choice: plan_of(choice))
    editor.drafted.connect(lambda c, k: binding.drafted(current[0], c, k))
    editor.cancelled.connect(lambda c: binding.cancelled(current[0], c))
    try:
        editor.begin_gesture()
        editor.draft(replace(a_stored, music_level=Fraction(1, 4)))
        current[0] = pin[0] = "B"                    # the output changes
        before = list(monitor.updates)
        editor.cancel_gesture()
        editor.draft(replace(a_stored, music_level=Fraction(1, 8)))
        editor.end_gesture()
        assert monitor.updates == before, "a stale gesture reached the monitor"
        assert next_gain(streams["B"]) == 0.5, "B was given the values of A"
        assert not editor.gesture_active
    finally:
        stop(*streams.values())


def test_a_committed_parameter_asks_for_no_rebuild(app):
    stream = a_stream(a_choice())
    binding = LiveMusicBinding(Monitor({"A": stream}),
                               lambda target, choice: plan_of(choice))
    try:
        assert binding.committed("A", a_choice(Fraction(1, 2)), EditKind.PARAMETER)
        assert next_gain(stream) == 0.5
        assert not binding.committed("A", a_choice(), EditKind.STRUCTURAL)
    finally:
        stop(stream)

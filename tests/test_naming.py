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

"""Naming templates, against the names the app already produces.

The one requirement that matters more than every feature in the template system
is that turning it on changes nothing. Somebody with a library of exports and a
session full of decisions must not find the next export named differently from
the last one because a feature they never asked for now exists.

So the test that counts is not "does the template expand" — it is `select_stem`
composed with `output_path`, the pair that produces today's filenames, compared
against the default template for the same clip. Byte for byte, on every shape of
clip the app can export.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flightdvr.format import (  # noqa: E402
    DEFAULT_TEMPLATE, TEMPLATE_FIELDS, UnknownTemplateField, check_template,
    expand_template, export_fields, select_stem,
)
from flightdvr.media import ClipInfo, Select  # noqa: E402
from flightdvr.presets import PRESETS, output_path  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


def clip(name="hdz_048.ts") -> ClipInfo:
    return ClipInfo(
        path=Path(name), size=599_189_652,
        modified=datetime(2025, 10, 8, 18, 39),
        duration=212.0, width=1280, height=720, fps=60.0,
        video_codec="hevc", audio_codec="aac",
        pix_fmt="yuvj420p", color_range="pc",
    )


def pieces_of(source: ClipInfo, ranges: list[Select]) -> list[ClipInfo]:
    """What `for_export` hands the queue: one clip per range, each holding its
    own single Select."""
    return [replace(source, selects=[one], current=0) for one in ranges]


def todays_stem(piece: ClipInfo, index: int, total: int) -> str:
    return select_stem(piece, index, total)


def template_stem(piece: ClipInfo, index: int, total: int, preset_key: str,
                  flight_date=None, session="") -> str:
    """The same filename, built from the default template instead.

    Deliberately calls the app's own `export_fields` rather than restating the
    rules. A copy here would prove the test agrees with itself while the app
    did something else.
    """
    return expand_template(DEFAULT_TEMPLATE, export_fields(
        piece, index, total, PRESETS[preset_key].suffix,
        flight_date=flight_date, session_name=session,
    ))


# -- the requirement that outranks the feature --------------------------------

@pytest.mark.parametrize("preset_key", sorted(PRESETS))
@pytest.mark.parametrize("flight_date", [None, date(2026, 7, 4)])
@pytest.mark.parametrize("ranges", [
    pytest.param([], id="untrimmed"),
    pytest.param([Select(12.0, 48.0, "")], id="one-unnamed"),
    pytest.param([Select(12.0, 48.0, "Launch")], id="one-named"),
    pytest.param([Select(12.0, 48.0, "Launch"), Select(96.0, 141.0, "")],
                 id="two-half-named"),
    pytest.param([Select(12.0, 48.0, "Tree dive!"),
                  Select(96.0, 141.0, "gap #2"),
                  Select(150.0, 190.0, "")], id="three-awkward-names"),
])
def test_the_default_template_reproduces_todays_filenames(preset_key,
                                                          flight_date, ranges):
    """Every combination of preset, date and range shape the app can export.

    A mismatch here is not a failing test, it is a silent rename of somebody's
    library the first time they update.
    """
    source = clip()
    pieces = pieces_of(source, ranges) if ranges else [source]
    total = len(pieces)

    for index, piece in enumerate(pieces):
        today = output_path(Path("/out"), todays_stem(piece, index, total),
                            preset_key, subfolders=False,
                            flight_date=flight_date).name
        templated = template_stem(piece, index, total, preset_key,
                                  flight_date) + PRESETS[preset_key].extension
        assert templated == today, (
            f"{preset_key} range {index + 1} of {total}: template produced "
            f"{templated!r}, the app produces {today!r}"
        )


def test_an_already_dated_clip_is_not_dated_twice():
    """Files copied to the library already start with a date, and the export
    of one must not read 2026-07-04_2026-07-04_hdz_048."""
    source = clip("2026-07-04_hdz_048.ts")
    stem = template_stem(source, 0, 1, "master", date(2026, 7, 4))
    assert stem == "2026-07-04_hdz_048_master"
    assert stem.count("2026-07-04") == 1


# -- the rules the expansion has to hold ---------------------------------------

def test_an_empty_field_takes_its_separator_with_it():
    """Otherwise an untitled range reads hdz_048_3__upload, and a Remux export
    — whose preset suffix is empty on purpose — ends in an underscore."""
    assert expand_template(DEFAULT_TEMPLATE, {
        "date": "", "session": "", "clip": "hdz_048",
        "range": "", "range_number": "3", "preset": "upload",
    }) == "hdz_048_3_upload"

    assert expand_template(DEFAULT_TEMPLATE, {
        "date": "", "session": "", "clip": "hdz_048",
        "range": "", "range_number": "", "preset": "",
    }) == "hdz_048"


def test_each_value_is_sanitised_on_its_own_not_the_finished_string():
    """safe_name strips separators the template itself supplies. Cleaning the
    whole expansion afterwards would let a typed name look like a field
    boundary."""
    stem = expand_template("{clip}_{range}", {
        "clip": "hdz_048", "range": "over/the: gate",
    })
    assert stem == "hdz_048_overthe-gate"


def test_an_unknown_field_is_refused_rather_than_left_empty():
    """A typo in {clipp} must say so. Expanding it to nothing would export
    every clip in the queue under one name, and the first would survive."""
    with pytest.raises(UnknownTemplateField) as raised:
        expand_template("{clipp}_{preset}", {"clip": "hdz_048"})
    assert "clipp" in str(raised.value)
    # and it names what is available, because the person is mid-typo
    for field in TEMPLATE_FIELDS:
        assert f"{{{field}}}" in str(raised.value)


def test_checking_a_template_does_not_need_any_values():
    """The export panel validates what was typed as it is typed, with no clip
    selected and nothing queued."""
    check_template(DEFAULT_TEMPLATE)
    with pytest.raises(UnknownTemplateField):
        check_template("{flight}")


def test_a_template_that_is_all_empty_fields_does_not_produce_a_dotfile():
    """Every field can be empty except clip. Stripping the leading separator
    matters: `.upload` is hidden on Unix and refused on Windows."""
    stem = expand_template("{date}_{session}_{range}", {})
    assert stem == ""


# -- the control, and the round trip a session depends on ----------------------

def test_the_template_survives_capture_and_apply(qt_app):
    """A session stores what capture() returns and hands it back to apply().
    A template that did not survive that would silently revert to the default
    on reopening a card, renaming everything queued afterwards."""
    from flightdvr.export_panel import ExportPanel

    panel = ExportPanel()
    panel.template_edit.setText("{session}_{clip}_{range}")
    stored = panel.capture()
    assert stored["template"] == "{session}_{clip}_{range}"

    other = ExportPanel()
    other.apply(stored)
    assert other.template() == "{session}_{clip}_{range}"


def test_an_empty_box_means_the_default_not_a_nameless_file(qt_app):
    """Clearing the field is how somebody asks for "however it used to be",
    not for every export to be called the same nothing."""
    from flightdvr.export_panel import ExportPanel

    panel = ExportPanel()
    panel.template_edit.setText("   ")
    assert panel.template() == DEFAULT_TEMPLATE
    assert panel.capture()["template"] == DEFAULT_TEMPLATE


def test_an_older_session_without_a_template_keeps_the_default(qt_app):
    """Every session written before this existed has no template key. Reading
    one must not blank the control."""
    from flightdvr.export_panel import ExportPanel

    panel = ExportPanel()
    panel.apply({"preset": "master"})          # a 1.5-era settings dict
    assert panel.template() == DEFAULT_TEMPLATE


def test_the_example_shows_what_the_template_will_produce(qt_app):
    """The control is only useful if the result is visible while typing."""
    from flightdvr.export_panel import ExportPanel

    panel = ExportPanel()
    panel.preset_buttons["upload"].setChecked(True)
    panel.template_edit.setText("{clip}_{range}")
    panel._show_example()
    assert panel.template_example.text() == "e.g. hdz_048_Launch.mp4"


def test_a_mistyped_field_says_so_in_the_example(qt_app):
    """Before the queue, and before anything is written. The example is where
    a typo should surface."""
    from flightdvr.export_panel import ExportPanel

    panel = ExportPanel()
    panel.template_edit.setText("{clipp}")
    panel._show_example()
    shown = panel.template_example.text()
    assert "clipp" in shown and "{clip}" in shown

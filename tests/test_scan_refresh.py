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

"""Derived refreshes while pre-probed scan rows reach the window (#117)."""

from __future__ import annotations

from PySide6.QtCore import Qt

from test_music_wiring import (  # noqa: F401
    a_clip as clip,
    app,
    probes,
    sessions_home,
    window,
)


def test_inserting_a_scan_row_does_not_imitate_six_user_edits(
        window, monkeypatch):
    """Six programmatic cells used to run the whole derived refresh chain.

    The row and its progressive count must appear immediately, but only an
    intentional edit after delivery should emit ``itemChanged``. The scan's
    explicit final flush is tested separately.
    """
    table = window.table
    table.setSortingEnabled(False)
    table.setRowCount(0)
    window.clips.clear()
    window.clip_by_path.clear()
    window._expected = 1
    window._scan_rebuilding = True
    generation = window._scan_generation
    refreshed = []
    monkeypatch.setattr(window, "_update_counts",
                        lambda: refreshed.append("derived"))
    monkeypatch.setattr(window.thumbs, "request", lambda *_: None)

    try:
        made = clip("hdz_117.ts")
        window._add_clip(generation, made)

        assert refreshed == [], (
            "programmatic cells were delivered as itemChanged user edits"
        )
        assert table.rowCount() == 1
        assert window.clips == [made]
        assert window.clip_by_path == {str(made.path): made}
        assert window.clip_count_label.text() == "Reading 1 of 1 clips…"

        table.item(0, 0).setCheckState(Qt.CheckState.Checked)
        assert refreshed == ["derived"], (
            "suppressing scan delivery also suppressed an intentional edit"
        )
    finally:
        table.setRowCount(0)
        window.clips.clear()
        window.clip_by_path.clear()
        window._expected = 0
        window._scan_rebuilding = False
        table.setSortingEnabled(True)


def test_a_cancelled_partial_scan_keeps_its_rows_and_flushes_once(
        window, monkeypatch):
    """A worker reports the rows it finished before cancellation through done.

    The final callback must derive state once from that useful partial list;
    stale rows and stale completion callbacks must still be ignored.
    """
    table = window.table
    table.setSortingEnabled(False)
    table.setRowCount(0)
    window.clips.clear()
    window.clip_by_path.clear()
    window._expected = 3
    window._scan_rebuilding = True
    window._scan_source = None
    window._flight_scan_ready = False
    generation = window._scan_generation
    updates = []
    markers = []
    original_update_counts = window._update_counts

    def update_counts():
        updates.append("derived")
        original_update_counts()

    monkeypatch.setattr(window, "_update_counts", update_counts)
    monkeypatch.setattr(window, "_refresh_export_markers",
                        lambda: markers.append("markers"))
    monkeypatch.setattr(window, "_source_path", lambda: None)
    monkeypatch.setattr(window, "_apply_decision_availability", lambda: None)
    monkeypatch.setattr(window, "_start_flight_analysis", lambda: None)
    monkeypatch.setattr(window.thumbs, "request", lambda *_: None)
    monkeypatch.setattr(window.thumbs, "resume", lambda: None)

    try:
        first = clip("hdz_117_a.ts")
        second = clip("hdz_117_b.ts")
        window._add_clip(generation, first)
        window._add_clip(generation - 1, clip("stale.ts"))
        window._add_clip(generation, second)

        assert updates == []
        assert markers == []
        assert window.clips == [first, second]
        assert table.rowCount() == 2
        assert window.clip_count_label.text() == "Reading 2 of 3 clips…"

        window._scan_done(generation - 1, 99)
        assert updates == []
        assert markers == []
        assert window._scan_rebuilding

        window._scan_done(generation, 2)
        assert updates == ["derived"]
        assert markers == ["markers"]
        assert window.clip_count_label.text() == "2 clips found, 0 ticked"
        assert not window._scan_rebuilding
        assert window._flight_scan_ready
    finally:
        table.setRowCount(0)
        window.clips.clear()
        window.clip_by_path.clear()
        window._expected = 0
        window._scan_rebuilding = False
        window._scan_source = None
        window._flight_scan_ready = False
        table.setSortingEnabled(True)

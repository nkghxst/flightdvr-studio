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

"""The test run's own home, and a window that outlives one test.

`conftest.user_storage` points the whole run at a disposable home before any
window exists. These check that every place the app keeps things resolves
there, including for a window built once per module and used across tests —
the case a per-test redirect starts too late for.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from flightdvr import session as session_module
from flightdvr import thumbs, trim
from flightdvr.media import ClipInfo


def test_the_run_has_a_disposable_home(user_storage):
    assert Path.home() == user_storage
    for name in ("HOME", "USERPROFILE"):
        assert os.environ[name] == str(user_storage)
    for folder in (session_module.sessions_dir(), thumbs.cache_dir(),
                   trim.cache_root(), session_module.recent_path().parent):
        assert folder.is_relative_to(user_storage), folder


class _NoProbe(QObject):
    result = Signal(object)

    def __init__(self, tools, parent=None):
        super().__init__(parent)

    def start(self, *_args) -> None:
        pass

    def isRunning(self) -> bool:  # noqa: N802 (Qt naming)
        return False

    def stop(self) -> None:
        pass

    def wait(self, *_args) -> bool:
        return True


@pytest.fixture(scope="module")
def card(tmp_path_factory):
    folder = tmp_path_factory.mktemp("card")
    return folder


@pytest.fixture(scope="module")
def module_window(card):
    """Built once, before the first test in this module runs."""
    from flightdvr.media import find_tools
    from flightdvr.ui import MainWindow

    app = QApplication.instance() or QApplication([])
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("flightdvr.ui.HardwareProbe", _NoProbe)
        patch.setattr("flightdvr.updates.should_check", lambda *a, **k: False)
        made = MainWindow(find_tools())
        made.source_combo.insertItem(0, str(card), str(card))
        made.source_combo.setCurrentIndex(0)
        clip = ClipInfo(path=card / "hdz_001.ts", size=4096,
                        modified=datetime(2025, 10, 8, 18, 39), duration=60.0,
                        width=1280, height=720, fps=60.0, video_codec="hevc")
        made._add_clip(made._scan_generation, clip)
        made._scan_done(made._scan_generation, 1)
        app.processEvents()
        yield made
        made.close()


def test_a_module_window_saves_into_the_disposable_home(module_window,
                                                        user_storage):
    module_window.clips[0].trim_in = 2.0
    module_window.clips[0].trim_out = 8.0
    module_window._touch_session()
    module_window._flush_session()
    assert module_window.session.path.is_relative_to(user_storage)
    assert module_window.session.path.exists()
    recent = session_module.recent_path()
    assert recent.is_relative_to(user_storage) and recent.exists()


def test_a_pending_save_is_left_armed_for_teardown(module_window, user_storage):
    """Left armed on purpose: the module's close, and the run's own teardown
    after it, must still write here. `user_storage` fails the run if the real
    home changed."""
    module_window.clips[0].trim_out = 9.0
    module_window._touch_session()
    assert module_window._session_timer.isActive()

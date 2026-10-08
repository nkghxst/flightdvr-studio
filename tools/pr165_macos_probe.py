"""Instrumentation-only pytest hook for the one-shot PR165 macOS diagnosis.

It observes the existing tests at their original source lines. No production
object or assertion is patched. The diagnostic branch is not a release input.
"""

from __future__ import annotations

import json
import linecache
import os
from pathlib import Path
import sys
import time

import pytest


SAVED = "test_a_saved_depth_is_fitted_to_the_room_when_music_opens"
LIVE = "test_ui_constructed_all_silent_sequence_needs_no_source_reader"


def _size(value):
    return [value.width(), value.height()]


def _geometry(window):
    from PySide6.QtCore import qVersion
    from PySide6.QtGui import QGuiApplication
    from flightdvr.ui import CLASSIC_MUSIC_MINIMUM

    view = window.preview_view
    splitter = window.splitter
    screen = QGuiApplication.primaryScreen()
    body = view.music_body
    current = body.height() if body.isVisible() else 0
    slack = (max(0, splitter.height() - splitter.minimumSizeHint().height())
             if splitter is not None and splitter.isVisible() else 0)
    least = max(CLASSIC_MUSIC_MINIMUM, view.track_button.sizeHint().height())
    return {
        "window_size": _size(window.size()),
        "window_minimum_size_hint": _size(window.minimumSizeHint()),
        "music_checked": view.music_band.isChecked(),
        "music_body_visible": body.isVisible(),
        "music_body_height": body.height(),
        "music_body_minimum_size_hint": _size(body.minimumSizeHint()),
        "band_minimum_size_hint": _size(view.music_band.minimumSizeHint()),
        "splitter_visible": bool(splitter and splitter.isVisible()),
        "splitter_height": splitter.height() if splitter else None,
        "splitter_minimum_size_hint": (
            _size(splitter.minimumSizeHint()) if splitter else None),
        "current": current,
        "slack": slack,
        "least": least,
        "predicted_fitted_240": max(least, min(240, current + slack)),
        "screen_available_geometry": (
            [screen.availableGeometry().x(), screen.availableGeometry().y(),
             screen.availableGeometry().width(), screen.availableGeometry().height()]
            if screen else None),
        "qt_platform": QGuiApplication.platformName(),
        "qt_version": qVersion(),
    }


def _record(kind, **values):
    record = {"kind": kind, "monotonic": time.monotonic(), **values}
    line = json.dumps(record, sort_keys=True, default=str)
    print("PR165_PROBE " + line, flush=True)
    with Path(os.environ["PR165_PROBE_LOG"]).open("a", encoding="utf-8") as out:
        out.write(line + "\n")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    if not (item.nodeid.endswith(SAVED) or item.nodeid.endswith(LIVE)):
        yield
        return

    original = sys.gettrace()
    counters = {"pull_attempts": 0, "buffering_waits": 0,
                "last_output_start": None, "start": None}

    def trace(frame, event, arg):
        filename = frame.f_code.co_filename.replace("\\", "/")
        function = frame.f_code.co_name
        if event == "call":
            if function == SAVED and filename.endswith("/tests/test_music_wiring.py"):
                return trace
            if function == "_fitted_music_depth" and filename.endswith("/flightdvr/ui.py"):
                return trace
            if function == "_pull_stream_at" and filename.endswith("/tests/test_live_preview.py"):
                counters.update(pull_attempts=0, buffering_waits=0,
                                last_output_start=None, start=time.monotonic())
                return trace
            return None
        if function == SAVED and event == "line":
            source = linecache.getline(filename, frame.f_lineno).strip()
            if source == "view.music_band.setChecked(True)":
                _record("saved_depth_before_toggle", **_geometry(frame.f_locals["window"]))
            elif source.startswith("assert window.size() == size"):
                _record("saved_depth_after_settle", original_size=_size(frame.f_locals["size"]),
                        **_geometry(frame.f_locals["window"]))
        elif function == "_fitted_music_depth" and event == "return":
            local = frame.f_locals
            _record("fitted_depth_return", wanted=local.get("wanted"),
                    current=local.get("current"), slack=local.get("slack"),
                    least=local.get("least"), fitted=arg)
        elif function == "_pull_stream_at":
            if event == "line":
                source = linecache.getline(filename, frame.f_lineno).strip()
                if source == "block = stream.pull()":
                    counters["pull_attempts"] += 1
                elif source == "time.sleep(0.01)":
                    counters["buffering_waits"] += 1
                elif source == "if block.output_start == output_sample:":
                    counters["last_output_start"] = frame.f_locals["block"].output_start
                elif source.startswith("pytest.fail(f\"stream produced no block"):
                    _record("live_preview_timeout", output_sample=frame.f_locals["output_sample"],
                            state=frame.f_locals["stream"].state.value,
                            elapsed=time.monotonic() - counters["start"], **{
                                key: counters[key] for key in (
                                    "pull_attempts", "buffering_waits", "last_output_start")})
            elif event == "return":
                _record("live_preview_helper_return", output_sample=frame.f_locals["output_sample"],
                        elapsed=time.monotonic() - counters["start"],
                        returned_output_start=getattr(arg, "output_start", None), **{
                            key: counters[key] for key in (
                                "pull_attempts", "buffering_waits", "last_output_start")})
        return trace

    sys.settrace(trace)
    try:
        yield
    finally:
        sys.settrace(original)

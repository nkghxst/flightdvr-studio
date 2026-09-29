# FlightDVR Studio - browse, trim and convert HDZero goggle DVR footage.
# Copyright (C) 2026 Isadu Nkemi
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.

"""P2: media correctness.

P2a, cancellation cleanup: a cancelled job removes its own unfinished file,
and when it cannot, it says so precisely rather than leaving it silently. The
Stage A Windows CI failure (the job's own partial left behind once, not
reproduced in six local repeats, passing on rerun) is preserved as history in
the P2 handoff; nothing here claims to have explained it. What these tests
hold is the contract around it.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import time
from copy import deepcopy
from pathlib import Path

import pytest

import flightdvr.audio_export as audio_export
from flightdvr.audio_plan import AudioMode
from flightdvr.jobs import ExportWorker, Job, JobStatus
from flightdvr.presets import ExportSettings

# The generated media and tools Stage A already built for the same worker.
from tests.test_audio_export import choice, media, tools  # noqa: F401

pytestmark = pytest.mark.integration


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cancel_during_the_real_pcm_count(media, tmp_path, monkeypatch,  # noqa: F811
                                      unlink_refusal=None):
    """Two Edit jobs; the first is cancelled while the real PCM count is
    reading its finished partial. Returns what the test needs to judge."""
    out = tmp_path / "edit.mov"
    out.write_bytes(b"previous destination")
    neighbour = tmp_path / "edit-neighbour.flightdvr-part.mov"
    neighbour.write_bytes(b"another job's unfinished file")
    before = {path: _digest(path) for path in (out, neighbour)}
    first = Job([media.source], "edit", ExportSettings(edit_codec="prores_lt"),
                out, audio=choice(media.asset, AudioMode.REPLACE))
    second = Job([media.silent], "edit", ExportSettings(edit_codec="prores_lt"),
                 tmp_path / "second.mov", audio=choice(media.asset, AudioMode.MIX))
    second_before = deepcopy(second)
    part = out.with_name("edit.flightdvr-part.mov")

    real_popen = subprocess.Popen
    decodes, seen_part = [], []

    def slow_decode(command, *args, **kwargs):
        # Only the count's decode is slowed: read at playback speed.
        if "s16le" in command and "-map" in command:
            seen_part.append(part.exists() and part.stat().st_size)
            command = list(command)
            command.insert(command.index("-i"), "-re")
            proc = real_popen(command, *args, **kwargs)
            decodes.append(proc)
            return proc
        return real_popen(command, *args, **kwargs)

    monkeypatch.setattr(audio_export.subprocess, "Popen", slow_decode)
    unlinked = []
    real_unlink = Path.unlink

    def watched_unlink(self, *args, **kwargs):
        unlinked.append(Path(self))
        if unlink_refusal is not None and Path(self) == part:
            raise unlink_refusal
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", watched_unlink)
    worker = ExportWorker(None, [first, second], tmp_path / "work")
    from flightdvr.media import find_tools
    worker.tools = find_tools()
    runner = threading.Thread(target=worker.run, daemon=True)
    runner.start()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not decodes:
        time.sleep(0.01)
    assert decodes, "the PCM count never started"
    assert decodes[0].poll() is None, "the count finished before cancel"
    assert worker._process is None, "FFmpeg's encode had already finished"
    worker.cancel()
    runner.join(timeout=15)
    assert not runner.is_alive(), "cancel was not seen during the count"
    return {
        "worker": worker, "first": first, "second": second,
        "second_before": second_before, "out": out, "neighbour": neighbour,
        "before": before, "part": part, "decodes": decodes,
        "seen_part": seen_part, "unlinked": unlinked,
    }


def test_a_cancel_during_the_real_pcm_count_removes_its_own_partial(
        media, tmp_path, monkeypatch):  # noqa: F811
    got = _cancel_during_the_real_pcm_count(media, tmp_path, monkeypatch)
    first, part = got["first"], got["part"]
    # The partial was real and the validator was really reading it.
    assert got["seen_part"] and got["seen_part"][0] > 0
    assert got["decodes"][0].returncode not in (None, 0), "decode not stopped"
    assert first.status is JobStatus.CANCELLED
    # If this fails, the worker says why: that is the diagnostic P2a adds.
    assert not part.exists(), got["worker"].residuals.get(0, "(no residual)")
    assert first.message == "Cancelled"
    assert 0 not in got["worker"].residuals
    for path, digest in got["before"].items():
        assert _digest(path) == digest, path
    # The second job never started, and its submission is untouched.
    assert got["second"].status is JobStatus.CANCELLED
    assert (got["second"].settings, got["second"].audio,
            got["second"].out_path) == (
        got["second_before"].settings, got["second_before"].audio,
        got["second_before"].out_path)
    assert not (tmp_path / "second.mov").exists()


def test_an_own_partial_that_cannot_be_removed_is_said_not_swallowed(
        media, tmp_path, monkeypatch):  # noqa: F811
    refusal = PermissionError(
        13, "The process cannot access the file because it is being used by "
            "another process", "edit.flightdvr-part.mov",
        *((32,) if os.name == "nt" else ()))
    got = _cancel_during_the_real_pcm_count(
        media, tmp_path, monkeypatch, unlink_refusal=refusal)
    first, part = got["first"], got["part"]
    assert first.status is JobStatus.CANCELLED, "still Cancelled"
    assert first.message.startswith("Cancelled — ")
    assert "could not be removed (PermissionError, errno 13" in first.message
    if os.name == "nt":
        assert "winerror 32" in first.message
    assert str(part) in first.message, "the exact path to remove by hand"
    assert "(the job was checking the sound)" in first.message
    assert part.exists(), "the refused partial is where the message says"
    # Only the job's own partial was ever unlinked; nothing else of anyone's.
    owned = {part}
    assert set(got["unlinked"]) <= owned | set(
        (tmp_path / "work").glob("*")), got["unlinked"]
    assert got["neighbour"] not in got["unlinked"]
    assert got["out"] not in got["unlinked"]
    for path, digest in got["before"].items():
        assert _digest(path) == digest, path
    assert got["second"].status is JobStatus.CANCELLED
    assert "could not be removed" not in got["second"].message


def test_a_failed_job_keeps_its_own_failure_and_adds_the_residual(
        media, tmp_path, monkeypatch):  # noqa: F811
    """Not only cancellation: a job that fails and then cannot remove its own
    partial reports both, the failure first."""
    from flightdvr.media import find_tools

    out = tmp_path / "failed.mov"
    part = out.with_name("failed.flightdvr-part.mov")
    job = Job([media.source], "edit", ExportSettings(edit_codec="prores_lt"),
              out, audio=choice(media.asset, AudioMode.REPLACE))
    monkeypatch.setattr(audio_export, "validate_expected_audio",
                        lambda *a, **k: (False, "the sound was wrong"))
    real_unlink = Path.unlink

    def refuse(self, *args, **kwargs):
        if Path(self) == part and part.exists():
            raise PermissionError(13, "held", str(self))
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse)
    worker = ExportWorker(find_tools(), [job], tmp_path / "work")
    worker.run()
    assert job.status is JobStatus.FAILED
    assert job.message.startswith("the sound was wrong — its unfinished file "
                                  "could not be removed")
    assert str(part) in job.message
    monkeypatch.setattr(Path, "unlink", real_unlink)
    part.unlink(missing_ok=True)

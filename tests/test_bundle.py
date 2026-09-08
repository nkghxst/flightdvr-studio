"""Delivery bundles: several presets out of one range or one assembly (#61).

The risk in this feature is not that it queues too little. It is that it queues
something *plausible* — three jobs that look right in the queue and turn out to
be two files because two of them shared a name, or a member whose settings
moved between the confirmation and the encode. Both are invisible until
somebody opens the folder.

So the tests here mostly compare the bundle against the path that already
works: the same material through `_add_to_queue`, one preset at a time, has to
produce the same target paths as the bundle does in one action. That assertion
comes from the existing behaviour rather than from reading the new code, which
is the only version of it worth having.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import date, datetime  # noqa: E402
from pathlib import Path  # noqa: E402

from flightdvr.media import ClipInfo, Select  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _clip(tmp_path, name, ranges=(), width=1280, height=720, fps=60.0,
          sequence_size=1) -> ClipInfo:
    """A recording with the shape of a Box Pro's, and nothing on disk to decode.

    The bundle never opens the file — it plans names, sizes and refusals — so a
    real encode here would buy nothing and cost fifteen seconds.
    """
    source = tmp_path / name
    source.write_bytes(b"x" * sequence_size)
    clip = ClipInfo(path=source, size=source.stat().st_size,
                    modified=datetime.fromtimestamp(source.stat().st_mtime),
                    duration=60.0, width=width, height=height, fps=fps,
                    video_codec="hevc", audio_codec="aac", pix_fmt="yuvj420p",
                    color_range="pc")
    clip.selects = [Select(start, end, label) for start, end, label in ranges]
    return clip


def _pieces(clips):
    """The grouping `_bundle_material` builds, without needing a window."""
    from flightdvr.bundle import Piece

    grouped = []
    for clip in clips:
        parts = clip.for_export()
        for index, piece in enumerate(parts):
            grouped.append(Piece(piece, index, len(parts)))
    return grouped


def _context(tmp_path, **overrides):
    from flightdvr.presets import ExportSettings

    context = dict(
        joined=False,
        out_dir=tmp_path / "out",
        template="{date}_{clip}_{range_number}_{range}_{preset}",
        subfolders=False,
        stamp=None,
        session_name="",
        settings=ExportSettings(),
    )
    context.update(overrides)
    return context


# -- planning, without a window -------------------------------------------------


def test_a_source_too_narrow_to_crop_disables_vertical_rather_than_queueing_it(tmp_path):
    """A member the app already knows would fail must not reach the queue.

    Vertical on a source narrower than 9:16 is refused by `vertical_problems`
    at export time. Offering it in a bundle would mean queueing a job whose
    failure was known while the dialog was on screen.
    """
    from flightdvr.bundle import plan_bundle

    narrow = _clip(tmp_path, "hdz_047.ts", [(0.0, 4.0, "")], width=320, height=720)
    members = {m.key: m for m in plan_bundle(
        ["master", "vertical"], _pieces([narrow]), **_context(tmp_path))}

    assert members["master"].usable, "an ordinary preset was refused"
    assert not members["vertical"].usable
    assert "narrow" in members["vertical"].problem
    assert members["vertical"].jobs == [], "a refused member still planned work"


def test_a_clip_with_no_readable_rate_disables_slow_motion_only(tmp_path):
    """Slow motion cannot keep every frame once if it cannot count them.

    The single-preset path refuses the whole action. In a bundle the other
    members are still perfectly deliverable, so only the one that cannot keep
    its promise is withdrawn.
    """
    from flightdvr.bundle import plan_bundle

    rateless = _clip(tmp_path, "hdz_047.ts", [(0.0, 4.0, "")], fps=0.0)
    members = {m.key: m for m in plan_bundle(
        ["master", "slowmo"], _pieces([rateless]), **_context(tmp_path))}

    assert members["master"].usable
    assert not members["slowmo"].usable
    assert members["slowmo"].problem


def test_two_members_writing_one_filename_are_named_before_anything_is_queued(tmp_path):
    """The failure this dialog exists to prevent.

    A template with no `{preset}` field and subfolders turned off gives Master,
    Upload, Social, Vertical and Slow motion the same stem and the same `.mp4`,
    so five queued jobs would leave one file and the last one would win. Both
    labels have to appear, because "there is a clash" does not tell anybody
    which pair to separate.
    """
    from flightdvr.bundle import collisions, plan_bundle

    clip = _clip(tmp_path, "hdz_047.ts", [(0.0, 4.0, "")])
    members = plan_bundle(["master", "upload"], _pieces([clip]),
                          **_context(tmp_path, template="{clip}"))

    clashes = collisions(members, set())
    assert clashes, "two presets sharing a filename were not reported"
    assert "Master" in clashes[0] and "Upload" in clashes[0]


def test_the_preset_field_is_what_keeps_members_apart(tmp_path):
    """The other half of the check above: the default template is fine.

    Without this, the collision test would pass just as well against code that
    reported a clash for every bundle.
    """
    from flightdvr.bundle import collisions, plan_bundle

    clip = _clip(tmp_path, "hdz_047.ts", [(0.0, 4.0, "")])
    members = plan_bundle(["master", "upload", "social"], _pieces([clip]),
                          **_context(tmp_path))

    assert collisions(members, set()) == []


def test_a_target_already_in_the_queue_refuses_the_bundle(tmp_path):
    """Refused, where the single-preset path would skip it.

    Re-ticking clips queued a moment ago is ordinary, and skipping the
    duplicate there is right. A bundle is one deliberate action whose promise
    is that the files it listed are the files that get made, so a member that
    would be silently dropped is a refusal instead.
    """
    from flightdvr.bundle import collisions, plan_bundle
    from flightdvr.format import output_key

    clip = _clip(tmp_path, "hdz_047.ts", [(0.0, 4.0, "")])
    members = plan_bundle(["master", "upload"], _pieces([clip]),
                          **_context(tmp_path))
    master = next(m for m in members if m.key == "master")
    already = {output_key(master.jobs[0].target)}

    clashes = collisions(members, already)
    assert len(clashes) == 1
    assert "already in the queue" in clashes[0]


def test_two_ranges_that_would_share_a_name_refuse_their_own_member(tmp_path):
    """A member cannot quietly deliver fewer files than it says it will.

    A template that ignores the range number and the range name gives two
    ranges of one recording the same target, so only one file would ever
    exist. That is the member's problem rather than the bundle's: it is
    offered, greyed out, with the name that would have been overwritten.
    """
    from flightdvr.bundle import plan_bundle

    clip = _clip(tmp_path, "hdz_047.ts",
                 [(0.0, 4.0, "one"), (10.0, 14.0, "two")])
    member = plan_bundle(["master"], _pieces([clip]),
                         **_context(tmp_path, template="{clip}_{preset}"))[0]

    assert not member.usable
    assert "hdz_047_master.mp4" in member.problem
    assert member.jobs == []


def test_the_size_and_runtime_shown_are_the_ones_the_queue_will_carry(tmp_path):
    """The confirmation is a promise about the jobs, so it is computed from them.

    Recomputed here from the planned jobs with the preset's own estimator,
    rather than compared against a number this module also produced.
    """
    from flightdvr.bundle import plan_bundle
    from flightdvr.presets import ExportSettings, estimate_output_size, output_runtime

    settings = ExportSettings()
    clip = _clip(tmp_path, "hdz_047.ts", [(0.0, 4.0, "a"), (10.0, 16.0, "b")])
    member = plan_bundle(["master"], _pieces([clip]),
                         **_context(tmp_path, settings=settings))[0]

    expected_size = sum(estimate_output_size(c, "master", settings)
                        for job in member.jobs for c in job.clips)
    expected_runtime = output_runtime("master", sum(
        c.trimmed_duration or c.duration for job in member.jobs for c in job.clips))

    assert member.size == expected_size
    assert member.runtime == pytest.approx(expected_runtime)


def test_slow_motion_is_shown_as_the_runtime_it_writes_not_the_footage(tmp_path):
    """Two ranges of ten seconds are forty seconds of Slow motion, not twenty.

    The number people plan a card around is what comes out, and Slow motion is
    the one preset for which the two differ.
    """
    from flightdvr.bundle import plan_bundle

    clip = _clip(tmp_path, "hdz_047.ts", [(0.0, 10.0, "a"), (20.0, 30.0, "b")])
    context = _context(tmp_path)
    members = {m.key: m for m in plan_bundle(["master", "slowmo"],
                                             _pieces([clip]), **context)}

    assert members["master"].runtime == pytest.approx(20.0)
    assert members["slowmo"].runtime == pytest.approx(40.0)


# -- the confirmation itself ----------------------------------------------------


def test_the_dialog_will_not_add_a_bundle_it_has_already_refused(qt_app, tmp_path):
    """Ticking two members that clash disables Add rather than explaining later.

    Driving the real dialog, not a stand-in: what is being checked is that the
    button state follows the tickboxes, and a fake checkbox would only prove
    the fake.
    """
    from flightdvr.bundle import plan_bundle
    from flightdvr.bundle_panel import BundleDialog

    clip = _clip(tmp_path, "hdz_047.ts", [(0.0, 4.0, "")])
    members = plan_bundle(["master", "upload"], _pieces([clip]),
                          **_context(tmp_path, template="{clip}"))

    dialog = BundleDialog(members, set(), [], None)
    try:
        dialog._boxes["master"].setChecked(True)
        assert dialog.add_button.isEnabled(), "one member alone cannot clash"

        dialog._boxes["upload"].setChecked(True)
        assert not dialog.add_button.isEnabled()
        assert "Master" in dialog.summary.text()
        assert "Upload" in dialog.summary.text()

        dialog._boxes["upload"].setChecked(False)
        assert dialog.add_button.isEnabled(), "the refusal did not clear"
    finally:
        dialog.deleteLater()


def test_a_refused_member_cannot_be_ticked_and_says_why(qt_app, tmp_path):
    """Greyed out with the reason beside it, not hidden.

    "Why can I not have a vertical of this" is a question the checklist should
    answer where it is asked.
    """
    from flightdvr.bundle import plan_bundle
    from flightdvr.bundle_panel import BundleDialog

    narrow = _clip(tmp_path, "hdz_047.ts", [(0.0, 4.0, "")], width=320, height=720)
    members = plan_bundle(["master", "vertical"], _pieces([narrow]),
                          **_context(tmp_path))

    dialog = BundleDialog(members, set(), ["master", "vertical"], None)
    try:
        assert not dialog._boxes["vertical"].isEnabled()
        assert not dialog._boxes["vertical"].isChecked(), (
            "a remembered selection ticked a member that cannot be delivered")
        assert dialog._boxes["master"].isChecked()
        assert "vertical" not in dialog.chosen()
    finally:
        dialog.deleteLater()


def test_every_row_is_tab_reachable_and_answers_space(qt_app, tmp_path):
    """A field table is not a place to rely on a trackpad.

    Offscreen has no key mapper and no real focus, so what can honestly be
    checked here is the focus *policy* — whether Tab would reach the row — and
    that the widget answers a Space delivered to it. That a real keyboard walks
    the dialog is checked natively instead, and said so in the PR rather than
    implied by this test.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from flightdvr.bundle import plan_bundle
    from flightdvr.bundle_panel import BundleDialog

    clip = _clip(tmp_path, "hdz_047.ts", [(0.0, 4.0, "")])
    members = plan_bundle(["master", "upload"], _pieces([clip]),
                          **_context(tmp_path))

    dialog = BundleDialog(members, set(), [], None)
    dialog.show()
    qt_app.processEvents()
    try:
        for key in ("master", "upload"):
            box = dialog._boxes[key]
            # Tab order is a focus policy, and this platform will answer that
            # honestly even though it has no key mapper.
            assert box.focusPolicy() != Qt.FocusPolicy.NoFocus, (
                f"{key} is not reachable by Tab")
            QTest.keyClick(box, Qt.Key.Key_Space)
            assert box.isChecked(), f"{key} did not answer Space"
        assert dialog.chosen() == ["master", "upload"]
    finally:
        dialog.close()
        dialog.deleteLater()


# -- through the window ---------------------------------------------------------


@pytest.fixture(scope="module")
def window(qt_app):
    from flightdvr.media import ToolsMissing, find_tools
    from flightdvr.ui import MainWindow

    try:
        tools = find_tools()
    except ToolsMissing:
        pytest.skip("the window needs ffmpeg on this machine")
    made = MainWindow(tools)
    yield made
    made.close()


@pytest.fixture
def bench(window, tmp_path, monkeypatch):
    """A window pointed at throwaway clips, with an empty queue.

    Module-scoped windows are expensive, so the state each test depends on is
    reset here rather than assumed.
    """
    clips = [
        _clip(tmp_path, "hdz_047.ts", [(0.0, 4.0, "")], sequence_size=2),
        _clip(tmp_path, "hdz_048.ts", [(0.0, 4.0, "")], sequence_size=3),
    ]
    window.jobs.clear()
    window.clips = clips
    window.export_panel.assembly_panel.show_rows([])
    window.export_panel.set_bundle([])
    window.export_panel.out_edit.setCurrentText(str(tmp_path / "out"))
    window.export_panel.template_edit.setText(
        "{date}_{clip}_{range_number}_{range}_{preset}")
    window.export_panel.subfolder_check.setChecked(False)
    window.export_panel.date_check.setChecked(False)
    monkeypatch.setattr(type(window), "selected_clips", lambda self: clips)
    monkeypatch.setattr(window, "_rebuild_queue", lambda: None)
    return clips


def _accept(monkeypatch, keys):
    """Stand in for the person reading the dialog, and nothing else.

    Only the choice is faked. The dialog itself is exercised by its own tests
    above; what these want is the window's behaviour after somebody presses
    Add, without a modal loop nothing can answer.
    """
    import flightdvr.ui as ui
    from PySide6.QtWidgets import QDialog

    class Chosen:
        def __init__(self, members, already, remembered, parent=None):
            self._members = members

        def exec(self):
            return QDialog.DialogCode.Accepted

        def selected_members(self):
            return [m for m in self._members if m.key in keys and m.usable]

    monkeypatch.setattr(ui, "BundleDialog", Chosen)


def test_a_bundle_names_every_member_exactly_as_add_to_queue_would(
        window, bench, monkeypatch):
    """The assertion that matters, and it comes from the existing behaviour.

    A bundle that invented its own naming would be discovered by the person
    who found two differently named copies of one range in a folder. So each
    preset is queued the ordinary way, its targets recorded, and the bundle
    then has to produce exactly those paths in one action.
    """
    wanted = ["master", "social", "upload"]

    singly = {}
    for key in wanted:
        window.jobs.clear()
        window.export_panel.preset_buttons[key].setChecked(True)
        window._add_to_queue()
        singly[key] = [job.out_path for job in window.jobs]
        assert singly[key], f"the ordinary path queued nothing for {key}"

    window.jobs.clear()
    window.export_panel.preset_buttons["master"].setChecked(True)
    _accept(monkeypatch, wanted)
    window._add_bundle()

    together = {}
    for job in window.jobs:
        together.setdefault(job.preset_key, []).append(job.out_path)

    assert together == singly


def test_a_three_preset_bundle_adds_three_jobs_with_distinct_targets(
        window, bench, monkeypatch):
    """One range, three presets, three files that can all exist at once."""
    monkeypatch.setattr(type(window), "selected_clips", lambda self: bench[:1])
    _accept(monkeypatch, ["master", "social", "upload"])
    window._add_bundle()

    assert len(window.jobs) == 3
    assert sorted(j.preset_key for j in window.jobs) == [
        "master", "social", "upload"]
    assert len({str(j.out_path) for j in window.jobs}) == 3


def test_each_member_keeps_the_settings_it_was_confirmed_under(
        window, bench, monkeypatch):
    """A queued export is a promise about the settings that were on screen.

    Changing the panel afterwards must not reach a job somebody has already
    agreed to, so each member holds its own snapshot rather than the panel's
    live object.
    """
    monkeypatch.setattr(type(window), "selected_clips", lambda self: bench[:1])
    _accept(monkeypatch, ["master", "social"])
    window._add_bundle()

    before = {j.preset_key: j.settings.master_crf for j in window.jobs}
    live = window.current_settings()
    live.master_crf = 51

    after = {j.preset_key: j.settings.master_crf for j in window.jobs}
    assert after == before
    assert len({id(j.settings) for j in window.jobs}) == len(window.jobs), (
        "two members shared one settings object")


def test_the_flight_date_does_not_rename_a_bundle_member_afterwards(
        window, bench, monkeypatch):
    """The confirmation showed a filename, so that is the filename.

    The ordinary retarget exists for the opposite case — a job queued before
    somebody remembered the date — and it is asserted here too, so this test
    proves the bundle is exempt rather than that retargeting stopped working.
    """
    monkeypatch.setattr(type(window), "selected_clips", lambda self: bench[:1])
    _accept(monkeypatch, ["master"])
    window._add_bundle()
    bundled = window.jobs[0]
    promised = bundled.out_path

    window.export_panel.preset_buttons["upload"].setChecked(True)
    window._add_to_queue()
    ordinary = next(j for j in window.jobs if j.preset_key == "upload")
    was = ordinary.out_path

    window.export_panel.set_flight_date(date(2026, 9, 8))
    window._retarget_pending()

    assert bundled.out_path == promised, "a confirmed bundle member was renamed"
    assert ordinary.out_path != was, "the ordinary retarget stopped working"


def test_an_assembly_bundle_joins_each_member_the_way_one_preset_does(
        window, bench, monkeypatch):
    """One assembly becomes N joined jobs, named as the single-preset join is."""
    from flightdvr.assembly import Item, resolve

    items = [Item(bench[0].fingerprint, bench[0].real_selects[0].sid),
             Item(bench[1].fingerprint, bench[1].real_selects[0].sid)]
    window.export_panel.assembly_panel.show_rows(resolve(items, bench))
    assert window.export_panel.join_enabled()

    window.export_panel.preset_buttons["master"].setChecked(True)
    window._add_to_queue()
    singly = window.jobs[0].out_path
    assert len(window.jobs) == 1, "a join queued more than one file"

    window.jobs.clear()
    _accept(monkeypatch, ["master", "upload"])
    window._add_bundle()

    assert len(window.jobs) == 2, "each member of an assembly bundle is one job"
    master = next(j for j in window.jobs if j.preset_key == "master")
    assert master.out_path == singly
    assert all(len(j.clips) == 2 for j in window.jobs), "a member lost material"
    assert all(j.concat_file is not None for j in window.jobs), (
        "a joined member was queued without its concat list")


def test_a_missing_assembly_row_stops_the_bundle_before_the_dialog(
        window, bench, monkeypatch):
    """The same refusal the ordinary join gives, and for the same reason.

    Queueing round a gap would join a shorter film than the list shows.
    """
    from flightdvr.assembly import Item, resolve

    items = [Item(bench[0].fingerprint, bench[0].real_selects[0].sid),
             Item("fingerprint-of-something-gone")]
    window.export_panel.assembly_panel.show_rows(resolve(items, bench))

    _pieces_out, joined, problem = window._bundle_material()
    assert joined
    assert _pieces_out == []
    assert "not here" in problem


def test_nothing_is_queued_when_one_member_of_the_bundle_would_clash(
        window, bench, monkeypatch):
    """All of it or none of it: a half-added bundle is the worst outcome.

    The queue is left exactly as it was, rather than holding whichever members
    were appended before the refusal.
    """
    monkeypatch.setattr(type(window), "selected_clips", lambda self: bench[:1])
    window.export_panel.template_edit.setText("{clip}")
    _accept(monkeypatch, ["master", "upload"])

    warned = []
    monkeypatch.setattr("flightdvr.ui.QMessageBox.warning",
                        lambda *args, **kw: warned.append(args))
    window._add_bundle()

    assert window.jobs == [], "a refused bundle still queued something"
    assert warned, "the refusal said nothing"


def test_a_failed_member_does_not_take_its_siblings_with_it(
        window, bench, monkeypatch):
    """Queueing is one action; the exports are not one transaction.

    Each member is an ordinary independent job on the same worker, so a
    failure marks that member and leaves the others alone. This checks the
    queue model rather than a real encode — the publication path itself is
    unchanged and is covered where it already was.
    """
    from flightdvr.jobs import JobStatus

    monkeypatch.setattr(type(window), "selected_clips", lambda self: bench[:1])
    _accept(monkeypatch, ["master", "social", "upload"])
    window._add_bundle()

    failed = window.jobs[1]
    failed.status = JobStatus.FAILED
    failed.message = "no"

    survivors = [j for j in window.jobs if j is not failed]
    assert len(survivors) == 2
    assert all(j.status is JobStatus.PENDING for j in survivors)
    assert len({str(j.out_path) for j in window.jobs}) == 3, (
        "a sibling shared the failed member's output path")


def test_the_bundle_is_remembered_beside_the_preset_not_instead_of_it(
        window, bench, monkeypatch):
    """Reopening a card finds both the radio button and the last bundle.

    Round-tripped through the session document, because that is what the app
    writes — asserting on the panel alone would not notice a value that cannot
    be stored.
    """
    from flightdvr.session import Session, apply_settings, capture_settings

    monkeypatch.setattr(type(window), "selected_clips", lambda self: bench[:1])
    window.export_panel.preset_buttons["edit"].setChecked(True)
    _accept(monkeypatch, ["master", "social"])
    window._add_bundle()

    session = Session()
    capture_settings(session, window.export_panel, bench)
    assert session.export["preset"] == "edit"

    reopened = Session.load(session.save(Path(bench[0].path).parent / "card.flightdvr"))
    window.export_panel.set_bundle([])
    window.export_panel.preset_buttons["master"].setChecked(True)

    assert apply_settings(reopened, window.export_panel)
    assert window.export_panel.bundle() == ["master", "social"]
    assert window.export_panel.preset_key() == "edit", (
        "restoring the bundle moved the single preset")


def test_a_concat_list_that_cannot_be_written_leaves_the_queue_untouched(
        window, bench, monkeypatch, tmp_path):
    """Sol's finding on #82. Preparation used to happen inside the appending loop.

    A joined bundle writes a concat list per member. Building those in the same
    loop that appended meant a second member whose list could not be written
    left the first one already in `self.jobs` — neither the one action the
    dialog promised nor a refusal, and the queue not even redrawn to show what
    had happened. Everything is staged outside the queue now, so a failure
    halfway through has to leave the window exactly as it was.

    The sentinel job is the point: "the queue is empty afterwards" would pass
    against code that cleared it.
    """
    from flightdvr.assembly import Item, resolve
    from flightdvr.jobs import Job
    from flightdvr.presets import ExportSettings

    sentinel = Job([bench[0]], "master", ExportSettings(),
                   tmp_path / "already-there.mp4")
    window.jobs.append(sentinel)
    window.export_panel.set_bundle(["remux"])

    items = [Item(bench[0].fingerprint, bench[0].real_selects[0].sid),
             Item(bench[1].fingerprint, bench[1].real_selects[0].sid)]
    window.export_panel.assembly_panel.show_rows(resolve(items, bench))

    prepared = []

    def failing(clips, work, stem):
        prepared.append(stem)
        if len(prepared) > 1:
            raise OSError("synthetic second concat preparation failure")
        written = tmp_path / f"{stem}.txt"
        written.write_text("first member", encoding="utf-8")
        return written

    redraws = []
    touches = []
    warned = []
    monkeypatch.setattr("flightdvr.ui.write_concat_file", failing)
    monkeypatch.setattr(window, "_rebuild_queue", lambda: redraws.append(1))
    monkeypatch.setattr(window, "_touch_session", lambda: touches.append(1))
    monkeypatch.setattr("flightdvr.ui.QMessageBox.warning",
                        lambda *args, **kw: warned.append(args))

    _accept(monkeypatch, ["master", "upload"])
    window._add_bundle()

    assert len(prepared) == 2, "the second member was never prepared"
    assert window.jobs == [sentinel], (
        "a member of a bundle that could not be prepared reached the queue")
    assert window.export_panel.bundle() == ["remux"], (
        "the remembered selection moved for a bundle that was never queued")
    assert touches == [], "a failed bundle scheduled a session write"
    assert redraws == [], "a failed bundle redrew the queue"
    assert warned, "the failure was silent"
    assert not list(tmp_path.glob("*_joined*.txt")), (
        "the concat list of the abandoned action was left behind")


# -- through real ffmpeg --------------------------------------------------------


@pytest.mark.integration
def test_a_two_member_bundle_really_writes_two_playable_files(tools, clip, tmp_path):
    """The plan has to survive contact with the encoder.

    Everything above is arithmetic about names and sizes. This runs the jobs a
    bundle planned through the same worker the single-preset path uses, and
    looks inside what comes out: two files, both with video, at exactly the two
    paths the confirmation would have shown. A well-formed queue is not a
    working one — eighteen defects in this repository were invisible in the
    arguments.
    """
    from conftest import FPS, probe_output
    from flightdvr.bundle import Piece, plan_bundle
    from flightdvr.jobs import ExportWorker, Job
    from flightdvr.presets import ExportSettings

    settings = ExportSettings()
    source = ClipInfo(path=clip.path, size=clip.path.stat().st_size,
                      modified=datetime.fromtimestamp(clip.path.stat().st_mtime),
                      duration=clip.duration, width=clip.width,
                      height=clip.height, fps=float(FPS))
    source.selects = [Select(1.0, 3.0, "")]

    out_dir = tmp_path / "out"
    members = plan_bundle(
        ["master", "social"], [Piece(p) for p in source.for_export()],
        joined=False, out_dir=out_dir,
        template="{clip}_{preset}", subfolders=False, stamp=None,
        session_name="", settings=settings)

    planned = [(m.key, job) for m in members if m.usable for job in m.jobs]
    assert len(planned) == 2, "the bundle planned something other than two jobs"

    for key, job in planned:
        job.target.parent.mkdir(parents=True, exist_ok=True)
        one = Job(clips=list(job.clips), preset_key=key, settings=settings,
                  out_path=job.target)
        ok, message = ExportWorker(tools, [one], tmp_path)._run_job(0, one)
        assert ok, f"{key} failed: {message}"

    produced = [job.target for _key, job in planned]
    assert len({str(p) for p in produced}) == 2
    for path in produced:
        assert path.exists(), f"{path.name} was never written"
        assert probe_output(tools, path)["has_video"], (
            f"{path.name} was reported done and holds no video")


# -- the slow member of a bundle reaches the corrected join (#86) -------------


def test_a_slow_bundle_member_of_an_assembly_builds_a_silent_join(tmp_path):
    """#86 through the route a bundle actually takes.

    A bundle plans one job per member and hands each to the same worker, so a
    Slow motion member of an Assembly bundle reaches `build_commands` with
    several clips — the joined branch, which is where the unwanted audio came
    from. This asserts the forwarding rather than the fix: the command that
    member would run maps no audio label at all.

    Deliberately no UI: the point is which code path the planned job lands on.
    """
    from flightdvr.bundle import Piece, plan_bundle
    from flightdvr.media import Tools
    from flightdvr.presets import ExportSettings, build_commands

    tools = Tools(Path("ffmpeg"), Path("ffprobe"))
    settings = ExportSettings(keep_audio=True)
    clips = [_clip(tmp_path, "hdz_047.ts", [(0.0, 4.0, "")], sequence_size=2),
             _clip(tmp_path, "hdz_048.ts", [(0.0, 4.0, "")], sequence_size=3)]
    joined_pieces = [Piece(c.for_export()[0]) for c in clips]

    member = plan_bundle(["slowmo"], joined_pieces, joined=True,
                         out_dir=tmp_path / "out",
                         template="{clip}_{preset}", subfolders=False,
                         stamp=None, session_name="", settings=settings)[0]
    assert member.usable, member.problem
    planned = member.jobs[0]
    assert len(planned.clips) == 2, "the member was not planned as a join"

    command = build_commands(
        tools, planned.clips[0], "slowmo", settings, planned.target,
        tmp_path / "work", clips=planned.clips,
    )[0]

    mapped = [command[i + 1] for i, arg in enumerate(command) if arg == "-map"]
    assert mapped == ["[vout]"], f"the bundle's slow join mapped {mapped}"
    assert "[ja]" not in command[command.index("-filter_complex") + 1]
    assert "-an" in command
    assert settings.keep_audio is True, "the route mutated the caller's settings"

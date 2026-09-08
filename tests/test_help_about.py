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

"""The Help/About additions against the source-grounded content policy."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from flightdvr import __version__
from flightdvr import help_content
from flightdvr.format import check_template


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class Settings:
    """The About box needs a value store, not the user's real settings."""

    def __init__(self):
        self.values = {}

    def value(self, key, default=None, type=None):
        value = self.values.get(key, default)
        if type is bool:
            return bool(value)
        return value

    def setValue(self, key, value):
        self.values[key] = value


def _host(qt_app):
    from PySide6.QtWidgets import QMainWindow

    host = QMainWindow()
    host.tools = SimpleNamespace(ffmpeg=Path("C:/ffmpeg/ffmpeg.exe"))
    host.settings_store = Settings()
    return host


def _labels_text(widget) -> str:
    from PySide6.QtWidgets import QLabel

    return "\n".join(label.text() for label in widget.findChildren(QLabel))


def test_naming_examples_use_the_complete_output_path(monkeypatch):
    """Help must not append an extension to a stem by hand.

    The previous naming matrix did exactly that and missed a duplicate preset
    suffix in the real queued filename. Spying on the path builder makes this
    test fail if the content helper returns plausible strings without using the
    complete construction path.
    """
    real = help_content.templated_output_path
    calls = []

    def traced(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(help_content, "templated_output_path", traced)
    examples = help_content.naming_examples()

    assert len(calls) == len(examples) == 7
    assert [example.filename for example in examples] == [
        "hdz_048_master.mp4",
        "hdz_048_1_Launch_upload.mp4",
        "hdz_048_2_Tree-dive!_upload.mp4",
        "hdz_048_3_upload.mp4",
        "hdz_048.mp4",
        "2026-07-04_hdz_048_1_Launch_master.mp4",
        "Practice_hdz_048_Launch_upload.mp4",
    ]
    assert all(kwargs == {"subfolders": False} for _, kwargs in calls)
    assert all(len(args) == 3 for args, _ in calls)


def test_naming_help_lists_the_supported_fields_and_filename_boundary():
    html = help_content.naming_help_html()

    for field in ("date", "session", "clip", "range", "range_number",
                  "preset"):
        assert "{" + field + "}" in html
    assert "filename template, not a folder" in html
    assert "{clipp}" in html
    assert "reserved" in html
    assert "hdz_048_2_Tree-dive!_upload.mp4" in html


@pytest.mark.parametrize("template", [
    "{clipp}",
    "../{clip}",
    r"..\{clip}",
    "{clip}/{range}",
    "{clip",
    "{clip}:bad",
])
def test_invalid_name_templates_are_rejected_before_help_can_promise_them(
    template,
):
    """The reference must describe the actual pre-queue validation boundary."""
    with pytest.raises(ValueError):
        check_template(template)


def test_release_links_do_not_call_a_source_checkout_a_published_build():
    """The unchanged version constant is not proof of a published artifact."""
    links = help_content.release_links(__version__)

    assert not links.is_published_build
    assert links.current_url == help_content.RELEASES_PAGE
    assert "/releases/tag/" not in links.current_url
    assert links.all_releases_url == help_content.ALL_RELEASES_PAGE
    assert links.source_changelog_url == help_content.SOURCE_CHANGELOG_PAGE


def test_release_links_accept_a_separately_verified_exact_published_tag():
    links = help_content.release_links("1.5.0", published_tag="v1.5.0")

    assert links.is_published_build
    assert links.current_url == (
        "https://github.com/nkghxst/flightdvr-studio/releases/tag/v1.5.0"
    )
    assert not help_content.release_links(
        "1.5.0-dev", published_tag="v1.5.0"
    ).is_published_build


def test_shortcuts_dialog_includes_naming_help(qt_app):
    from flightdvr.ui import MainWindow

    host = _host(qt_app)
    dialog = MainWindow._build_shortcuts_dialog(host)
    try:
        text = _labels_text(dialog)
        assert help_content.NAMING_HELP_TITLE in text
        for field in help_content.TEMPLATE_FIELDS:
            assert "{" + field + "}" in text
        assert "filename template, not a folder" in text
        assert "hdz_048_1_Launch_upload.mp4" in text
    finally:
        dialog.deleteLater()
        host.deleteLater()


def test_about_uses_generic_links_and_does_not_open_the_network(qt_app,
                                                                 monkeypatch):
    """Opening About only builds labels; update checking owns the network."""
    import urllib.request

    def network_is_a_test_failure(*_args, **_kwargs):
        raise AssertionError("About opened the network")

    monkeypatch.setattr(urllib.request, "urlopen",
                        network_is_a_test_failure)

    from flightdvr.ui import MainWindow

    host = _host(qt_app)
    box = MainWindow._build_about_box(host)
    try:
        text = _labels_text(box)
        links = help_content.release_links(__version__)
        assert links.current_url in text
        assert links.all_releases_url in text
        assert links.source_changelog_url in text
        assert "/releases/tag/v1.5.0" not in text
        assert "development build" in text
        assert "absolutely no warranty" in text
        assert box.checkBox().text() == "Check for updates"

        release_label = next(
            label for label in box.findChildren(__import__(
                "PySide6.QtWidgets", fromlist=["QLabel"]
            ).QLabel)
            if "Release notes:" in label.text()
        )
        assert release_label.openExternalLinks()
    finally:
        box.deleteLater()
        host.deleteLater()

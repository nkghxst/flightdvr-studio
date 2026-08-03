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

"""The update check, which is the only part of the app that uses the network.

None of these tests make a request. Everything that decides anything is a pure
function, which is the point of splitting it that way.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from flightdvr.updates import (
    CHECK_INTERVAL_HOURS, fetch_latest, is_newer, parse_version, should_check,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 1, 12, 0, 0)


# -- comparing versions --------------------------------------------------------

@pytest.mark.parametrize("latest,current", [
    ("1.2.1", "1.2.0"),
    ("1.3.0", "1.2.9"),
    ("2.0.0", "1.9.9"),
    ("v1.3.0", "1.2.0"),
    ("1.3.0", "v1.2.0"),
    ("1.2.0.1", "1.2.0"),
])
def test_a_newer_release_is_offered(latest, current):
    assert is_newer(latest, current)


@pytest.mark.parametrize("latest,current", [
    ("1.2.0", "1.2.0"),
    ("v1.2.0", "1.2.0"),
    ("1.2.0", "1.3.0"),
    ("1.2.0", "1.2.1"),
    ("1.2", "1.2.0"),
    ("1.2.0", "1.2"),
])
def test_nothing_is_offered_when_it_is_not_newer(latest, current):
    assert not is_newer(latest, current)


def test_ten_is_newer_than_nine():
    """String comparison says the opposite, which is the classic way to get
    this wrong and would have stopped offering updates after 1.9.0."""
    assert is_newer("1.10.0", "1.9.0")
    assert not is_newer("1.9.0", "1.10.0")
    assert is_newer("1.2.10", "1.2.9")


@pytest.mark.parametrize("tag", ["", "latest", "nightly", "v", "  "])
def test_an_unreadable_tag_offers_nothing(tag):
    """Saying nothing is the right failure for something nobody asked for."""
    assert not is_newer(tag, "1.2.0")


def test_an_unreadable_current_version_offers_nothing():
    assert not is_newer("1.3.0", "")


def test_parse_version_takes_the_numbers_out():
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("1.2.3-beta.1") == (1, 2, 3, 1)
    assert parse_version("nothing") == ()


# -- deciding when to look -----------------------------------------------------

def test_the_first_run_checks():
    assert should_check(None, NOW)


def test_a_check_just_now_is_not_repeated():
    assert not should_check(NOW - timedelta(minutes=5), NOW)


def test_a_check_a_day_ago_is_due():
    assert should_check(NOW - timedelta(hours=CHECK_INTERVAL_HOURS), NOW)
    assert not should_check(NOW - timedelta(hours=CHECK_INTERVAL_HOURS - 1), NOW)


def test_a_clock_that_went_backwards_does_not_wedge_it_forever():
    """These goggles have already taught us that clocks lie. A last-checked
    stamp in the future must not mean never checking again."""
    assert should_check(NOW + timedelta(days=400), NOW)


# -- reading the reply ---------------------------------------------------------

class FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_the_tag_and_page_are_taken_from_the_reply(monkeypatch):
    from flightdvr import updates
    monkeypatch.setattr(updates.urllib.request, "urlopen",
                        lambda *a, **k: FakeResponse(
                            {"tag_name": "v1.3.0",
                             "html_url": "https://example.invalid/v1.3.0"}))
    assert fetch_latest() == ("v1.3.0", "https://example.invalid/v1.3.0")


def test_a_reply_with_no_tag_is_an_error(monkeypatch):
    from flightdvr import updates
    monkeypatch.setattr(updates.urllib.request, "urlopen",
                        lambda *a, **k: FakeResponse({"html_url": "x"}))
    with pytest.raises(ValueError):
        fetch_latest()


def test_the_request_identifies_itself_and_asks_for_json(monkeypatch):
    """A request with no user agent is rejected by the GitHub API, and saying
    which version is asking is the least a well-behaved client can do."""
    from flightdvr import updates
    seen = {}

    def capture(request, timeout=None):
        seen["headers"] = request.headers
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return FakeResponse({"tag_name": "v1.0.0", "html_url": "x"})

    monkeypatch.setattr(updates.urllib.request, "urlopen", capture)
    fetch_latest(version="1.2.0")

    agent = seen["headers"].get("User-agent", "")
    assert "FlightDVRStudio" in agent and "1.2.0" in agent
    assert "github.com" in seen["url"]
    assert seen["timeout"] == updates.TIMEOUT_SECONDS


# -- the promise ---------------------------------------------------------------

def test_only_this_project_is_ever_contacted():
    """One host, one repository, over TLS. If a second endpoint ever appears it
    should be a deliberate decision with its own line in the README, not a
    quiet addition to a module nobody re-reads."""
    from urllib.parse import urlparse

    from flightdvr import updates
    for url in (updates.RELEASES_URL, updates.RELEASES_PAGE):
        parts = urlparse(url)
        assert parts.scheme == "https", url
        assert parts.hostname in {"api.github.com", "github.com"}, url
        assert "nkghxst/flightdvr-studio" in parts.path, url


def test_the_readme_no_longer_claims_there_is_no_network_access():
    """It used to say "no network access of any kind", unqualified. Shipping a
    checker without correcting that would be the expensive kind of quiet."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "no account, telemetry or network access of any kind" not in readme
    assert "update" in readme.lower()


def test_nothing_is_downloaded_or_run():
    """Check and link only. The builds are unsigned, so fetching and running an
    installer would be an arbitrary-code-execution path."""
    source = (ROOT / "flightdvr" / "updates.py").read_text(encoding="utf-8")
    for forbidden in ("urlretrieve", "subprocess", "os.system", "startfile"):
        assert forbidden not in source, f"updates.py should not use {forbidden}"

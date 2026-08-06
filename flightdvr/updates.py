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

"""Asking GitHub whether a newer release exists.

This is the only part of the application that touches the network, and it does
so on terms worth stating plainly, because the README used to promise it never
would:

* One HTTPS request, at most once a day, to the public releases API of this
  project's own repository. Nothing else is contacted, ever.
* Nothing is sent beyond what an HTTP request unavoidably reveals — an IP
  address and the user agent below. No account, no identifier, no information
  about the machine, the footage, or how the app is used.
* Nothing is downloaded or installed. A newer version produces a link, and the
  person decides.
* It can be turned off, and then no request is made at all.

That last point is why failures here are silent. Somebody flying without an
internet connection must never see an error about a check they did not ask for.

Installing an update from inside the app is deliberately not done. The builds
are unsigned, so a downloader that fetched and ran an installer would be an
arbitrary-code-execution path the moment anything upstream were compromised,
and it is a shape antivirus flags on sight. A link costs almost nothing and
carries none of that.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QThread, Signal

PROJECT_PAGE = "https://github.com/nkghxst/flightdvr-studio"
RELEASES_URL = (
    "https://api.github.com/repos/nkghxst/flightdvr-studio/releases/latest"
)
RELEASES_PAGE = f"{PROJECT_PAGE}/releases/latest"

CHECK_INTERVAL_HOURS = 24
TIMEOUT_SECONDS = 10

# Anything that is not a run of digits separates one part of a version from the
# next, so "v1.2.0", "1.2.0" and "1.2.0-beta.1" all reduce to their numbers.
_NUMBERS = re.compile(r"\d+")


def parse_version(text: str) -> tuple[int, ...]:
    """The numeric parts of a version string, most significant first.

    Returns an empty tuple for anything with no digits in it at all, which the
    comparison below treats as "cannot tell" rather than "older".
    """
    return tuple(int(part) for part in _NUMBERS.findall(text or ""))


def is_newer(latest: str, current: str) -> bool:
    """Whether `latest` is a release the person running `current` does not have.

    Compared part by part as numbers, so 1.10.0 is correctly newer than 1.9.0 —
    comparing the strings would say the opposite, which is the classic way to
    get this wrong. A version with fewer parts is padded with zeroes, so 1.3
    and 1.3.0 are the same release.

    Anything unparseable returns False. Saying nothing is the right failure for
    a feature nobody asked for.
    """
    new, old = parse_version(latest), parse_version(current)
    if not new or not old:
        return False
    width = max(len(new), len(old))
    new += (0,) * (width - len(new))
    old += (0,) * (width - len(old))
    return new > old


def should_check(last_check: datetime | None, now: datetime) -> bool:
    """Whether enough time has passed since the last look.

    A clock that has gone backwards — a corrected system time, a machine that
    booted without a battery, which these goggles have taught us to expect —
    reads as "due" rather than never being due again.
    """
    if last_check is None:
        return True
    if last_check > now:
        return True
    return now - last_check >= timedelta(hours=CHECK_INTERVAL_HOURS)


def fetch_latest(url: str = RELEASES_URL, timeout: float = TIMEOUT_SECONDS,
                 version: str = "") -> tuple[str, str]:
    """The newest published release as (tag, page url). Raises on any problem.

    Drafts and prereleases are excluded by the endpoint itself: GitHub's
    "latest" is the newest published, non-prerelease release, which is exactly
    what should be offered.
    """
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"FlightDVRStudio/{version or 'dev'}",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    tag = str(payload.get("tag_name") or "")
    page = str(payload.get("html_url") or RELEASES_PAGE)
    if not tag:
        raise ValueError("the releases API returned no tag")
    return tag, page


class UpdateCheck(QThread):
    """One look at the releases API, off the UI thread.

    `failed` exists for the log and for tests. Nothing in the window is
    connected to it: a person with no internet is not having a problem.
    """

    found = Signal(str, str)      # version without the leading v, page url
    failed = Signal(str)

    def __init__(self, current_version: str, parent=None):
        super().__init__(parent)
        self.current_version = current_version

    def run(self) -> None:  # noqa: D102  (QThread entry point)
        try:
            tag, page = fetch_latest(version=self.current_version)
        except (urllib.error.URLError, OSError, ValueError,
                json.JSONDecodeError, TimeoutError) as exc:
            self.failed.emit(str(exc))
            return
        if is_newer(tag, self.current_version):
            self.found.emit(tag.lstrip("vV"), page)

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

"""Is GitHub Actions actually working again?

    python tools/check_ci.py                # answer once
    python tools/check_ci.py --watch        # keep checking until it is back
    python tools/check_ci.py --dispatch     # prove it by running the workflow

Written during the outage of 6 August 2026, which is worth describing because
it is why this asks the question the way it does.

The status page said "we have started implementing a fix" for hours while jobs
still sat queued and never acquired a runner. It also said webhooks were
throttled to fifteen percent, which meant several pushes produced **no workflow
run at all** — nothing failed, nothing appeared, and the only sign was an
absence.

So this weights what the repository is actually doing above what the status
page claims. A job that starts is proof. A status page saying "operational"
while nothing has run for three hours is a hypothesis.

Exit codes, so it can be used in a script: 0 when Actions looks usable, 1 when
it does not, 2 when the check itself could not run.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

STATUS_URL = "https://www.githubstatus.com/api/v2/summary.json"
TIMEOUT = 15

# A job that has sat this long without starting is queued, not merely slow.
# Normal pickup on this repository is well under a minute.
STUCK_MINUTES = 10

# How far back to look for evidence that something actually ran.
RECENT_HOURS = 2


def _run(args: list[str]) -> str | None:
    """A gh call, or None if gh is missing, unauthenticated, or erroring.

    The Actions API returned errors during the same outage, so failing to
    answer is itself a possible answer and must not be a traceback.
    """
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=45)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def status_page() -> dict:
    """What GitHub says about itself. Taken as a claim, not as evidence."""
    try:
        with urllib.request.urlopen(STATUS_URL, timeout=TIMEOUT) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return {"reachable": False}

    actions = next(
        (c for c in raw.get("components", [])
         if c.get("name", "").strip().lower() == "actions"),
        {},
    )
    incidents = [
        i for i in raw.get("incidents", [])
        if any(c.get("name", "").strip().lower() == "actions"
               for c in i.get("components", []))
    ]
    latest = incidents[0] if incidents else None
    updates = (latest or {}).get("incident_updates") or []
    return {
        "reachable": True,
        "component": actions.get("status", "unknown"),
        "incident": (latest or {}).get("name"),
        "incident_status": (latest or {}).get("status"),
        "update": (updates[0].get("body") if updates else "") or "",
        "updated": (updates[0].get("created_at") if updates else "") or "",
    }


def _age_minutes(stamp: str) -> float:
    try:
        started = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (datetime.now(timezone.utc) - started).total_seconds() / 60.0


def runs(limit: int = 25) -> list[dict] | None:
    """This repository's recent workflow runs, newest first."""
    out = _run([
        "gh", "run", "list", "--limit", str(limit),
        "--json", "status,conclusion,createdAt,updatedAt,displayTitle,headBranch",
    ])
    if out is None:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def verdict(page: dict, recent: list[dict] | None) -> tuple[bool, list[str]]:
    """Combine the claim and the evidence. The evidence wins."""
    notes: list[str] = []

    if recent is None:
        notes.append("could not reach the Actions API through gh — which was "
                     "itself a symptom during the last outage")
        return False, notes

    stuck = [r for r in recent
             if r.get("status") == "queued"
             and _age_minutes(r.get("createdAt", "")) > STUCK_MINUTES]
    if stuck:
        # A run that has sat queued for hours only proves something if nothing
        # newer got picked up. The August outage ended with a handful of
        # poisoned jobs that no runner would ever claim, sitting behind runs
        # that were starting and passing normally — and this tool called that
        # "not usable" while three runs went green underneath it.
        oldest_stuck = min(_age_minutes(r.get("createdAt", "")) for r in stuck)
        moved_since = [
            r for r in recent
            if r.get("status") != "queued"
            and _age_minutes(r.get("createdAt", "")) < oldest_stuck
        ]
        worst = max(_age_minutes(r.get("createdAt", "")) for r in stuck)
        if not moved_since:
            notes.append(
                f"{len(stuck)} run(s) queued without starting, the oldest for "
                f"{worst / 60:.1f} hours — no runner has picked them up"
            )
            return False, notes
        notes.append(
            f"{len(stuck)} run(s) stuck queued for up to {worst / 60:.1f} "
            f"hours, but {len(moved_since)} newer run(s) were picked up — "
            "treating the stuck ones as leftovers rather than evidence"
        )

    cutoff = datetime.now(timezone.utc) - timedelta(hours=RECENT_HOURS)
    finished = [
        r for r in recent
        if r.get("status") == "completed"
        and r.get("updatedAt", "") and
        datetime.fromisoformat(r["updatedAt"].replace("Z", "+00:00")) > cutoff
    ]
    running = [r for r in recent if r.get("status") == "in_progress"]

    if running:
        notes.append(f"{len(running)} run(s) actually executing right now")
        return True, notes
    if finished:
        newest = max(finished, key=lambda r: r["updatedAt"])
        notes.append(
            f"a run completed {_age_minutes(newest['updatedAt']):.0f} minutes "
            f"ago ({newest.get('conclusion')})"
        )
        return True, notes

    # Nothing queued and nothing recent is genuinely ambiguous: it looks the
    # same whether things are healthy and idle, or whether webhooks are being
    # dropped so nothing is being created in the first place.
    if page.get("reachable") and page.get("component") == "operational":
        notes.append("nothing queued, nothing run in the last "
                     f"{RECENT_HOURS} hours, and the status page is clear — "
                     "probably fine, but unproven until something runs")
        return True, notes

    notes.append(f"nothing has run in {RECENT_HOURS} hours and the status page "
                 "is not clear")
    return False, notes


def report(dispatch: bool = False) -> bool:
    page = status_page()
    recent = runs()
    ok, notes = verdict(page, recent)

    print("GitHub status page")
    if not page.get("reachable"):
        print("  unreachable")
    else:
        print(f"  Actions component : {page['component']}")
        if page.get("incident"):
            print(f"  incident          : {page['incident']} "
                  f"({page['incident_status']})")
            if page.get("updated"):
                print(f"  last update       : {page['updated']}")
            # The status feed is HTML, and <br /> in a terminal is just noise.
            body = re.sub(r"<[^>]+>", " ", page.get("update") or "")
            body = " ".join(html.unescape(body).split())
            if body:
                print(f"  {body[:220]}")
        else:
            print("  no open Actions incident")

    print("\nThis repository")
    if recent is None:
        print("  could not read the runs")
    elif not recent:
        print("  no runs at all")
    else:
        for entry in recent[:4]:
            state = entry.get("status")
            if state == "completed":
                state = entry.get("conclusion") or "completed"
            age = _age_minutes(entry.get("createdAt", ""))
            print(f"  {state:12} {age / 60:5.1f}h  "
                  f"{entry.get('displayTitle', '')[:44]}")

    print("\nVerdict")
    for note in notes:
        print(f"  {note}")
    print(f"  -> Actions {'looks usable' if ok else 'is NOT usable'}")

    if ok and dispatch:
        print("\nDispatching a run to prove it…")
        if _run(["gh", "workflow", "run", "build.yml"]) is None:
            print("  dispatch failed")
            return False
        print("  dispatched — check with: gh run list --limit 3")

    if not ok:
        print("\n  Nothing to fix on this side. Local tests are the fallback:")
        print("    python -m pytest")
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--watch", action="store_true",
                        help="keep checking until it is back")
    parser.add_argument("--every", type=int, default=300,
                        help="seconds between checks when watching (default 300)")
    parser.add_argument("--for", dest="limit_hours", type=float, default=6.0,
                        help="give up after this many hours (default 6)")
    parser.add_argument("--dispatch", action="store_true",
                        help="run the workflow once Actions looks usable")
    args = parser.parse_args(argv)

    if not args.watch:
        return 0 if report(dispatch=args.dispatch) else 1

    deadline = time.monotonic() + args.limit_hours * 3600
    while True:
        print(f"\n{'=' * 60}\n{datetime.now():%H:%M:%S}")
        if report(dispatch=args.dispatch):
            print("\nBack. Stopping.")
            return 0
        if time.monotonic() + args.every > deadline:
            print(f"\nGave up after {args.limit_hours} hours.")
            return 1
        time.sleep(args.every)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)

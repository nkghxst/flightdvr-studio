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

"""Prove two focused regressions fail when the guarded behavior is removed."""
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from .model import AudioPlan
from .stream import Levels


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
    from test_audio_proof import StreamTests, TimelineTests
    cases = (
        (Levels, "pair", lambda self, include_dvr=True:
         (self.music / max(1, self.music + self.dvr),
          self.dvr / max(1, self.music + self.dvr)),
         StreamTests("test_all_silent_dvr_keeps_music_at_requested_gain")),
        (AudioPlan, "music_at", lambda self, position: (0, 1.0),
         TimelineTests("test_nonzero_seek_keeps_absolute_music_loop_and_fade_phase")),
    )
    for owner, method, mutant, case in cases:
        output = io.StringIO()
        with patch.object(owner, method, mutant):
            result = unittest.TextTestRunner(stream=output).run(case)
        if len(result.failures) != 1 or result.errors:
            raise AssertionError(output.getvalue())
        print(f"EXPECTED FAILURE: {case.id()}\n{output.getvalue()}")
    print("Both injected regressions were detected; source files were not changed.")


if __name__ == "__main__":
    main()

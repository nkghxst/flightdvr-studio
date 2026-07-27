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

"""Entry point for the packaged build.

PyInstaller needs a plain script rather than a package's __main__, because
relative imports do not resolve when a module is used as the entry script.
"""

import multiprocessing
import sys

from flightdvr.ui import launch

if __name__ == "__main__":
    # Without this, a frozen build can re-launch itself instead of spawning a
    # worker if anything ever reaches for multiprocessing.
    multiprocessing.freeze_support()
    sys.exit(launch())

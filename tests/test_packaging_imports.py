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

"""Nothing the app imports may be left out of the package.

The spec trims Qt by excluding modules the app "never touches". 2.0.0 shipped
with QtMultimedia still on that list after the audio output started using it:
the import sits inside a function, so the packaged app started, passed
--check and every package gate, and failed only when someone pressed Listen.
Text only — no product or Qt import.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "flightdvr_studio.spec"


def excluded_qt() -> set[str]:
    tree = ast.parse(SPEC.read_text(encoding="utf-8"))
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "EXCLUDED_QT"):
            return set(ast.literal_eval(node.value))
    raise AssertionError("EXCLUDED_QT not found in the spec")


def pyside_modules_imported(source: str) -> set[str]:
    """Every `PySide6.X` the source imports, wherever the import sits."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            parts = name.split(".")
            if parts[0] == "PySide6" and len(parts) > 1:
                found.add(parts[1])
    return found


def test_no_module_the_app_imports_is_excluded_from_the_package():
    excluded = excluded_qt()
    offending = {}
    for path in sorted((ROOT / "flightdvr").rglob("*.py")):
        taken = pyside_modules_imported(path.read_text(encoding="utf-8")) & excluded
        if taken:
            offending[path.name] = sorted(taken)
    assert offending == {}, f"excluded from the package but imported: {offending}"


def test_the_scan_sees_an_import_inside_a_function():
    """The case that shipped: a lazy import, not a module-level one."""
    source = ("def sink():\n"
              "    from PySide6.QtMultimedia import QAudioSink\n"
              "    import PySide6.QtCore\n")
    assert pyside_modules_imported(source) == {"QtMultimedia", "QtCore"}

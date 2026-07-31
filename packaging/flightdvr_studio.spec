# PyInstaller spec for FlightDVR Studio. Builds on Windows, Linux and macOS.
#
# Build with:
#     python -m PyInstaller packaging/flightdvr_studio.spec --noconfirm
#
# ffmpeg bundling
# ---------------
# The Windows build bundles ffmpeg and ffprobe so the app works on a machine
# that has never had them installed, and media.py looks inside the bundle
# before it looks at PATH. Linux and macOS builds do not bundle by default:
# both have a package manager that supplies a maintained ffmpeg, and shipping
# a second GPL binary means shipping a second corresponding-source offer.
#
# Set FFMPEG_DIR to bundle anyway. On Windows it defaults to C:\ffmpeg\bin and
# is required; elsewhere it is only honoured when you set it explicitly.

import os
import re
import sys
from pathlib import Path

ROOT = Path(os.getcwd())
PACKAGING = ROOT / "packaging"

WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"

VERSION = re.search(
    r'__version__\s*=\s*"([^"]+)"',
    (ROOT / "flightdvr" / "__init__.py").read_text(encoding="utf-8"),
).group(1)

# -- ffmpeg -------------------------------------------------------------------

TOOL_NAMES = ("ffmpeg.exe", "ffprobe.exe") if WINDOWS else ("ffmpeg", "ffprobe")

ffmpeg_dir = os.environ.get("FFMPEG_DIR") or (r"C:\ffmpeg\bin" if WINDOWS else "")
# Required on Windows; opt-in elsewhere, so an unset FFMPEG_DIR is not an error.
ffmpeg_required = WINDOWS or bool(os.environ.get("FFMPEG_DIR"))

ffmpeg_files = []
if ffmpeg_dir:
    for tool in TOOL_NAMES:
        candidate = Path(ffmpeg_dir) / tool
        if candidate.exists():
            ffmpeg_files.append((str(candidate), "ffmpeg"))
        elif ffmpeg_required:
            raise SystemExit(
                f"{tool} not found in {ffmpeg_dir}. Set FFMPEG_DIR to a folder "
                "containing a matching ffmpeg/ffprobe pair."
            )

print(f"[spec] bundling {len(ffmpeg_files)} ffmpeg binaries"
      + (f" from {ffmpeg_dir}" if ffmpeg_files else " (using the system copy)"))

# -- data files ---------------------------------------------------------------

# The window icon is loaded from inside the package at runtime.
data_files = [(str(ROOT / "flightdvr" / "resources" / "icon.ico"),
               "flightdvr/resources")]

# Licences travel with the binary. LICENSE is our own GPL v3; the LGPL text is
# there because Qt reaches us under it and section 4(b) requires a copy of that
# licence to accompany a combined work — it is not optional and was missing
# from every build format before 1.1.1.
for name in ("LICENSE", "LICENSE.LGPL-3.0.txt", "THIRD-PARTY-NOTICES.md"):
    if (ROOT / name).exists():
        data_files.append((str(ROOT / name), "."))

# -- trimming Qt --------------------------------------------------------------

# Qt modules this app never touches. Excluding them roughly halves the build.
EXCLUDED_QT = [
    "QtWebEngineCore", "QtWebEngineWidgets", "QtWebEngineQuick", "QtWebChannel",
    "QtQml", "QtQuick", "QtQuick3D", "QtQuickWidgets", "QtQuickControls2",
    "Qt3DCore", "Qt3DRender", "Qt3DInput", "Qt3DLogic", "Qt3DAnimation",
    "Qt3DExtras", "QtCharts", "QtDataVisualization", "QtGraphs",
    "QtMultimedia", "QtMultimediaWidgets", "QtPdf", "QtPdfWidgets",
    "QtSql", "QtTest", "QtDesigner", "QtHelp", "QtUiTools",
    "QtBluetooth", "QtNfc", "QtPositioning", "QtLocation", "QtSerialPort",
    "QtSensors", "QtSpatialAudio", "QtTextToSpeech", "QtWebSockets",
    "QtRemoteObjects", "QtScxml", "QtStateMachine", "QtNetworkAuth",
    "QtHttpServer", "QtSerialBus", "QtOpcUa",
]

excludes = [f"PySide6.{name}" for name in EXCLUDED_QT]
excludes += ["tkinter", "unittest", "pydoc_data", "numpy", "matplotlib",
             "PIL", "scipy", "pandas", "pytest", "setuptools", "pip"]


a = Analysis(
    [str(PACKAGING / "app_entry.py")],
    pathex=[str(ROOT)],
    binaries=ffmpeg_files,
    datas=data_files,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

# The PySide6 hook copies Qt's libraries regardless of the module excludes
# above, so the QML/Quick/Pdf stack and the software OpenGL fallback have to be
# dropped from the collected files directly. This app is Qt Widgets only and
# renders through the raster engine, so none of it is reachable.
#
# The same library is named three ways: Qt6Quick.dll on Windows,
# libQt6Quick.so.6 on Linux, and QtQuick.framework/Versions/A/QtQuick on macOS.
DROP_FILENAMES = {"opengl32sw.dll"}
DROP_PREFIXES = (
    "qt6quick", "qt6qml", "qt6pdf",
    "qtquick", "qtqml", "qtpdf",
)


def _drops(segment: str) -> bool:
    stem = segment[3:] if segment.startswith("lib") else segment
    return stem.startswith(DROP_PREFIXES)


def _unwanted(entry):
    dest = str(entry[0]).replace("\\", "/").lower()
    if "translations/" in dest:          # 6.4 MB of Qt UI translations
        return True
    segments = dest.split("/")
    if segments[-1] in DROP_FILENAMES:
        return True
    # Matching every segment catches macOS frameworks, where the module name is
    # a directory rather than the file at the end of the path.
    return any(_drops(segment) for segment in segments)


_before = len(a.binaries) + len(a.datas)
a.binaries = [e for e in a.binaries if not _unwanted(e)]
a.datas = [e for e in a.datas if not _unwanted(e)]
print(f"[spec] dropped {_before - len(a.binaries) - len(a.datas)} unused Qt files")

# -- assembling ---------------------------------------------------------------

if WINDOWS:
    exe_icon = str(PACKAGING / "flightdvr.ico")
elif MACOS and (PACKAGING / "flightdvr.icns").exists():
    exe_icon = str(PACKAGING / "flightdvr.icns")
else:
    exe_icon = None                      # Linux takes its icon from the .desktop

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FlightDVRStudio",
    debug=False,
    strip=False,
    upx=False,
    console=False,               # GUI app: no console window
    icon=exe_icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="FlightDVRStudio",
)

if MACOS:
    app = BUNDLE(
        coll,
        name="FlightDVR Studio.app",
        icon=exe_icon,
        bundle_identifier="uk.co.nkghxst.flightdvrstudio",
        version=VERSION,
        info_plist={
            "CFBundleName": "FlightDVR Studio",
            "CFBundleDisplayName": "FlightDVR Studio",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            # Qt 6.5+ needs 11.0, and that is also the oldest macOS still
            # getting security updates.
            "LSMinimumSystemVersion": "11.0",
            "NSHumanReadableCopyright":
                "Copyright (C) 2026 Isadu Nkemi. Licensed under the GNU "
                "General Public License version 3 or later.",
        },
    )

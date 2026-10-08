# PyInstaller spec for FlightDVR Studio. Builds on Windows, Linux and macOS.
#
# Build with:
#     python -m PyInstaller packaging/flightdvr_studio.spec --noconfirm
#
# ffmpeg bundling
# ---------------
# The Windows and Linux builds bundle ffmpeg and ffprobe so the app works on a
# machine that has never had them installed, and media.py looks inside the
# bundle before it looks at PATH. The macOS build does not bundle by default:
# Homebrew supplies a maintained ffmpeg there.
#
# On Windows FFMPEG_DIR defaults to C:\ffmpeg\bin and is required. On Linux it
# is required and must hold exactly the pair ffmpeg-build-linux.json pins,
# which packaging/fetch-ffmpeg.sh downloads and verifies; the folder is checked
# again here so nothing else can be bundled under the notices' attribution. On
# macOS it is only honoured when you set it explicitly.

import os
import re
import sys
from pathlib import Path

ROOT = Path(os.getcwd())
PACKAGING = ROOT / "packaging"

WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"
LINUX = sys.platform.startswith("linux")

VERSION = re.search(
    r'__version__\s*=\s*"([^"]+)"',
    (ROOT / "flightdvr" / "__init__.py").read_text(encoding="utf-8"),
).group(1)

# -- ffmpeg -------------------------------------------------------------------

TOOL_NAMES = ("ffmpeg.exe", "ffprobe.exe") if WINDOWS else ("ffmpeg", "ffprobe")

ffmpeg_dir = os.environ.get("FFMPEG_DIR") or (r"C:\ffmpeg\bin" if WINDOWS else "")
# Required on Windows and Linux; opt-in on macOS, where an unset FFMPEG_DIR is
# not an error.
ffmpeg_required = WINDOWS or LINUX or bool(os.environ.get("FFMPEG_DIR"))

if LINUX:
    if not ffmpeg_dir:
        raise SystemExit(
            "The Linux build bundles the pinned ffmpeg/ffprobe pair. Set "
            "FFMPEG_DIR to the folder packaging/fetch-ffmpeg.sh prints."
        )
    sys.path.insert(0, str(PACKAGING))
    from verify_ffmpeg_linux import PinError, check_dir
    try:
        check_dir(Path(ffmpeg_dir))
    except PinError as exc:
        raise SystemExit(f"Refusing to bundle {ffmpeg_dir} ({exc.reason}): {exc}")

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
for name in ("LICENSE", "LICENSE.LGPL-2.1.txt", "LICENSE.LGPL-3.0.txt", "THIRD-PARTY-NOTICES.md"):
    if (ROOT / name).exists():
        data_files.append((str(ROOT / name), "."))

# -- trimming Qt --------------------------------------------------------------

# Qt modules this app never touches. Excluding them roughly halves the build.
#
# QtMultimedia is not on this list. The audio output (`flightdvr/audio_device.py`)
# hands already-decoded PCM to `QAudioSink`; decoding stays with ffmpeg. It was
# excluded until 2.0.0 shipped that way: the import is inside a function, so
# the package started and passed every check, and Listen failed for the person
# using it. `tests/test_packaging_imports.py` now holds this list against what
# the app imports, and `--check` reports the audio output module.
EXCLUDED_QT = [
    "QtWebEngineCore", "QtWebEngineWidgets", "QtWebEngineQuick", "QtWebChannel",
    "QtQml", "QtQuick", "QtQuick3D", "QtQuickWidgets", "QtQuickControls2",
    "Qt3DCore", "Qt3DRender", "Qt3DInput", "Qt3DLogic", "Qt3DAnimation",
    "Qt3DExtras", "QtCharts", "QtDataVisualization", "QtGraphs",
    "QtMultimediaWidgets", "QtPdf", "QtPdfWidgets",
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

# The pinned pip receipt is generated in CI before Analysis. Match the wheel
# RECORD bytes to the exact selected Analysis entries, then carry the small
# source reference with the package. This records inputs, not source clearance.
sys.path.insert(0, str(PACKAGING))
from collect_qt_multimedia_sources import write_spec_receipt

inputs_path = ROOT / "build" / "qt-inputs.json"
collection_path = ROOT / "build" / "qt-collection.json"
if os.environ.get("QT_LAYOUT_DIAGNOSTIC") == "1":
    from hashlib import sha256
    from importlib import metadata

    for entry in a.binaries + a.datas:
        if "QtMultimedia.framework" in str(entry[0]) or "QtMultimedia.framework" in str(entry[1]):
            print(f"QT_DIAG_TOC {entry!r}", flush=True)
    dist = metadata.distribution("PySide6-Addons")
    print(f"QT_DIAG_ROOT {dist.locate_file('')}", flush=True)
    candidates = {str(item) for item in dist.files or []
                  if "QtMultimedia.framework" in str(item)}
    candidates.update({"PySide6/Qt/lib/QtMultimedia.framework/QtMultimedia",
                       "PySide6/Qt/lib/QtMultimedia.framework/Versions/Current",
                       "PySide6/Qt/lib/QtMultimedia.framework/Versions/A/QtMultimedia"})
    for name in sorted(candidates):
        path = Path(dist.locate_file(name))
        resolved = path.resolve()
        hash_value = sha256(resolved.read_bytes()).hexdigest() if resolved.is_file() else "not_file"
        print(f"QT_DIAG_PATH {name} exists={path.exists()} symlink={path.is_symlink()} "
              f"resolved={resolved} sha256={hash_value}", flush=True)
write_spec_receipt(a.binaries + a.datas, inputs_path, collection_path)
a.datas.append(("qt-multimedia-source-reference.json", str(collection_path), "DATA"))
a.datas.append(("qt-multimedia-sources.json", str(PACKAGING / "qt-multimedia-sources.json"), "DATA"))

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

# PyInstaller spec for FlightDVR Studio.
#
# Build with:
#     python -m PyInstaller packaging/flightdvr_studio.spec --noconfirm
#
# ffmpeg and ffprobe are bundled so the app works on a machine that has never
# had them installed. media.py looks inside the bundle before it looks at PATH.

import os
from pathlib import Path

ROOT = Path(os.getcwd())
PACKAGING = ROOT / "packaging"

# Where to take ffmpeg from. Override with FFMPEG_DIR to build elsewhere.
FFMPEG_DIR = Path(os.environ.get("FFMPEG_DIR", r"C:\ffmpeg\bin"))

ffmpeg_files = []
for tool in ("ffmpeg.exe", "ffprobe.exe"):
    candidate = FFMPEG_DIR / tool
    if candidate.exists():
        ffmpeg_files.append((str(candidate), "ffmpeg"))
    else:
        raise SystemExit(
            f"{tool} not found in {FFMPEG_DIR}. Set FFMPEG_DIR to a folder "
            "containing a matching ffmpeg/ffprobe pair."
        )

# The window icon is loaded from inside the package at runtime.
icon_files = [(str(ROOT / "flightdvr" / "resources" / "icon.ico"),
               "flightdvr/resources")]

# The GPL requires the licence to travel with the binary, so it goes in the
# bundle as well as being installed by the setup program.
for name in ("LICENSE", "THIRD-PARTY-NOTICES.md"):
    if (ROOT / name).exists():
        icon_files.append((str(ROOT / name), "."))

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
    datas=icon_files,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

# The PySide6 hook copies Qt's DLLs regardless of the module excludes above,
# so the QML/Quick/Pdf stack and the software OpenGL fallback have to be dropped
# from the collected files directly. This app is Qt Widgets only and renders
# through the raster engine, so none of it is reachable.
DROP_FILENAMES = {"opengl32sw.dll"}
DROP_PREFIXES = (
    "qt6quick", "qt6qml", "qt6pdf",
    "qtquick", "qtqml", "qtpdf",
)


def _unwanted(entry):
    dest = str(entry[0]).replace("\\", "/").lower()
    if "translations/" in dest:          # 6.4 MB of Qt UI translations
        return True
    name = dest.rsplit("/", 1)[-1]
    return name in DROP_FILENAMES or name.startswith(DROP_PREFIXES)


_before = len(a.binaries) + len(a.datas)
a.binaries = [e for e in a.binaries if not _unwanted(e)]
a.datas = [e for e in a.datas if not _unwanted(e)]
print(f"[spec] dropped {_before - len(a.binaries) - len(a.datas)} unused Qt files")

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
    icon=str(PACKAGING / "flightdvr.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="FlightDVRStudio",
)

; Inno Setup script for flightdvr.
;
; Build the PyInstaller bundle first, then compile this:
;     python -m PyInstaller packaging/flightdvr.spec --noconfirm
;     "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" packaging\installer.iss
;
; Installs per-user by default so it needs no administrator rights, which
; matters on a laptop you do not own the admin account for.

#define AppName        "FlightDVR Studio"
#define AppVersion     "1.1.0"
#define AppPublisher   "Isadu Nkemi"
#define AppExeName     "FlightDVRStudio.exe"

[Setup]
AppId={{2F5D8A31-C47B-4E90-A6D2-71B3E8C40F19}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppComments=Browse, trim and convert HDZero goggle DVR footage
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no
OutputDir=..\dist\installer
OutputBaseFilename=FlightDVRStudio-{#AppVersion}-Setup
SetupIconFile=flightdvr.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName} {#AppVersion}
WizardStyle=modern
LicenseFile=..\LICENSE

; lzma2/ultra64 with several block threads exhausted memory on a payload this
; size. /max uses a 32 MB dictionary and one block thread, which compresses
; almost as well and completes reliably.
Compression=lzma2/max
SolidCompression=yes
LZMANumBlockThreads=1

; No admin prompt: installs under the user's own Programs folder.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
; Licence and attribution travel with the install.
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD-PARTY-NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "ffmpeg-configuration.txt"; DestDir: "{app}"; Flags: ignoreversion
; The whole PyInstaller output, including the bundled ffmpeg and ffprobe.
Source: "..\dist\FlightDVRStudio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Thumbnail cache the app builds at runtime; the settings in the registry are
; left alone so a reinstall keeps your preferences.
Type: filesandordirs; Name: "{%USERPROFILE}\.flightdvr"

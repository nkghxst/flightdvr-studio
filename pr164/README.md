# PR #164 Stage 2: native screenshots

This branch exists only to host images for the PR #164 comment. It holds no code and is not for merging.

## What was captured

- **Code:** the reviewed candidate `17bd03de13e8c8c77c1528ff403ad2d7fb8c632f`. Its `flightdvr/` code is identical to main `581f86b`.
- **How it ran:** from source, natively on the Windows platform plugin. It was not run offscreen. PySide6 6.11.1; the packaged build uses 6.11.2.
- **Machine:** Windows 11. The screen is 1493×933 logical pixels at a device pixel ratio of 1.5.
- **Window size:** 1400×893.
- **How the images were made:** each one is `QWidget.grab()` of the shown window, or of the open menu.

## Isolated storage

- Settings went to a temporary INI file, not the registry.
- HOME, USERPROFILE, APPDATA and LOCALAPPDATA all pointed to a temporary folder.
- Nothing touched the real user's settings. An export of the registry and a listing of `~/.flightdvr` were byte-identical before and after.

## Media and devices

- **Clips and music track:** generated with ffmpeg (test patterns plus tones). There are no real flights.
- **Output devices:** the machine's real list, which contains one entry, `Speakers (Realtek(R) Audio)`.
- **Playback:** Play was never pressed and nothing was played.
- **Not covered:** this is not listening, device or installer acceptance.

## Files

| File | What it shows |
|---|---|
| `01-classic-collapsed-music-open.png` | Classic layout with the browser list Collapsed and Music open, with a track chosen |
| `02-music-focus.png` | Music Focus: the output-time picture and music lanes with fades, music time, sound mode and the passage |
| `03-sound-on-state-line.png` | Sound turned on beside Play, with its state line |
| `04-audio-menu.png` | The Audio menu: Sound for the preview, and Output device |
| `05-audio-output-device-menu.png` | Audio ▸ Output device: the system default named by the device it currently is, then each output |
| `06-DEFECT-after-focus-off-window-grew.png` | **Defect:** after Focus is turned off, the window grew from 893 to 1029 px, taller than the 933 px screen |
| `capture-log.txt` | The capture script's measurements |

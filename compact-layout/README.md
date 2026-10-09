# Compact layout correction: native before and after

This folder holds images only and is not for merging.

## What was captured

- **Before:** the tested candidate `39fe1ee929b337aab2bc755bc33eeb121136ef6a`, the build Nk rejected on 9 October.
- **After:** `claude/compact-layout` at `00c8404f2679bffc1b63824d7812cc10b7d96326`.
- **Both runs:** natively on the Windows platform plugin, PySide 6.11.1, at Nk's window sizes from his screenshots (1160×880 and maximised 1490×880 logical px). The screen is 1493×933 at a device pixel ratio of 1.5.
- **Isolation:** settings in a temporary INI file, and HOME and APPDATA in temporary folders.
- **Media:** a generated 14-clip card with clustered dates, which triggers the card-clock note, and a generated music track. Nothing was played.
- **Script:** `layout_matrix.py`. Each `matrix.json` holds every measurement.

| | Before | After |
|---|---|---|
| Fully visible list rows, 1160×880: Normal / Expanded, Music closed | 2 / 2 | 3 / 6 |
| Fully visible list rows, Music open: Normal / Expanded | 1 / 2 | 2 (third nearly whole) / 4 |
| Music band body, Normal with Music open | 26 px (track row only) | 120 px (track, Focus, grip, Level, fade lane) |
| Music band body, Collapsed with Music open | 64 px | 254 px (full lanes) |
| Picture floor with Music open or Expanded | 278 px | 162 px |
| Window minimum height, 1490 Collapsed with Music open | 880 (at the window's height) | 650 |

# Music Focus fix: native screenshots

Code: claude/music-focus-fit at 39fe1ee929b337aab2bc755bc33eeb121136ef6a (from main 581f86b). It ran natively on the Windows platform plugin with the same isolated settings and generated media as `../pr164/README.md`.

The window was 1400x893 on a 933 px screen. After Focus on and then off, it stayed 1400x893, with the track and Level rows in view. Before the fix it grew to 1029 px; see `../pr164/06-DEFECT-after-focus-off-window-grew.png`.


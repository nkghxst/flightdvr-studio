# Known issues

## Windows: cancelling while the sound is checked can leave an unfinished file

**Affects:** Windows. **Status:** accepted for release as a known issue; still under investigation.

### What you might see

Every export is written first to a temporary file next to its destination,
named after it with `.flightdvr-part` before the extension. For example,
`flight.mov` is written as `flight.flightdvr-part.mov`. The temporary file is
only renamed to the real name after it has been fully checked.

If you **cancel** an export while FlightDVR Studio is checking the sound of the
finished file (the last step before it is put in place), Windows occasionally
refuses to delete that temporary file. When that happens:

- the export is reported as **Cancelled**, together with the words
  *its unfinished file could not be removed* and the full path of the file
  that was left;
- the file at that path is the **unfinished, cancelled** export. It is not
  your export and should not be used;
- the destination itself is not touched. A file you already had under the
  destination name is left as it was, and nothing is published under that
  name.

How often this happens on real machines has not been measured. It has been
seen only in automated tests on Windows, and only for a cancel during the
sound check.

### What to do

Delete **only the one file named in the message**, once the app has stopped
exporting. Its name always contains `.flightdvr-part`. If Windows says the
file is in use, wait a moment and try again; it has been free again within a
fraction of a second in every case measured.

Do not delete other files in the folder, and there is no need to end any
process.

### What is known

- The file is the job's own temporary output. The app tries to remove it once,
  reports exactly why it could not, and never retries silently.
- In the recorded failures, the file was refused with Windows error 32 (in use
  by another process). The cancelled decoding process had exited about two
  seconds earlier, and the file could be opened exclusively again almost
  immediately afterwards.
- Which process held the file, and why, is **not known**. A diagnostic run
  designed to record the timing (draft PR #161) did not see the failure recur,
  so it is inconclusive.
- No damage to a destination file, and no export wrongly reported as
  finished, has been observed. Either of those would be a different, more
  serious problem and should be reported.

### Investigation record

| Date | What |
|---|---|
| 29 Sep 2026 | Local traces of the same cancel all removed the file. A file-PID query was added to the test observer. |
| 1–2 Oct 2026 | CI failures in runs 36901041940, 36939750838, 36943104763 and 36985161869, each winerror 32 on the job's own partial. The first three record the cancelled decode exiting 2.005–2.009 s before the failed delete. |
| 2 Oct 2026 | Source and log analysis: the 2 s matches one of the cleanup's own 2-second bounds, but which step absorbed it is unproven. An in-memory phase trace (PR #161) ran once and did not recur: inconclusive. |
| 2 Oct 2026 | Accepted for release as a documented limitation. Investigation continues from new, naturally occurring failures. |

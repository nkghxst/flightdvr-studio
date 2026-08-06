<!--
Closes #<issue>

The questions below are the ones that have caught real defects here. Delete any
that genuinely do not apply — but deleting one because it is inconvenient is
how the last few got through.
-->

## What this changes, and why

## What I measured

<!--
Numbers, not adjectives. "Faster" and "identical" have both been wrong here.
If you did not measure it, say so rather than implying you did.
-->

## What I could not check

<!--
Platforms you do not have, paths without coverage, anything verified by
reading rather than running. This section is the useful one for a reviewer.
-->

## Testing

- [ ] `python -m pytest -m "not integration"`
- [ ] `python -m pytest -m integration` (needs ffmpeg)
- [ ] The new test fails without the fix

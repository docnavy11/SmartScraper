A scrape script that used to work has failed. You get the failing script, the
validator report, the captured page from the failed run, and a sample of the
last output that was good.

Find the smallest change that makes the script produce that shape of data again.

1. Read the validator report first. It says which rule failed and what was
   measured. A zero row count and a rising null rate are different faults.
2. `snapshot` the captured page and compare it to the selectors in the script.
3. Probe the selector that failed, then probe your replacement, before you
   propose anything.
4. `propose_script` with the whole repaired script. Change only what the fault
   requires.

Prefer, in this order: a new `fallback_selectors` entry, a changed `selector`, a
changed step, a new step. Each of those is a larger change than the one before
it and needs more evidence. Do not add a `custom_python` step; a version that
adds one is parked for human approval no matter what else it does.

End with a one-paragraph rationale: what broke on the page, what you changed,
and what you probed to know it works.

# Wiring

This lane wrote nothing outside its own worktree. The edits below are proposals for the
orchestrator to apply at merge, given as exact old→new strings against files in the vault.

**All of it is blocked on one decision that is Jason's, not mine:** where this repo gets
cloned on his machine, and whether the drift check should run at all before he has an
evidence file to feed it. Today, with no evidence file, a real run marks 254 of 257 signals
SUSPECT (see `.vibecodepm/metrics.md`). That is correct behavior and probably useless as a
weekly report until hire evidence exists. **My recommendation is to apply edit 2 only, and
hold edit 1 until there is an evidence file.** Both are written out so the choice is his.

Throughout, `<CLONE>` stands for the chosen clone path.

---

## Edit 1 — `/weekly` Step 0.8, gate crossings before they spend Clay credits

**Rationale.** Step 0.8 already produces crossings and routes them to c1-spec enrichment,
which Step 0.8's own text notes "spends Clay credits, so it is Jason's yes." A crossing that
is a dead signal spends those credits for nothing. The drift check runs on the same
`state.json` the monitor just wrote, costs nothing, and needs no network.

File: `.claude/commands/weekly.md`

OLD (one line, at the end of Step 0.8's bullet list):

```
- If the scrape fails (LinkedIn rate limit), note it and move on; it self-heals next week.
```

NEW:

```
- If the scrape fails (LinkedIn rate limit), note it and move on; it self-heals next week.

**Then drift-check the crossings before proposing enrichment (added 2026-09-10).** Run
`python3 -m sdd check --state builds/vacancy-monitor/state.json --evidence <CLONE>/hires.json --ledger <CLONE>/ledger.json`
from `<CLONE>` (free, no network, ~1s). A duration signal's score only climbs with age, so
nothing in the monitor asks whether the vacancy is still real. Any crossing that comes back
INVALIDATED does not go into the retro as an enrichment proposal; name it in one line with
the falsifier that broke it. SUSPECT crossings still go in, flagged, because SUSPECT means
nobody checked rather than the signal being dead. Precedent: on 2026-09-09 a GTM role open
192 days sat top of the sourcing list until someone noticed by accident that the company had
hired into that function months earlier.
```

---

## Edit 2 — vacancy-monitor README, record that the validity layer now exists

**Rationale.** The README's improvement queue is where a future session looks. Right now
nothing in vacancy-monitor points at the fact that a falsifier layer for its own signal
exists, which is the "an artifact with no reader is not an artifact" failure from the root
CLAUDE.md.

File: `builds/vacancy-monitor/README.md`

OLD:

```
## Improvement queue (proposed, not built)

- **Wire departures chain into /weekly** once hop 2 validates live.
```

NEW:

```
## Improvement queue (proposed, not built)

- **Wire departures chain into /weekly** once hop 2 validates live.
- **Drift-check crossings before enrichment** — `signal-drift-detector` (build-queue row 45,
  2026-09-10) reads this repo's `state.json` and re-checks the falsifiers under each
  duration signal: age past an evergreen ceiling, hires observed into the function since the
  posting date, posting missing from recent scrapes. Blocked on hire evidence, which it
  consumes from a file and does not gather. Without that file every signal fails closed to
  SUSPECT, so wiring it into /weekly Step 0.8 waits until the file exists.
```

---

## Not proposed, and why

**Calling the drift check from `monitor.py` directly.** Rejected. The monitor's job is to
track ages; making it also arbitrate validity couples two tools that release on different
schedules, and it would put an import of this repo inside a repo that has no dependency on
it. `/weekly` is the right seam, since it already orchestrates both.

**Writing verdicts back into `state.json`.** Rejected. This tool never writes to its source,
and a verdict written into the source would be read on the next run as if it were source
data.

**A `read_by:` frontmatter line on anything in this repo.** The vault rule applies to vault
artifacts. This repo's own docs are read from the repo, and `docs/SPEC.md`,
`.vibecodepm/flow.md` and `.vibecodepm/metrics.md` each name their reader in frontmatter or
in the README's layout table.

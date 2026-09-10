# signal-drift-detector — spec

Build-queue row 45. Lane agent: `lane-45-signal-drift-builder`. Written 2026-09-10, before any code.

## 1. The problem this exists for

A duration signal is a signal whose score only climbs with age. "This role has been open
192 days" is the canonical one. Recency weighting is structurally blind to it: the signal
gets *stronger* the longer it sits, so nothing in a scoring stack ever asks whether the
premise underneath it is still true.

The worked case this build comes from, and the golden test case for the suite:

> A GTM role open 192 days, the oldest of 257 tracked postings, ranked top of the sourcing
> list. A human reviewer, reading a roster for an unrelated reason, noticed hires had been
> made into that exact function months after the posting went up. Read: likely evergreen
> recruiting, not a true vacancy. The account was ruled out before send.

The catch was luck. A person happened to read the right roster on the right day. This tool
makes that catch designed.

## 2. What the tool does

It gives a signal a **falsifier** — the fact that would have to still be true for the
signal to still be valid — and re-checks that falsifier on a **schedule** against a
**threshold**, before the signal is allowed to rank or enter an outreach sequence.

Output is a verdict per signal:

| Verdict | Meaning | Ranking |
| --- | --- | --- |
| `VALID` | Every falsifier still holds. | Eligible |
| `SUSPECT` | A falsifier is degraded, or a check could not run. | Excluded, flagged for review |
| `INVALIDATED` | A falsifier has been broken by evidence. | Excluded |

Every verdict carries the falsifier statement, the evidence that moved it, and the check
date. Verdicts combine worst-wins across a signal's falsifiers.

## 3. Scope — strict

**In:** falsifier + schedule + threshold on ONE signal class, `vacancy_duration`. One
adapter, `vacancy-monitor`'s `state.json` (~257 tracked postings, ages persisted across
runs).

**Out, explicitly, and not to be quietly re-added:** positioning drift; segment-threshold
drift; a plugin framework for arbitrary signal sources; any network call; any scoring or
ranking logic of its own beyond emitting eligibility. One signal class, one adapter, done
well.

## 4. Design decisions

Recorded per the lane brief. No divergences from the row; where the brief left a choice
open, the resolution is marked **[resolved here]**.

1. **Python 3, stdlib only, CLI-runnable.** Matches `vacancy-monitor` (Python, `state.json`
   + `tests.py`). No third-party dependency, so a clean clone runs with nothing installed.
2. **Input is a path argument.** `--state <path>` points at a vacancy-monitor-shaped
   `state.json`. No absolute path to Jason's machine is baked in anywhere.
3. **Synthetic fixtures only.** The real `state.json` was read for *shape* only
   (top-level dict, key `li-<id>`, value with `company`, `title`, `date_posted`, `job_url`,
   `company_url`, `first_seen`, `reported`, `last_seen`). Fixtures in this repo are
   invented companies and invented postings modeled on that shape. No real company,
   contact, or posting data is committed here, ever.
4. **The falsifier registry, the schedule, and the thresholds are data.** `config.json` in
   the repo holds the falsifier definitions (name, statement, check id, thresholds), the
   signal-class registry that maps a class to its falsifiers and its recheck interval, and
   the title→function map. Check *implementations* are code, keyed by the `check` id the
   config names; a config naming an unknown check id is a load-time error, not a silent
   skip.
5. **Output is both machine-readable and human-readable.** `--format json` emits verdict
   records; the default emits a report. `--ranking-only` emits just the eligible signal
   ids, which is the form a ranking stage consumes.
6. **Fail closed.** A check that errors, a check whose evidence source has no record for
   the subject, an unknown signal class, an unparseable date — all produce `SUSPECT` with
   the reason attached. Nothing becomes `VALID` by default or by silence.
7. **Deterministic, keyless tests.** No network in the suite. `--as-of YYYY-MM-DD` fixes
   the clock so age arithmetic is reproducible; it defaults to today.
8. **Evidence is a pluggable source with one file-based implementation.**
   `no_hires_since_posting` needs external evidence (observed hires into a function). v1
   ships `FileEvidenceSource` reading a JSON file, plus a `NullEvidenceSource` that
   reports "unknown" for everything and therefore drives SUSPECT. The README says plainly
   that v1 does not *gather* hire evidence; it *consumes* it. **[resolved here]** the
   evidence file is hand-maintained or fed by a future adapter; that gap is a stated
   limitation, not a hidden one.
9. **Schedule state is a ledger.** `--ledger <path>` holds the last check date and last
   verdict per signal id. A signal inside its recheck interval is not re-checked; its prior
   verdict carries forward marked `carried_forward`. `--force` re-checks everything. A
   missing ledger means everything is due, which is the correct fail-closed default.
10. **The tool never writes to the source.** It reads `state.json`; it writes only its own
    ledger and its own output paths.

## 5. Falsifiers shipped in v1

All three apply to `vacancy_duration`. Statements are the load-bearing part — a falsifier
is only useful if a human can read what it claims and disagree.

| Name | Statement (must still be true) | Breaks when |
| --- | --- | --- |
| `evergreen_age_ceiling` | The posting's age is below the point where an unfilled posting is more likely evergreen recruiting than a live vacancy. | Age crosses `suspect_after_days`, then `invalidate_after_days`. |
| `no_hires_since_posting` | No hires have been observed into this function at this company since the posting date. | Evidence records a hire into the mapped function dated after `date_posted`. Unknown company → SUSPECT. |
| `posting_still_listed` | The posting was still listed on the most recent source scrape. | `last_seen` falls behind `as_of` by more than the thresholds. |

The golden case exercises the first two together: 192 days old *and* a recorded hire into
`gtm` after the posting date → `INVALIDATED`, with both falsifiers cited.

## 6. Prior art — gate run 2026-09-10 by the orchestrator

Reproduced verbatim in effect:

- No drift or falsifier build exists in `builds/`, in `gh repo list derrtaderr`, in the
  build ledger, or in the gtm-resources parts bin.
- `evalgate`'s "drift" is **provider drift** (a pinned model id deprecating). Different
  concept.
- `maestro_crm`'s drift checks are **metric-definition** checks. Different concept.
- `vacancy-monitor`'s own "stale/drift" mentions are about the provider **feed** being
  ~3 months stale (data freshness), not signal validity. Its code carries zero falsifier,
  evergreen, or revalidation logic.
- The signal source exists in production; the validity check exists nowhere.

**Greenfield confirmed.**

## 7. Layout

```
config.json            falsifier registry, signal classes, thresholds, function map
sdd/model.py           Signal, Verdict, FalsifierResult, SignalVerdict
sdd/config.py          load + validate config
sdd/adapters.py        vacancy-monitor state.json -> [Signal]
sdd/evidence.py        FileEvidenceSource, NullEvidenceSource
sdd/checks.py          check implementations keyed by check id
sdd/schedule.py        ledger read/write, due/not-due
sdd/engine.py          orchestration, worst-wins, fail-closed
sdd/report.py          human-readable rendering
sdd.py                 CLI entry point
fixtures/              synthetic state.json + evidence.json
tests.py               stdlib unittest; run with `python3 tests.py`
```

## 8. Process

Strict TDD, a commit per red-green cycle, conventional commits. This spec is commit 1.
Anything requiring an edit outside this worktree (e.g. wiring vacancy-monitor's weekly run
to call this tool) goes into `WIRING.md` as exact old→new strings for the orchestrator to
apply at merge. No vault writes from this lane.

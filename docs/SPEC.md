# signal-drift-detector — spec

Build-queue row 45. Lane agent: `lane-45-signal-drift-builder`. Written 2026-09-10, before any code.

**Amended 2026-09-12, build-queue row 51** (lane `lane-51-hire-epistemics-builder`), which
added §9 and rewrote the `no_hires_since_posting` row in §5. §9 was written before any code
for that change, same rule as the original.

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
| `no_hires_since_posting` | No hires have been observed into this function at this company since the posting date. | A hire into the mapped function dated after `date_posted` **and** corroboration (see §9). A hire alone degrades it to SUSPECT. Unknown company → SUSPECT. |
| `posting_still_listed` | The posting was still listed on the most recent source scrape. | `last_seen` falls behind `as_of` by more than the thresholds. |

The golden case exercises the first two together: 192 days old *and* a recorded hire into
`gtm` after the posting date. The age ceiling breaks and the signal is `INVALIDATED`; the
hire falsifier lands SUSPECT, because one hire into a `GTM Engineer` posting is not
corroborated. §9 is the whole argument.

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
config.json            falsifier registry, signal classes, thresholds, function map,
                       single_seat_patterns (added row 51, see §9.4)
sdd/model.py           Signal, Verdict, FalsifierResult, SignalVerdict
sdd/config.py          load + validate config
sdd/adapters.py        vacancy-monitor state.json -> [Signal]
sdd/evidence.py        FileEvidenceSource, NullEvidenceSource
sdd/checks.py          check implementations keyed by check id
sdd/schedule.py        ledger read/write, due/not-due
sdd/engine.py          orchestration, worst-wins, fail-closed
sdd/report.py          human-readable rendering
sdd/cli.py             argument parsing, CLI entry point
sdd/__main__.py        `python3 -m sdd` entry point
fixtures/              synthetic state.sample.json + evidence.sample.json
tests.py               stdlib unittest; run with `python3 tests.py`
```

## 8. Process

Strict TDD, a commit per red-green cycle, conventional commits. This spec is commit 1.
Anything requiring an edit outside this worktree (e.g. wiring vacancy-monitor's weekly run
to call this tool) goes into `WIRING.md` as exact old→new strings for the orchestrator to
apply at merge. No vault writes from this lane.

## 9. Hire epistemics — corroboration before invalidation

Added after the v1 release, from an accepted outside review of the published article about
this build. The reviewer is a simulated reader persona, so the credit here is to the
**argument**, not to a name; a persona is not a citable endorser.

### 9.1 The bug in v1

v1 read any function-matched, post-dated hire as proof the falsifier was broken, and the
signal went straight to `INVALIDATED`.

That is not what a hire proves. "3 Senior GTM Engineers" is one posting and three chairs.
A company scaling a function posts once and hires five times. In both cases the hire is
real, the posting is still live, and v1 would have thrown the account away.

A detected hire is **counterevidence to the unfilled-role reading**. It is not proof the
vacancy is gone. v1 collapsed those two things, which is a tool claiming to know more than
its evidence supports — the exact failure it was built to catch. Fixing it is not a feature
request; it is the artifact obeying its own thesis.

### 9.2 The new semantics

A hire into the mapped function, dated after the posting:

| Path | Condition | Falsifier | Signal (before worst-wins) |
| --- | --- | --- | --- |
| `uncorroborated-hence-suspect` | A hire, nothing else | SUSPECT | SUSPECT |
| `corroborated-by-delisting` | A hire, **and** `posting_still_listed` is INVALIDATED in the same run | INVALIDATED | INVALIDATED |
| `corroborated-by-single-seat-title` | A hire, **and** the title matches a `single_seat_patterns` entry | INVALIDATED | INVALIDATED |

Every evidence line names its path with the literal token above, so an operator can grep a
run for which argument fired and disagree with that argument specifically.

Worst-wins across falsifiers is **unchanged**. This change only alters what the hire
falsifier reports about itself.

### 9.3 Why those two corroborators, and no others

**Delisting.** A hire into the function plus a listing that stopped appearing is two
independent observations pointing at the same conclusion. Each alone is weak. Together they
are the ordinary signature of a filled seat. Only `INVALIDATED` on `posting_still_listed`
corroborates; its SUSPECT band (14–44 days missing) is itself an "I am not sure," and two
unsure readings do not make a sure one.

**Single-seat title.** "Founding GTM Engineer," "Head of Revenue Operations," "VP of Sales"
name *one chair*. A company does not hire two founding GTM engineers. When the title names
one seat and a hire lands in that function, the expansion reading is gone and the filled
reading is what is left.

Rejected as corroborators: headcount parsed from the posting body (this tool never reads
posting bodies, and "3 Senior..." in a title is not reliably present), company size
(not in `state.json`), and hire count (see §9.5).

### 9.4 `single_seat_patterns` — a new config key

A list of keyword patterns, matched case-insensitively against the title **at word
boundaries**, normalised to stripped lowercase at load. Shipped default, with the reasoning
for each:

```json
"single_seat_patterns": ["founding", "head of", "director of", "chief", "principal", "vp"]
```

- `founding` — "Founding X" is definitionally the first and only one.
- `head of`, `director of`, `chief` — leadership of a function, one post per function.
- `principal` — the top individual-contributor rung; companies open one at a time.
- `vp` — matches "VP Sales", "VP of Sales" and "Sales VP".

**Word boundaries, not substrings, and this is load-bearing.** A plain substring scan fires
`vp` inside "MVP Growth Engineering Lead" and `head of` inside "Analyst Ahead Of Market".
Every such false fire lands on INVALIDATED, which throws away an account that may still be
live. Being wrong in that direction is the failure this whole change exists to remove, so a
pattern's first character must not be preceded by a word character and its last must not be
followed by one. The rule also settles "SVP Sales" with no special case (`vp` is preceded by
`s`); a workspace that wants SVP treated as one chair adds `"svp"` to config, which is where
that judgment belongs.

The list is deliberately short and conservative. Every pattern added makes the tool quicker
to throw an account away, so the bar for adding one is "this title cannot describe two
chairs," not "this title sounds senior."

**Validation follows the `function_map` lesson (the S1 review class), and for the same
reason.** A bare string is iterated character by character, so `"single_seat_patterns":
"founding"` would match nearly every title, make every hire corroborated, and restore
exactly the v1 behavior this change removes — silently, from a config that looks set. A
non-string or empty pattern matches everything, with the same result. Both are refused at
load with exit 2 and a message naming the key, never a silent skip.

An absent key is an empty list: no title corroborates, every hire lands SUSPECT. That is
the fail-closed direction, so absence is legal where malformation is not.

### 9.5 Divergences from the change brief, reasoned

1. **No new `DEGRADED` status.** The brief describes the falsifier "landing DEGRADED."
   `sdd/model.py` already defines SUSPECT as exactly that — its docstring reads "A
   falsifier is degraded, or its check could not run" — and the README's verdict table says
   the same. A fourth status would need a new severity rank in `worst()`, a new report
   section, a new JSON value for every consumer, and would buy no distinction that SUSPECT
   does not already carry. DEGRADED is implemented as the existing SUSPECT.

2. **Corroboration-by-delisting is resolved in the engine, not inside the check.** A check
   receives one signal and its own thresholds; it cannot see a sibling falsifier's outcome,
   and giving it one would make check order significant. So the check returns SUSPECT
   carrying a conditional promotion (`corroborated_by`), and the engine — which already owns
   combination, via worst-wins — applies it after every falsifier has run. The pass is
   single and non-cascading: a result promoted by escalation cannot itself trigger another
   escalation, so the outcome never depends on the order falsifiers are listed in.

3. **Single-seat corroboration is resolved inside the check**, because the title is on the
   signal and needs nothing from a sibling.

4. **Multiple hires do not corroborate.** Three hires into an expanding function is the
   *expansion* reading, not the filled one; counting them and escalating past some threshold
   would re-introduce the v1 error with an arbitrary number attached. The evidence line
   reports the count so a human can weigh it. The verdict stays SUSPECT.

5. **When both corroborators fire, single-seat is reported.** Both reach INVALIDATED, so the
   verdict is identical either way; the title argument is named because it is the one a
   reader can check without re-running the tool.

### 9.6 What this changes about the catches already made — verified, not assumed

**The bundled fixture's 192-day posting** (`li-9000000001`, title `GTM Engineer`). The hire
falsifier moves INVALIDATED → SUSPECT: `GTM Engineer` matches no single-seat pattern, and
`last_seen` is one day back so nothing delists. The signal's verdict is **unchanged at
INVALIDATED**, because 192 days is past the 180-day evergreen ceiling and worst-wins takes
the age falsifier. Verified by running the suite, not reasoned about.

**The two real postings the published article worked through.** Named there, left unnamed
here, because §4.3 of this spec forbids real posting data in this repo and a name adds
nothing to the classification argument.

- The one titled *Founding GTM Engineer*: **unchanged at INVALIDATED**, now via
  `corroborated-by-single-seat-title`. A founding seat is one seat.
- The one with a bare *GTM* title at a seven-person company: the hire axis moves to
  SUSPECT, since a bare function title names no seat count. Its **signal verdict survives as
  INVALIDATED via the age ceiling**, on the same worst-wins path as the fixture case.

So no historical catch is lost. What is lost is the *claim* that the hire alone made two of
them. In one of the three, it never did.

### 9.7 Fixture additions

The v1 fixture demonstrated one hire outcome, because v1 had one. Two postings were added
so a clean clone can see all three paths in a single run without writing its own data:

- a `Founding GTM Engineer` posting with a post-dated `gtm` hire, young and still listed →
  `corroborated-by-single-seat-title`
- an `Analytics Engineer` posting with a post-dated `data_eng` hire, 52 days off the
  scrapes → `corroborated-by-delisting`

Both are synthetic, per §4.3.

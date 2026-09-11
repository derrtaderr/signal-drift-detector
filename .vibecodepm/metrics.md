---
name: signal-drift-detector metrics
read_by: vibecodepm ship-check, and any review deciding whether this build earned its place
---

# Metrics

## Activation event

**The first run that flips a previously-VALID signal to SUSPECT or INVALIDATED on real
data.**

That is the moment the tool has done something a human would otherwise have had to notice by
accident. Everything before it is setup.

It is deliberately not "the tool ran," not "verdicts were emitted," and not "an INVALIDATED
appeared on a first run." A first run has no prior verdict to contradict, so an
INVALIDATED there is a filter doing filter work. The value claim is about **drift** — a
signal that was fine and stopped being fine — and only a flip against a stored verdict
demonstrates it.

### How it is measured

The ledger already records the last verdict per signal id, and every verdict carries
`checked_on`. A flip is detectable with no new instrumentation:

```bash
python3 -m sdd check --state state.json --evidence hires.json \
  --ledger ledger.json --out today.json
```

Compare `today.json`'s per-signal `verdict` against the pre-run ledger entry. A signal whose
ledger verdict was `VALID` and whose new verdict is not is an activation event. The verdict
record carries the falsifier name and evidence string that caused it, so each flip is
self-documenting.

**Not yet built:** a `--diff` flag that prints flips directly. Today the comparison is a
manual read of two files, or a caller's own diff. Naming that honestly here rather than
claiming instrumentation that does not exist.

## North star

**Accounts ruled out before send that a recency-weighted ranking would have ranked at the
top.**

The worked case is exactly one of these, caught by luck. The number this build is trying to
move is how many get caught on purpose.

## Week-one numbers

Real, from a live 257-posting state file, `--as-of 2026-09-10`, no evidence file supplied:

| Measure | Value |
| --- | --- |
| Signals parsed from the real source | 257 |
| INVALIDATED on age alone, evidence-free | 3 |
| SUSPECT for want of an evidence file | 254 |
| Oldest posting in the set | 291 days |

Read that honestly. The 3 are real finds — postings past 180 days that a duration ranking
would have scored highest. The 254 are not findings; they are the fail-closed rule reporting
that the strongest falsifier has nothing to check against yet.

## The number that decides whether this build earned its place

**Signals moved from SUSPECT to a decided verdict as evidence coverage grows.**

254 of 257 SUSPECT means the tool is currently honest and nearly useless in the same breath.
Its usefulness is bounded by evidence coverage, so the metric that matters over the first
month is what fraction of tracked companies have hire observations on file, and whether the
decided fraction climbs. If evidence coverage stays near zero, this build produced a
correctly-designed instrument that nobody can act on, and that is the failure mode to watch
for rather than the verdict counts.

## Explicitly not a metric

Number of INVALIDATED verdicts. Tightening the age ceiling to 60 days would triple it and
mean nothing. Verdict counts are an output of the thresholds, so they measure config, not
signal quality.

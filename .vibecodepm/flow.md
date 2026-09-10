---
name: signal-drift-detector flow map
read_by: vibecodepm ship-check, and any agent changing CLI behavior or error handling
---

# Flow

## Who is walking through this

An operator who runs a sourcing instrument weekly and has a ranked list of duration signals
coming out of it. They are not going to read the source. They want to know which rows on
that list are dead, and why, before anything gets sent.

## Entry point

One command. There is no install step, no key, no config to write before first run.

```bash
python3 -m sdd check --state <path> [--evidence <path>] [--ledger <path>]
```

`python3 -m sdd` with no subcommand prints help and exits 1. `--state` is the only required
argument.

## Happy path

1. Operator runs `check` with a state file and an evidence file.
2. Config loads from the repo's `config.json` (resolved from the package location, so the
   working directory does not matter).
3. The adapter reads the state file into `vacancy_duration` signals, computing each age
   against `--as-of` or today.
4. For each signal the ledger decides due or not due. Due signals run every falsifier
   registered for their class; not-due signals carry their stored verdict forward.
5. Verdicts combine worst-wins per signal.
6. The ledger is written, the report prints worst-first, and the last line says how many of
   how many may rank.
7. Exit 0.

Second run the same day with the same `--ledger`: everything is inside the 7-day interval,
so every row is marked `carried forward from <date>` and no check re-runs. That is the
intended steady state, not a degraded one.

## States a signal can be in

| State | What the operator sees | What they do |
| --- | --- | --- |
| `VALID` | One line, marked `[ok]`, under "Eligible to rank." | Nothing. It flows on. |
| `SUSPECT` | `[??]`, with the specific reason the check could not confirm. | Look, or feed better evidence. |
| `INVALIDATED` | `[XX]`, with the falsifier statement and the evidence that broke it. | Drop the account before send. |
| carried forward | `carried forward from <date>, inside the 7-day recheck interval` | Nothing. Use `--force` to override. |

## Recovery paths

Every one of these was built test-first and has a test asserting the behavior.

**Malformed state.json.** Unparseable JSON, a top-level array, a posting that is not an
object, a posting missing `company` / `title` / `date_posted`, or a date that is not ISO.
The run stops before producing any verdict, prints `error: <what and where>` to stderr, and
exits 2. No traceback, and never a partial verdict list, because a partial list read as a
complete one is the same class of failure the tool exists to prevent.

**No evidence file.** Not an error. The hire falsifier reports `no evidence source
configured, so the hire check could not run`, and every signal lands at SUSPECT. The
operator sees an all-SUSPECT board and the reason on every row.

**Company absent from the evidence file.** `no hire observations on record for <company>,
so nobody has looked` → SUSPECT. Distinct from a company present with an empty `hires` list,
which is `looked, found nothing` → VALID.

**Unmappable title.** `title 'Warehouse Associate' does not map to a known function, so
hires cannot be scoped to it` → SUSPECT. The fix is a `function_map` entry in config.

**Unknown signal class.** A signal whose class has no registered falsifiers returns SUSPECT
with `no falsifiers registered for signal class 'x', so its validity cannot be checked`. The
run continues; other signals are unaffected.

**A check raises.** The exception is caught per falsifier, that falsifier becomes SUSPECT
carrying `check 'x' raised RuntimeError: <message>`, and the rest of the run completes. One
broken check can degrade a verdict; it can never take down the run or silently pass.

**Corrupt or partial ledger.** Discarded, and everything is treated as due. A ledger is a
cache and never a source of truth, so unreadable means re-check, which is the fail-closed
direction.

**Bad config.** A falsifier naming an unimplemented check id, a falsifier with no statement,
a signal class with no falsifiers, or an undefined falsifier reference. All are load-time
errors with exit 2, before any signal is read. A silently skipped check would read as VALID
downstream, which is the one outcome that must never happen by accident.

## What the operator never has to do

Write config before the first run. Supply an API key. Have network access. Know where the
tool is installed relative to their working directory.

# signal-drift-detector

Give a signal a falsifier. Re-check it on a schedule. Hold it out of ranking when it breaks.

Some signals only get stronger with age. "This role has been open 192 days" is the
canonical one, and recency weighting is structurally blind to it, because the signal's
score climbs the longer it sits. Nothing in a scoring stack ever turns around and asks
whether the premise underneath it is still true.

This tool asks. For each signal it stores the **falsifier** — the fact that would have to
still be true for the signal to still be valid — re-checks it against a **threshold** on a
**schedule**, and emits a verdict per signal with the evidence and the check date.

| Verdict | Meaning | May it rank? |
| --- | --- | --- |
| `VALID` | Every falsifier still holds. | Yes |
| `SUSPECT` | A falsifier is degraded, or its check could not run. | No, held for review |
| `INVALIDATED` | A falsifier was broken by evidence. | No |

## The case this was built from

A GTM role open 192 days, the oldest of 257 tracked postings, sitting at the top of a
sourcing list. A reviewer reading a team roster for an unrelated reason noticed the company
had hired into that exact function three months after the posting went up. That reads as
evergreen recruiting, not a live vacancy. The account was ruled out before anyone sent
anything.

The catch worked. It was also luck, and luck does not scale to 257 postings a week. Here is
the same catch, designed:

```
[XX] Northwind Analytics — GTM Engineer (192 days old, li-9000000001)
    INVALIDATED: The posting's age is below the point where an unfilled posting
                 is more likely evergreen recruiting than a live vacancy.
      posting is 192 days old, at or past the 180-day evergreen ceiling
    SUSPECT: No hires have been observed into this function at this company
             since the posting date.
      hire into gtm at Northwind Analytics on 2026-06-14 (team roster review),
      after the posting went up on 2026-03-02. uncorroborated-hence-suspect:
      one hire cannot distinguish a filled seat from a team still expanding,
      so this is counterevidence to the unfilled-role reading rather than
      proof the vacancy is gone
```

Read that second falsifier closely, because it is the part of this tool worth arguing with.
The hire is real and it is recorded. It still does not prove the seat was filled. See
[what a hire actually proves](#what-a-hire-actually-proves).

## Install

Nothing to install. Python 3.8 or newer, standard library only, no keys, no network.

```bash
git clone https://github.com/derrtaderr/signal-drift-detector.git
cd signal-drift-detector
python3 tests.py          # 149 tests, no network, should print OK
```

## Run it against the bundled fixture

```bash
python3 -m sdd check \
  --state fixtures/state.sample.json \
  --evidence fixtures/evidence.sample.json \
  --as-of 2026-09-10
```

The fixture is synthetic, invented companies modeled on the real source shape, and it is
calibrated to `--as-of 2026-09-10` so the output is identical on every machine. You get
seven signals covering every outcome, including all three ways an observed hire can land:

```
signal drift check — 2026-09-10
============================================================
2 VALID   1 SUSPECT   4 INVALIDATED   (7 signals checked)

INVALIDATED — A falsifier broke. Excluded from ranking and from any sequence.
------------------------------------------------------------
  [XX] Northwind Analytics — GTM Engineer (192 days old, li-9000000001)
      INVALIDATED: The posting's age is below the point where an unfilled posting
                   is more likely evergreen recruiting than a live vacancy.
        posting is 192 days old, at or past the 180-day evergreen ceiling
      SUSPECT: No hires have been observed into this function at this company
               since the posting date.
        hire into gtm at Northwind Analytics on 2026-06-14 (team roster review),
        after the posting went up on 2026-03-02. uncorroborated-hence-suspect:
        one hire cannot distinguish a filled seat from a team still expanding,
        so this is counterevidence to the unfilled-role reading rather than
        proof the vacancy is gone

  [XX] Tessellate Group — Head of Growth Engineering (101 days old, li-9000000004)
      INVALIDATED: The posting was still listed on the most recent source scrape.
        not seen on a scrape for 71 days, past the 45-day limit

  [XX] Arbor Health — Analytics Engineer (92 days old, li-9000000007)
      INVALIDATED: No hires have been observed into this function at this company
                   since the posting date.
        hire into data_eng at Arbor Health on 2026-07-05 (team roster review),
        after the posting went up on 2026-06-10. corroborated-by-delisting: the
        posting also stopped appearing on scrapes in this run, and a hire into
        the function plus a listing that went away is the ordinary signature of
        a filled seat
      INVALIDATED: The posting was still listed on the most recent source scrape.
        not seen on a scrape for 52 days, past the 45-day limit

  [XX] Cascadia Robotics — Founding GTM Engineer (71 days old, li-9000000006)
      INVALIDATED: No hires have been observed into this function at this company
                   since the posting date.
        hire into gtm at Cascadia Robotics on 2026-08-05 (team roster review),
        after the posting went up on 2026-07-01.
        corroborated-by-single-seat-title: 'Founding GTM Engineer' matches the
        single-seat pattern 'founding', a title that names one chair, so the
        hire took it

SUSPECT — A falsifier is degraded or could not be checked. Held for review.
------------------------------------------------------------
  [??] Peregrine Labs — Revenue Operations Manager (143 days old, li-9000000003)
      SUSPECT: ... posting is 143 days old, past the 120-day evergreen warning line
      SUSPECT: ... no hire observations on record for Peregrine Labs, so nobody has looked

2 of 7 signals may rank.
```

### Reading a verdict

Each entry prints the falsifier's **statement** and then the **evidence** that moved it.
That pairing is the point. You are meant to be able to disagree with a verdict by reading
two lines, not by re-deriving the check. If you think 180 days is the wrong evergreen
ceiling for your market, that number is in `config.json`, not in the code.

Five separate things produce `SUSPECT`, and the evidence line always says which:

- **no evidence source configured** — no `--evidence` file was given at all, so the hire
  check could not run. This is about the run, not about any company.
- **nobody has looked** — an evidence file WAS given, and the company has no entry in it
- **title does not map to a known function** — the hire check cannot be scoped, so it is
  not run rather than run against everything
- **check raised** — the check itself failed; the exception text is in the evidence
- **`uncorroborated-hence-suspect`** — a hire WAS found, and one hire is not enough to
  call the seat filled. The next section is the argument.

None of them can become `VALID`. The tool exists because a signal was trusted by default,
so it never trusts anything by default itself.

## What a hire actually proves

You observe that a company hired a GTM engineer two months after posting a GTM role. What
follows?

Less than it looks like. The posting might have been `3 Senior GTM Engineers` — one
posting, three chairs, two still open. The company might be scaling the function, posting
once and hiring five times against it. In both cases the hire is real, the posting is live,
and the account is worth keeping.

A detected hire is **counterevidence to the unfilled-role reading**. It is not proof the
vacancy is gone. v1 of this tool collapsed those two and sent any hire straight to
`INVALIDATED`, which is a tool claiming to know more than its evidence supports — the exact
failure it was built to catch. That was raised by an outside review of the published article
about this build, and the argument was right.

So a hire degrades the falsifier to `SUSPECT`. The signal is held for review, not ranked and
not thrown away. It reaches `INVALIDATED` only when a second, independent observation agrees:

| Path named in the evidence | What fired |
| --- | --- |
| `uncorroborated-hence-suspect` | A hire, nothing else. Held for review. |
| `corroborated-by-delisting` | A hire, **and** the posting stopped appearing on scrapes. Two observations, one conclusion. |
| `corroborated-by-single-seat-title` | A hire, **and** the title names one chair — "Founding X", "Head of X", "VP of X". Nobody hires two founding GTM engineers. |

Three things this deliberately does not do:

- **Count.** Three hires into a function is the *expansion* reading, not the filled one.
  The count goes in the evidence so you can weigh it; the verdict stays `SUSPECT`.
- **Treat a shaky listing as corroboration.** Only `INVALIDATED` on `posting_still_listed`
  counts. Its warning band is itself an "I am not sure," and two unsure readings do not add
  up to a sure one.
- **Guess at headcount from the posting body.** This tool never reads posting bodies.

The single-seat list is config, in `single_seat_patterns`, because it is a judgment about
your market and you should be able to argue with it. Every pattern you add makes the tool
quicker to throw an account away, so the bar is "this title cannot describe two chairs,"
not "this title sounds senior."

## Run it against your own data

```bash
python3 -m sdd check \
  --state /path/to/vacancy-monitor/state.json \
  --evidence /path/to/hires.json \
  --ledger ./ledger.json
```

`--state` takes a [vacancy-monitor](https://github.com/derrtaderr)-shaped `state.json`: a
top-level object keyed by posting id, each value carrying `company`, `title`, `date_posted`,
`job_url`, `first_seen` and `last_seen`. No path to any particular machine is baked in
anywhere; every input is an argument.

`--ledger` is what turns this from a one-time filter into a drift detector. It records when
each signal was last checked and what the verdict was, so the next run only re-checks
signals whose recheck interval (7 days by default) has elapsed. Everything else carries its
stored verdict forward, marked `carried_forward` so you can tell a fresh answer from a
remembered one. `--force` re-checks everything.

### Feeding it into a ranking stage

```bash
python3 -m sdd check --state state.json --evidence hires.json --ranking-only
```

prints one eligible signal id per line and nothing else. Machine-readable verdicts with all
the evidence come from `--format json`, or `--out verdicts.json` to write them to a file
while still printing the report.

### Options

| Flag | Effect |
| --- | --- |
| `--state PATH` | Required. The vacancy-monitor-shaped state file. |
| `--evidence PATH` | Hire observations. Without it, every signal fails closed to SUSPECT. |
| `--config PATH` | Defaults to the repo's `config.json`. |
| `--ledger PATH` | Schedule state. Without it, every signal is re-checked every run. |
| `--as-of YYYY-MM-DD` | Treat this date as today. For reproducible runs. |
| `--force` | Re-check signals that are not yet due. |
| `--format text\|json` | Output on stdout. |
| `--ranking-only` | Print only the ids that may rank. |
| `--show-valid-detail` | Print every falsifier for VALID signals too. |
| `--out PATH` | Also write JSON verdicts to a file. |

Exit codes: `0` the run completed, `1` no subcommand given (bare `python3 -m sdd` prints
help), `2` bad input (the message goes to stderr, never a traceback).

## The evidence file

```json
{
  "companies": {
    "Northwind Analytics": {
      "checked_through": "2026-09-08",
      "hires": [
        {"function": "gtm", "date": "2026-06-14", "source": "team roster review"}
      ]
    },
    "Cobalt Systems": {"checked_through": "2026-09-08", "hires": []}
  }
}
```

One distinction here carries the whole design. A company **present** with an empty `hires`
list means *somebody looked and found nothing*. A company **absent** means *nobody looked*,
and produces SUSPECT. Collapsing those two into "no hires" is precisely the failure this
tool exists to prevent, so it is a structural distinction rather than a convention.

## Configuring falsifiers

The falsifier registry, the recheck schedule, the thresholds, the title-to-function map and
the single-seat title patterns all live in `config.json`. Adding a threshold, retuning a
ceiling, or teaching it a new title family is a config edit.

```json
"evergreen_age_ceiling": {
  "check": "age_ceiling",
  "statement": "The posting's age is below the point where an unfilled posting is more likely evergreen recruiting than a live vacancy.",
  "thresholds": {"suspect_after_days": 120, "invalidate_after_days": 180}
}
```

`single_seat_patterns` is a flat list of lowercase keywords, substring-matched against the
title:

```json
"single_seat_patterns": ["founding", "head of", "director of", "chief", "principal", "vp"]
```

Patterns match at **word boundaries**, not anywhere in the string. That matters more here
than it looks: a plain substring scan reads "MVP Growth Engineering Lead" as a VP posting
and "Analyst Ahead Of Market" as a head-of posting, and both false fires land on
`INVALIDATED` — discarding an account that may still be live, which is the expensive
direction to be wrong in. The rule also settles "SVP Sales" without a special case, since
`vp` there is preceded by `s`. A workspace that wants SVP treated as one chair adds `"svp"`,
which is the right place for that judgment.

Both this list and `function_map` are refused at load if a value is a bare string or an
entry is empty, because a bare string is matched one character at a time and would make
nearly every title match. An absent `single_seat_patterns` key means no title corroborates.

Adding a genuinely new *kind* of falsifier means writing a check function in
`sdd/checks.py` and registering it in that module's `CHECKS` table, then naming its id from
config. A config naming a check id that does not exist is a load-time error rather than a
skipped check, because a skipped check would read as VALID downstream.

The three shipped falsifiers:

| Falsifier | Must still be true | Breaks when |
| --- | --- | --- |
| `evergreen_age_ceiling` | Age is below the evergreen point. | Age crosses 120, then 180 days. |
| `no_hires_since_posting` | No hires into this function since the posting date. | A hire into the mapped function dated after `date_posted`, **plus** corroboration. A hire alone degrades it to `SUSPECT` — see [what a hire actually proves](#what-a-hire-actually-proves). |
| `posting_still_listed` | The posting was on the most recent scrape. | `last_seen` falls 14, then 45 days behind. |

## Limitations, stated plainly

- **v1 consumes hire evidence, it does not gather it.** `no_hires_since_posting` reads a
  file somebody else fills in. There is no scraper, no roster API, no enrichment call in
  this repo. The evidence source is an interface (`sdd/evidence.py`) precisely so a real
  adapter can be dropped in later, but today the honest description is that the strongest
  falsifier depends on a hand-maintained file.
- **Consequence: a first run with no evidence file marks nearly everything SUSPECT.**
  Verified against a real 257-posting state file: 257 parsed, 3 INVALIDATED on age alone,
  254 SUSPECT because no evidence file was supplied. That is the fail-closed rule working,
  not a bug, but it means the tool is only as useful as the evidence you feed it.
- **The age ceiling is a heuristic, not a fact.** 180 days is a starting number, not a
  measured one. It is in config so you can argue with it.
- **Title-to-function mapping is keyword matching.** "Head of Growth Engineering" maps to
  `gtm` because "growth engineering" is in the list. A title nobody has taught it maps to
  nothing, and the hire check fails closed rather than guessing.
- **Single-seat detection is keyword matching too, and it is the one place a title alone
  can end a signal.** It will miss "Sales VP" and it will fire on a title that happens to
  contain "principal" for an unrelated reason. Both directions are visible in the evidence
  line, which names the pattern that matched, so a wrong call is arguable rather than
  buried.
- **Corroboration is a judgment, not a measurement.** "A hire plus a delisting means the
  seat was filled" is a strong prior, not a fact. It is right more often than the v1 rule
  it replaced, which is the honest claim available.
- **One signal class only.** `vacancy_duration`. Positioning drift and segment-threshold
  drift are deliberately out of scope, and there is no plugin framework here.
- **Nothing is written to your source data.** The tool reads `state.json` and writes only
  its own ledger and output paths.

## Repo layout

```
config.json          falsifier registry, thresholds, schedule, function + single-seat maps
sdd/model.py         Signal, verdict constants, worst-wins
sdd/adapters.py      vacancy-monitor state.json -> signals
sdd/evidence.py      hire-observation sources (file, null)
sdd/checks.py        the three falsifier checks + the CHECKS registry
sdd/schedule.py      the ledger that makes this a re-check
sdd/engine.py        orchestration, worst-wins, fail-closed
sdd/report.py        human-readable rendering
sdd/cli.py           argument parsing
fixtures/            synthetic state + evidence, calibrated to 2026-09-10
tests.py             149 tests, deterministic, no network
docs/SPEC.md         scope, design decisions, prior art
.vibecodepm/         flow map and metrics definition
```

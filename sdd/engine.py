"""Orchestration: run a signal's falsifiers, combine them, fail closed.

Two rules govern everything here.

**Worst wins.** A signal is only as valid as its weakest falsifier. One broken
falsifier invalidates the signal no matter how many others hold.

**Nothing reaches VALID by silence.** A check that raises, a signal class with
no registered falsifiers, an evidence source that knows nothing — every one of
those produces SUSPECT with the reason attached. The tool exists because a
signal was trusted by default; it must never repeat that by default itself.
"""

from dataclasses import dataclass, field
from datetime import date

from .checks import FalsifierResult
from .model import INVALIDATED, SUSPECT, VALID, worst


@dataclass(frozen=True)
class FalsifierVerdict:
    """One falsifier's outcome for one signal."""

    name: str
    statement: str
    status: str
    evidence: str


@dataclass(frozen=True)
class SignalVerdict:
    """The whole answer for one signal, with its receipts."""

    signal_id: str
    signal_class: str
    company: str
    title: str
    age_days: int
    verdict: str
    checked_on: date
    falsifiers: tuple = ()
    url: str = ""
    note: str = ""
    carried_forward: bool = False

    @property
    def ranking_eligible(self):
        """Only a VALID signal may rank or enter a sequence."""
        return self.verdict == VALID

    def to_dict(self):
        return {
            "signal_id": self.signal_id,
            "signal_class": self.signal_class,
            "company": self.company,
            "title": self.title,
            "age_days": self.age_days,
            "verdict": self.verdict,
            "checked_on": self.checked_on.isoformat(),
            "ranking_eligible": self.ranking_eligible,
            "carried_forward": self.carried_forward,
            "url": self.url,
            "note": self.note,
            "falsifiers": [
                {
                    "name": f.name,
                    "statement": f.statement,
                    "status": f.status,
                    "evidence": f.evidence,
                }
                for f in self.falsifiers
            ],
        }


@dataclass(frozen=True)
class RunResult:
    """Every verdict from one pass, plus the two things a caller acts on."""

    as_of: date
    verdicts: tuple = ()

    @property
    def eligible_ids(self):
        """The signal ids a ranking stage may use. Everything else is held."""
        return [v.signal_id for v in self.verdicts if v.ranking_eligible]

    @property
    def summary(self):
        counts = {VALID: 0, SUSPECT: 0, INVALIDATED: 0}
        for verdict in self.verdicts:
            counts[verdict.verdict] = counts.get(verdict.verdict, 0) + 1
        return counts

    def to_dict(self):
        return {
            "as_of": self.as_of.isoformat(),
            "summary": self.summary,
            "eligible_ids": self.eligible_ids,
            "verdicts": [v.to_dict() for v in self.verdicts],
        }


def run(signals, config, context, ledger=None, force=False):
    """Check every due signal, carry the rest forward, record what was checked.

    A signal inside its class's recheck interval is not re-derived; its stored
    verdict is carried forward and marked, so a reader can tell a fresh answer
    from a remembered one.
    """
    verdicts = []
    for signal in signals:
        signal_class = config.signal_classes.get(signal.signal_class)
        interval = signal_class.recheck_interval_days if signal_class else 0

        if not force and ledger is not None and not ledger.is_due(
            signal.signal_id, interval, context.as_of
        ):
            entry = ledger.entry(signal.signal_id)
            verdicts.append(
                SignalVerdict(
                    signal_id=signal.signal_id,
                    signal_class=signal.signal_class,
                    company=signal.company,
                    title=signal.title,
                    age_days=signal.age_days,
                    verdict=entry["verdict"],
                    checked_on=entry["last_checked"],
                    url=signal.url,
                    note="inside the %d-day recheck interval" % interval,
                    carried_forward=True,
                )
            )
            continue

        verdict = evaluate_signal(signal, config, context)
        verdicts.append(verdict)
        if ledger is not None:
            ledger.record(signal.signal_id, verdict.verdict, verdict.checked_on)

    return RunResult(as_of=context.as_of, verdicts=tuple(verdicts))


def _corroborate(outcomes):
    """Apply conditional promotions, once, against the unpromoted results.

    Some evidence only means something alongside other evidence. A hire into a
    function is counterevidence to an unfilled-role reading; a hire PLUS a
    listing that stopped appearing is the ordinary signature of a filled seat.
    A check cannot see that, because it receives one signal and its own
    thresholds, so it hands up a conditional (``FalsifierResult.corroborated_by``)
    and this function resolves it. Combination already lives here, next to
    worst-wins, which is where cross-falsifier reasoning belongs.

    Two properties, both deliberate:

    **The trigger set is computed once, from the results as the checks returned
    them.** So a promotion can never itself trigger a further promotion, and the
    outcome does not depend on the order falsifiers are listed in.

    **Only INVALIDATED corroborates.** A SUSPECT sibling is itself an "I am not
    sure," and two unsure readings do not add up to a sure one.
    """
    broken = {
        falsifier.check_id
        for falsifier, outcome in outcomes
        if outcome.status == INVALIDATED
    }

    promoted = []
    for falsifier, outcome in outcomes:
        if outcome.corroborated_by:
            trigger, status, evidence = outcome.corroborated_by
            if trigger in broken:
                outcome = FalsifierResult(status, evidence)
        promoted.append((falsifier, outcome))
    return promoted


def evaluate_signal(signal, config, context):
    """Run every falsifier registered for this signal's class."""
    signal_class = config.signal_classes.get(signal.signal_class)

    if signal_class is None:
        return SignalVerdict(
            signal_id=signal.signal_id,
            signal_class=signal.signal_class,
            company=signal.company,
            title=signal.title,
            age_days=signal.age_days,
            verdict=SUSPECT,
            checked_on=context.as_of,
            url=signal.url,
            note=(
                "no falsifiers registered for signal class %r, so its validity "
                "cannot be checked" % signal.signal_class
            ),
        )

    outcomes = []
    for falsifier in signal_class.falsifiers:
        try:
            outcome = falsifier.check(signal, falsifier.thresholds, context)
        except Exception as exc:  # fail closed, never let one check kill the run
            outcome = FalsifierResult(
                SUSPECT,
                "check %r raised %s: %s"
                % (falsifier.check_id, type(exc).__name__, exc),
            )
        outcomes.append((falsifier, outcome))

    results = [
        FalsifierVerdict(
            name=falsifier.name,
            statement=falsifier.statement,
            status=outcome.status,
            evidence=outcome.evidence,
        )
        for falsifier, outcome in _corroborate(outcomes)
    ]

    return SignalVerdict(
        signal_id=signal.signal_id,
        signal_class=signal.signal_class,
        company=signal.company,
        title=signal.title,
        age_days=signal.age_days,
        verdict=worst(r.status for r in results),
        checked_on=context.as_of,
        falsifiers=tuple(results),
        url=signal.url,
    )

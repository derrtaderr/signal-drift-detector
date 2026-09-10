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

from .model import SUSPECT, VALID, worst


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

    results = []
    for falsifier in signal_class.falsifiers:
        try:
            outcome = falsifier.check(signal, falsifier.thresholds, context)
            status, evidence = outcome.status, outcome.evidence
        except Exception as exc:  # fail closed, never let one check kill the run
            status = SUSPECT
            evidence = "check %r raised %s: %s" % (
                falsifier.check_id,
                type(exc).__name__,
                exc,
            )
        results.append(
            FalsifierVerdict(
                name=falsifier.name,
                statement=falsifier.statement,
                status=status,
                evidence=evidence,
            )
        )

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

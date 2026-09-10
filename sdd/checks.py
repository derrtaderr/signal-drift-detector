"""Falsifier check implementations.

A check answers one question: does this falsifier still hold for this signal?
It returns a status plus the evidence that produced it. Checks never raise for
ordinary "I could not tell" conditions; they return SUSPECT with a reason. The
engine treats an unexpected exception as SUSPECT too, so the fail-closed rule
holds even for a check that misbehaves.

Which falsifiers apply to which signal class, and at what thresholds, is data
(``config.json``). Only the implementations live here, keyed by check id.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .model import INVALIDATED, SUSPECT, VALID


@dataclass
class CheckContext:
    """Everything a check may need beyond the signal itself."""

    as_of: date
    evidence: Optional[object] = None
    function_map: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FalsifierResult:
    status: str
    evidence: str


def check_age_ceiling(signal, thresholds, context):
    """Falsifier: the posting's age is below the point where an unfilled
    posting is more likely evergreen recruiting than a live vacancy.

    A duration signal's score climbs with age, so this is the falsifier that
    turns the signal's own strength into a reason to doubt it.
    """
    suspect_after = thresholds.get("suspect_after_days")
    invalidate_after = thresholds.get("invalidate_after_days")
    age = signal.age_days

    if invalidate_after is not None and age >= invalidate_after:
        return FalsifierResult(
            INVALIDATED,
            "posting is %d days old, at or past the %d-day evergreen ceiling"
            % (age, invalidate_after),
        )
    if suspect_after is not None and age >= suspect_after:
        return FalsifierResult(
            SUSPECT,
            "posting is %d days old, past the %d-day evergreen warning line"
            % (age, suspect_after),
        )
    return FalsifierResult(
        VALID, "posting is %d days old, inside the evergreen ceiling" % age
    )

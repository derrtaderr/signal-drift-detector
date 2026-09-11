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


def map_function(title, function_map):
    """Map a posting title to a function slug, or None if it does not map.

    A hire only falsifies a posting if it lands in the SAME function, so this
    scoping is what keeps `no_hires_since_posting` honest. Returning None rather
    than guessing is deliberate: an unmappable title means the check cannot be
    scoped, and the caller must fail closed rather than compare against
    everything.
    """
    haystack = (title or "").lower()
    for function, keywords in function_map.items():
        for keyword in keywords:
            if keyword.lower() in haystack:
                return function
    return None


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


def check_no_hires_since_posting(signal, thresholds, context):
    """Falsifier: no hires have been observed into this function at this
    company since the posting date.

    This is the check that makes the worked case designed rather than lucky. A
    role open 192 days looks like a capability vacuum right up until you learn
    the company hired into that exact function three months in, at which point
    the posting reads as evergreen recruiting and the signal is dead.

    Fails closed in three distinct ways, each reported with its own reason:
    no evidence source, no observations for the company, or a title that cannot
    be scoped to a function.
    """
    source = context.evidence
    if source is None:
        return FalsifierResult(
            SUSPECT, "no evidence source configured, so the hire check could not run"
        )

    function = map_function(signal.title, context.function_map)
    if function is None:
        return FalsifierResult(
            SUSPECT,
            "title %r does not map to a known function, so hires cannot be "
            "scoped to it" % signal.title,
        )

    record = source.observations(signal.company)
    if record is None:
        return FalsifierResult(
            SUSPECT,
            "no hire observations on record for %s, so nobody has looked"
            % signal.company,
        )

    since = [
        hire
        for hire in record.get("hires", [])
        if hire.get("function") == function and hire.get("date") > signal.date_posted
    ]
    if since:
        hire = min(since, key=lambda h: h["date"])
        return FalsifierResult(
            INVALIDATED,
            "hire into %s at %s on %s (%s), after the posting went up on %s"
            % (
                function,
                signal.company,
                hire["date"].isoformat(),
                hire.get("source") or "source unrecorded",
                signal.date_posted.isoformat(),
            ),
        )

    checked_through = record.get("checked_through")
    return FalsifierResult(
        VALID,
        "no hires into %s at %s since %s%s"
        % (
            function,
            signal.company,
            signal.date_posted.isoformat(),
            (
                ", observations current through %s" % checked_through.isoformat()
                if checked_through
                else ""
            ),
        ),
    )


def check_posting_still_listed(signal, thresholds, context):
    """Falsifier: the posting was still listed on the most recent source scrape.

    A posting that quietly stopped appearing was filled or pulled. Its age in
    ``state.json`` keeps climbing either way, which is the blindness this
    falsifier covers.
    """
    suspect_after = thresholds.get("suspect_after_days")
    invalidate_after = thresholds.get("invalidate_after_days")
    missing_for = (context.as_of - signal.last_seen).days

    if invalidate_after is not None and missing_for >= invalidate_after:
        return FalsifierResult(
            INVALIDATED,
            "not seen on a scrape for %d days, past the %d-day limit"
            % (missing_for, invalidate_after),
        )
    if suspect_after is not None and missing_for >= suspect_after:
        return FalsifierResult(
            SUSPECT,
            "not seen on a scrape for %d days, past the %d-day warning line"
            % (missing_for, suspect_after),
        )
    return FalsifierResult(
        VALID, "seen on a scrape %d days ago" % missing_for
    )


#: Check implementations, keyed by the ``check`` id a config entry names.
#: Adding a falsifier means adding an implementation here and an entry in
#: config.json; a config naming an id absent from this table is a load error.
CHECKS = {
    "age_ceiling": check_age_ceiling,
    "no_hires_since_posting": check_no_hires_since_posting,
    "posting_still_listed": check_posting_still_listed,
}

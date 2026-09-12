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
    #: Title keywords that name ONE chair. See :func:`match_single_seat`.
    single_seat_patterns: tuple = ()


@dataclass(frozen=True)
class FalsifierResult:
    status: str
    evidence: str
    #: An optional conditional promotion, ``(trigger_check_id, status,
    #: evidence)``. A check sees one signal and its own thresholds; it cannot
    #: see what a sibling falsifier concluded, and handing it that view would
    #: make the order falsifiers are listed in significant. So a check that
    #: would reach a different verdict *if* another falsifier broke says so
    #: here, and the engine -- which already owns combination -- applies it
    #: after every falsifier has run. Empty means the result is final.
    corroborated_by: tuple = ()


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


def match_single_seat(title, patterns):
    """Return the single-seat pattern this title matches, or None.

    A single-seat title names ONE chair: "Founding GTM Engineer", "Head of
    Revenue Operations", "VP of Sales". Nobody hires two founding GTM
    engineers, so a hire into that function fills the posting rather than
    expanding around it. That is the one thing a title alone can corroborate.

    "GTM Engineer" is NOT single-seat. It could be one chair or five, and that
    ambiguity is exactly why a hire against it cannot invalidate a signal.

    Returning the matched pattern rather than True is deliberate: the evidence
    line names which pattern fired, so the operator can disagree with that
    pattern specifically and edit it in config.
    """
    haystack = (title or "").lower()
    for pattern in patterns or ():
        if pattern.lower() in haystack:
            return pattern
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
    if source is None or not getattr(source, "configured", True):
        # Either no source at all, or a stand-in for one the operator never
        # supplied. Both are facts about the run, not about the company, so the
        # reason must not name a company.
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
        return _report_hire(signal, function, since, context)

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


def _report_hire(signal, function, since, context):
    """Grade an observed hire. This is the epistemics of the whole tool.

    A hire into the function, dated after the posting, is **counterevidence to
    the unfilled-role reading**. It is not proof the vacancy is gone. One hire
    cannot distinguish a seat that got filled from a team that is still
    expanding: "3 Senior GTM Engineers" is one posting and three chairs, and a
    company scaling a function posts once and hires five times.

    v1 read any such hire as proof and went straight to INVALIDATED, which is a
    tool claiming to know more than its evidence supports. That is the failure
    this tool exists to catch, so it does not get to commit it.

    So the hire degrades the falsifier to SUSPECT -- held for review, never
    ranked -- and only reaches INVALIDATED when a second, independent
    observation agrees:

    * **corroborated-by-single-seat-title** -- the title names one chair, so a
      hire into the function took it. Decided here; the title is on the signal.
    * **corroborated-by-delisting** -- the posting also stopped appearing on
      scrapes. Decided by the ENGINE, because a check cannot see a sibling
      falsifier's outcome, so this result carries the conditional instead.

    Counting hires is not corroborating. Three hires into a function is the
    *expansion* reading, not the filled one; escalating past some hire count
    would re-introduce the v1 error with an arbitrary number attached. The count
    goes in the evidence so a human can weigh it, and the verdict stays SUSPECT.
    """
    earliest = min(since, key=lambda h: h["date"])
    observed = "hire into %s at %s on %s (%s), after the posting went up on %s" % (
        function,
        signal.company,
        earliest["date"].isoformat(),
        earliest.get("source") or "source unrecorded",
        signal.date_posted.isoformat(),
    )
    if len(since) > 1:
        observed += " (%d hires into %s recorded since the posting; the earliest " \
            "is shown, and a count is not corroboration)" % (len(since), function)

    pattern = match_single_seat(signal.title, context.single_seat_patterns)
    if pattern is not None:
        return FalsifierResult(
            INVALIDATED,
            "%s. corroborated-by-single-seat-title: %r matches the single-seat "
            "pattern %r, a title that names one chair, so the hire took it"
            % (observed, signal.title, pattern),
        )

    return FalsifierResult(
        SUSPECT,
        "%s. uncorroborated-hence-suspect: one hire cannot distinguish a filled "
        "seat from a team still expanding, so this is counterevidence to the "
        "unfilled-role reading rather than proof the vacancy is gone" % observed,
        corroborated_by=(
            "posting_still_listed",
            INVALIDATED,
            "%s. corroborated-by-delisting: the posting also stopped appearing "
            "on scrapes in this run, and a hire into the function plus a "
            "listing that went away is the ordinary signature of a filled seat"
            % observed,
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

"""Core data types."""

from dataclasses import dataclass
from datetime import date


class DriftError(Exception):
    """Raised for input the tool cannot proceed on (bad config, bad state file)."""


@dataclass(frozen=True)
class Signal:
    """One signal whose validity can be falsified.

    v1 carries exactly one class, ``vacancy_duration``: a posting whose score
    climbs with age.
    """

    signal_id: str
    signal_class: str
    company: str
    title: str
    date_posted: date
    last_seen: date
    age_days: int
    url: str = ""


def parse_date(value, fieldname="date"):
    """Parse an ISO ``YYYY-MM-DD`` string into a date, or raise DriftError."""
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise DriftError("%s is not a date string: %r" % (fieldname, value))
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        raise DriftError("%s is not an ISO YYYY-MM-DD date: %r" % (fieldname, value))

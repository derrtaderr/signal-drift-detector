"""Adapters turn a source's own state into Signal objects.

v1 ships exactly one: vacancy-monitor's ``state.json``.
"""

import json
from datetime import date

from .model import DriftError, Signal, parse_date

SIGNAL_CLASS = "vacancy_duration"


def load_vacancy_signals(path, as_of=None):
    """Read a vacancy-monitor-shaped state.json into duration signals.

    The shape (verified against the live file, contents never copied): a
    top-level object keyed by posting id, each value carrying ``company``,
    ``title``, ``date_posted``, ``job_url``, ``company_url``, ``first_seen``,
    ``reported`` and ``last_seen``.
    """
    as_of = as_of or date.today()

    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        raise DriftError("state file not found: %s" % path)
    except json.JSONDecodeError as exc:
        raise DriftError("state file is not valid JSON (%s): %s" % (path, exc))

    if not isinstance(raw, dict):
        raise DriftError(
            "state file must be an object keyed by posting id, got %s: %s"
            % (type(raw).__name__, path)
        )

    signals = []
    for signal_id, record in raw.items():
        if not isinstance(record, dict):
            raise DriftError(
                "posting %s is not an object, got %s" % (signal_id, type(record).__name__)
            )
        for required in ("company", "title", "date_posted"):
            if required not in record:
                raise DriftError("posting %s is missing %r" % (signal_id, required))

        posted = parse_date(record["date_posted"], "posting %s date_posted" % signal_id)
        last_seen = parse_date(
            record.get("last_seen", record["date_posted"]),
            "posting %s last_seen" % signal_id,
        )
        signals.append(
            Signal(
                signal_id=signal_id,
                signal_class=SIGNAL_CLASS,
                company=record["company"],
                title=record["title"],
                date_posted=posted,
                last_seen=last_seen,
                age_days=(as_of - posted).days,
                url=record.get("job_url", ""),
            )
        )
    return signals

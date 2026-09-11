"""The schedule, and the ledger that drives it.

A falsifier checked once is a filter. A falsifier checked on a schedule is a
drift detector, and the difference is this file.

The ledger records when each signal was last checked and what the verdict was.
It is a cache and never a source of truth: missing, unreadable or partial all
mean "re-check", which is the fail-closed direction. The alternative — trusting
a stale ledger — would recreate the exact blindness the tool is built to close.
"""

import json
import os

from .model import INVALIDATED, SUSPECT, VALID, DriftError, parse_date

#: The only verdicts a cached row may carry. A row holding anything else --
#: a missing key, a typo, a value from a future version -- is unreadable, and
#: an unreadable row is a re-check. Carrying one forward would put a verdict in
#: the header that matches no report section, so the signal would be counted
#: and never shown, which is the silent disappearance this tool exists to stop.
KNOWN_VERDICTS = frozenset((VALID, SUSPECT, INVALIDATED))


class Ledger:
    """Last-checked dates and verdicts, keyed by signal id."""

    def __init__(self, path=None, entries=None):
        self.path = path
        self._entries = entries or {}

    @classmethod
    def load(cls, path):
        if not path or not os.path.exists(path):
            return cls(path=path)

        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
            signals = raw.get("signals") or {}
        except (json.JSONDecodeError, AttributeError, OSError):
            # Unreadable ledger means everything is due. Never fatal.
            return cls(path=path)

        if not isinstance(signals, dict):
            # Right key, wrong shape. Still just an unusable cache.
            return cls(path=path)

        entries = {}
        for signal_id, entry in signals.items():
            try:
                verdict = entry.get("verdict")
                if verdict not in KNOWN_VERDICTS:
                    continue  # partial or unrecognized: discard, re-check
                entries[signal_id] = {
                    "last_checked": parse_date(entry["last_checked"]),
                    "verdict": verdict,
                }
            except Exception:
                continue  # a bad row is a re-check, not a crash
        return cls(path=path, entries=entries)

    def entry(self, signal_id):
        return self._entries.get(signal_id)

    def is_due(self, signal_id, interval_days, as_of):
        entry = self.entry(signal_id)
        if entry is None:
            return True
        if entry.get("verdict") not in KNOWN_VERDICTS:
            # Belt and braces for a Ledger built in-process rather than loaded.
            # Nothing may be carried forward that the report cannot place.
            return True
        return (as_of - entry["last_checked"]).days >= interval_days

    def record(self, signal_id, verdict, checked_on):
        self._entries[signal_id] = {
            "last_checked": checked_on,
            "verdict": verdict,
        }

    def save(self, path=None):
        target = path or self.path
        if not target:
            return
        payload = {
            "version": 1,
            "signals": {
                signal_id: {
                    "last_checked": entry["last_checked"].isoformat(),
                    "verdict": entry["verdict"],
                }
                for signal_id, entry in sorted(self._entries.items())
            },
        }
        try:
            directory = os.path.dirname(os.path.abspath(target))
            if directory and not os.path.isdir(directory):
                os.makedirs(directory, exist_ok=True)
            with open(target, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
        except OSError as exc:
            raise DriftError("ledger could not be written (%s): %s" % (target, exc))

"""Config loading.

The falsifier registry, the recheck schedule, the thresholds and the function
map are data. This module reads them and resolves each falsifier's ``check`` id
against the implementation table in :mod:`sdd.checks`.

Validation is strict and happens at load. A config naming a check that does not
exist, or a signal class naming a falsifier that is not defined, is an error
rather than a silently skipped check. A skipped check would read as VALID
downstream, which is the one outcome this tool must never produce by accident.
"""

import json
import os
from dataclasses import dataclass, field

from .checks import CHECKS
from .model import DriftError

#: Resolved from the package location, so a clean clone finds its config no
#: matter which directory the CLI is invoked from.
DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json"
)


@dataclass(frozen=True)
class Falsifier:
    """One falsifiable claim, plus the check that tests it."""

    name: str
    check_id: str
    check: object
    statement: str
    thresholds: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SignalClass:
    name: str
    description: str
    recheck_interval_days: int
    falsifiers: tuple


@dataclass(frozen=True)
class Config:
    falsifiers: dict
    signal_classes: dict
    function_map: dict
    path: str = ""


def load_config(path=None):
    """Read and validate a config file."""
    path = path or DEFAULT_CONFIG_PATH

    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        raise DriftError("config file not found: %s" % path)
    except json.JSONDecodeError as exc:
        raise DriftError("config file is not valid JSON (%s): %s" % (path, exc))

    falsifiers = {}
    for name, entry in (raw.get("falsifiers") or {}).items():
        check_id = entry.get("check")
        if check_id not in CHECKS:
            raise DriftError(
                "falsifier %r names check %r, which is not implemented. "
                "Known checks: %s" % (name, check_id, ", ".join(sorted(CHECKS)))
            )
        statement = entry.get("statement")
        if not statement:
            raise DriftError(
                "falsifier %r has no statement. A claim nobody can read and "
                "disagree with is not a falsifier." % name
            )
        falsifiers[name] = Falsifier(
            name=name,
            check_id=check_id,
            check=CHECKS[check_id],
            statement=statement,
            thresholds=entry.get("thresholds") or {},
        )

    signal_classes = {}
    for name, entry in (raw.get("signal_classes") or {}).items():
        names = entry.get("falsifiers") or []
        resolved = []
        for falsifier_name in names:
            if falsifier_name not in falsifiers:
                raise DriftError(
                    "signal class %r names falsifier %r, which is not defined"
                    % (name, falsifier_name)
                )
            resolved.append(falsifiers[falsifier_name])
        if not resolved:
            raise DriftError(
                "signal class %r has no falsifiers. An unfalsifiable class would "
                "pass every run by default." % name
            )
        signal_classes[name] = SignalClass(
            name=name,
            description=entry.get("description", ""),
            recheck_interval_days=int(entry.get("recheck_interval_days", 7)),
            falsifiers=tuple(resolved),
        )

    return Config(
        falsifiers=falsifiers,
        signal_classes=signal_classes,
        function_map=raw.get("function_map") or {},
        path=path,
    )

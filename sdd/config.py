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


def _require_mapping(value, what, path):
    """Return ``value`` as a dict, or refuse the input naming what was wrong.

    An absent section is an empty one. A section of the wrong TYPE is an error,
    never an empty one: ``"falsifiers": []`` silently registering zero
    falsifiers is the same silent-skip this module exists to reject.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise DriftError(
            "%s must be an object in %s, got %s" % (what, path, type(value).__name__)
        )
    return value


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
    except OSError as exc:
        raise DriftError("config file could not be read (%s): %s" % (path, exc))

    raw = _require_mapping(raw, "config", path)

    falsifiers = {}
    for name, entry in _require_mapping(
        raw.get("falsifiers"), "falsifiers", path
    ).items():
        if not isinstance(entry, dict):
            raise DriftError(
                "falsifier %r must be an object, got %s" % (name, type(entry).__name__)
            )
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
        if "thresholds" in entry and entry["thresholds"] is None:
            # An ABSENT thresholds key means "this check takes none", and {} is
            # the same thing written out. An explicit null is different: it
            # reads as a deliberate setting, but every threshold lookup then
            # misses and the check reports VALID for input it should have
            # caught. Disarming a falsifier must never look like configuring it.
            raise DriftError(
                "falsifier %r has thresholds set to null, which would disarm "
                "its check silently. Omit the key, or use {}, to mean no "
                "thresholds." % name
            )

        falsifiers[name] = Falsifier(
            name=name,
            check_id=check_id,
            check=CHECKS[check_id],
            statement=statement,
            thresholds=_require_mapping(
                entry.get("thresholds"), "thresholds for falsifier %r" % name, path
            ),
        )

    signal_classes = {}
    for name, entry in _require_mapping(
        raw.get("signal_classes"), "signal_classes", path
    ).items():
        if not isinstance(entry, dict):
            raise DriftError(
                "signal class %r must be an object, got %s"
                % (name, type(entry).__name__)
            )
        names = entry.get("falsifiers") or []
        if not isinstance(names, list):
            raise DriftError(
                "signal class %r must list its falsifiers as an array, got %s"
                % (name, type(names).__name__)
            )
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
        interval = entry.get("recheck_interval_days", 7)
        try:
            interval = int(interval)
        except (TypeError, ValueError):
            raise DriftError(
                "signal class %r has a non-integer recheck_interval_days: %r"
                % (name, interval)
            )

        signal_classes[name] = SignalClass(
            name=name,
            description=entry.get("description", ""),
            recheck_interval_days=interval,
            falsifiers=tuple(resolved),
        )

    function_map = _require_mapping(raw.get("function_map"), "function_map", path)
    for function, keywords in function_map.items():
        # The map itself being an object is not enough. map_function iterates
        # each VALUE, so a bare string is scanned character by character and
        # matches almost any title -- scoping the hire check to a function
        # nobody chose and manufacturing an affirmative "no hires into X".
        if not isinstance(keywords, list):
            raise DriftError(
                "function_map entry %r must be a list of keywords, got %s.%s"
                % (
                    function,
                    type(keywords).__name__,
                    (
                        " A bare string is matched one character at a time, so "
                        "nearly every title would map to it."
                        if isinstance(keywords, str)
                        else ""
                    ),
                )
            )
        for keyword in keywords:
            if not isinstance(keyword, str) or not keyword.strip():
                raise DriftError(
                    "function_map entry %r has a keyword that is not a "
                    "non-empty string: %r.%s"
                    % (
                        function,
                        keyword,
                        (
                            " An empty keyword matches every title."
                            if isinstance(keyword, str)
                            else ""
                        ),
                    )
                )

    return Config(
        falsifiers=falsifiers,
        signal_classes=signal_classes,
        function_map=function_map,
        path=path,
    )

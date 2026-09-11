"""Command line entry point.

    python3 -m sdd check --state path/to/state.json [--evidence path] [options]

Every input is an argument. Nothing about any one machine is baked in, which is
what lets a clean clone run this against its own data on the first try.
"""

import argparse
import json
import sys
from datetime import date

from .adapters import load_vacancy_signals
from .checks import CheckContext
from .config import DEFAULT_CONFIG_PATH, load_config
from .engine import run
from .evidence import FileEvidenceSource, NullEvidenceSource
from .model import DriftError, parse_date
from .report import render
from .schedule import Ledger


def build_parser():
    parser = argparse.ArgumentParser(
        prog="sdd",
        description=(
            "Re-check the falsifiers under duration signals before they rank. "
            "A signal whose falsifier broke is INVALIDATED; one that cannot be "
            "confirmed is SUSPECT. Neither is eligible to rank."
        ),
    )
    sub = parser.add_subparsers(dest="command")

    check = sub.add_parser("check", help="check a state file and emit verdicts")
    check.add_argument(
        "--state",
        required=True,
        help="path to a vacancy-monitor-shaped state.json",
    )
    check.add_argument(
        "--evidence",
        default=None,
        help=(
            "path to a hire-observation JSON file. Without it the hire "
            "falsifier cannot run and every signal fails closed to SUSPECT."
        ),
    )
    check.add_argument(
        "--config", default=DEFAULT_CONFIG_PATH, help="path to config.json"
    )
    check.add_argument(
        "--ledger",
        default=None,
        help="path to the schedule ledger. Without it every signal is re-checked.",
    )
    check.add_argument(
        "--as-of",
        default=None,
        help="treat this ISO date as today (used for reproducible runs)",
    )
    check.add_argument(
        "--force", action="store_true", help="re-check even signals not yet due"
    )
    check.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="output format on stdout",
    )
    check.add_argument(
        "--ranking-only",
        action="store_true",
        help="print only the ids that may rank, one per line",
    )
    check.add_argument(
        "--show-valid-detail",
        action="store_true",
        help="print every falsifier for VALID signals too",
    )
    check.add_argument(
        "--out", default=None, help="also write the JSON verdicts to this path"
    )
    return parser


def main(argv=None, stdout=None, stderr=None):
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command != "check":
        parser.print_help(stdout)
        return 1

    try:
        as_of = parse_date(args.as_of) if args.as_of else date.today()
        config = load_config(args.config)
        signals = load_vacancy_signals(args.state, as_of=as_of)
        evidence = (
            FileEvidenceSource(args.evidence) if args.evidence else NullEvidenceSource()
        )
        ledger = Ledger.load(args.ledger) if args.ledger else None

        context = CheckContext(
            as_of=as_of, evidence=evidence, function_map=config.function_map
        )
        result = run(signals, config, context, ledger=ledger, force=args.force)

        if ledger is not None:
            ledger.save()
        if args.out:
            try:
                with open(args.out, "w", encoding="utf-8") as handle:
                    json.dump(result.to_dict(), handle, indent=2)
                    handle.write("\n")
            except OSError as exc:
                # Refuse before anything reaches stdout. A run that could not
                # write where it was told must not also look like it succeeded.
                raise DriftError(
                    "could not write --out file (%s): %s" % (args.out, exc)
                )
    except DriftError as exc:
        stderr.write("error: %s\n" % exc)
        return 2

    if args.ranking_only:
        for signal_id in result.eligible_ids:
            stdout.write("%s\n" % signal_id)
    elif args.format == "json":
        stdout.write(json.dumps(result.to_dict(), indent=2))
        stdout.write("\n")
    else:
        stdout.write(render(result, show_valid_detail=args.show_valid_detail))
        stdout.write("\n")

    return 0

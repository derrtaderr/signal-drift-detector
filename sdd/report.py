"""Human-readable rendering of a run.

The reader of this report is deciding whether to disagree with a verdict, which
means a bare label is useless to them. Every entry carries the falsifier's
statement (the claim being made) next to the evidence (what moved it), so
disagreeing is a matter of reading two lines rather than re-deriving the check.

Order is worst first. The signals a sourcing list would have ranked highest are
exactly the ones most likely to be dead, so they lead.
"""

from .model import INVALIDATED, SUSPECT, VALID

_STATUS_MARK = {VALID: "ok", SUSPECT: "??", INVALIDATED: "XX"}

_SECTION_BLURB = {
    INVALIDATED: "A falsifier broke. Excluded from ranking and from any sequence.",
    SUSPECT: "A falsifier is degraded or could not be checked. Held for review.",
    VALID: "Every falsifier still holds. Eligible to rank.",
}


def render(result, show_valid_detail=False):
    """Render a RunResult as plain text."""
    summary = result.summary
    lines = []
    lines.append("signal drift check — %s" % result.as_of.isoformat())
    lines.append("=" * 60)
    lines.append(
        "%d VALID   %d SUSPECT   %d INVALIDATED   (%d signals checked)"
        % (
            summary.get(VALID, 0),
            summary.get(SUSPECT, 0),
            summary.get(INVALIDATED, 0),
            len(result.verdicts),
        )
    )
    lines.append("")

    for status in (INVALIDATED, SUSPECT, VALID):
        group = [v for v in result.verdicts if v.verdict == status]
        if not group:
            continue
        lines.append("%s — %s" % (status, _SECTION_BLURB[status]))
        lines.append("-" * 60)
        for verdict in sorted(group, key=lambda v: -v.age_days):
            lines.extend(_render_signal(verdict, status, show_valid_detail))
        lines.append("")

    eligible = result.eligible_ids
    lines.append(
        "%d of %d signals may rank." % (len(eligible), len(result.verdicts))
    )
    return "\n".join(lines)


def _render_signal(verdict, status, show_valid_detail):
    lines = [
        "  [%s] %s — %s (%d days old, %s)"
        % (
            _STATUS_MARK.get(status, "??"),
            verdict.company,
            verdict.title,
            verdict.age_days,
            verdict.signal_id,
        )
    ]

    if verdict.carried_forward:
        lines.append(
            "      carried forward from %s, %s"
            % (verdict.checked_on.isoformat(), verdict.note)
        )
        lines.append("")
        return lines

    if verdict.note:
        lines.append("      %s" % verdict.note)

    for falsifier in verdict.falsifiers:
        if falsifier.status == VALID and status != VALID and not show_valid_detail:
            continue
        if status == VALID and not show_valid_detail:
            continue
        lines.append("      %s: %s" % (falsifier.status, falsifier.statement))
        lines.append("        %s" % falsifier.evidence)

    if verdict.url:
        lines.append("      %s" % verdict.url)
    lines.append("")
    return lines

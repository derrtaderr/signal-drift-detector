# Wiring

This tool is consumed by a private operations repo. The integration below was designed as
exact old-to-new file edits, proposed by the build lane without touching anything outside its
own worktree, reviewed, and **applied 2026-09-10** by the orchestrating session: a weekly
sourcing pass now runs the drift check over the tracked-postings state file before any
crossing can propose paid enrichment, and the signal source's docs point back here. The
verbatim edit text lived in this file's history and was trimmed when the repo went public,
since it quoted a private repo's internals; the design reasoning below is kept because it is
the useful part.

## Not proposed, and why

**Calling the drift check from `monitor.py` directly.** Rejected. The monitor's job is to
track ages; making it also arbitrate validity couples two tools that release on different
schedules, and it would put an import of this repo inside a repo that has no dependency on
it. `/weekly` is the right seam, since it already orchestrates both.

**Writing verdicts back into `state.json`.** Rejected. This tool never writes to its source,
and a verdict written into the source would be read on the next run as if it were source
data.

**A `read_by:` frontmatter line on anything in this repo.** The vault rule applies to vault
artifacts. This repo's own docs are read from the repo, and `docs/SPEC.md`,
`.vibecodepm/flow.md` and `.vibecodepm/metrics.md` each name their reader in frontmatter or
in the README's layout table.

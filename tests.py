"""Test suite for signal-drift-detector.

Deterministic and keyless. No network calls. Run:

    python3 tests.py
"""

import os
import shutil
import tempfile
import unittest
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures")
SAMPLE_STATE = os.path.join(FIXTURES, "state.sample.json")
SAMPLE_EVIDENCE = os.path.join(FIXTURES, "evidence.sample.json")

# The date the fixtures are calibrated against. The golden case is 192 days old
# as of this date, matching the worked case the build comes from.
AS_OF = date(2026, 9, 10)


class TestVacancyAdapter(unittest.TestCase):
    def test_reads_vacancy_monitor_state_into_duration_signals(self):
        from sdd.adapters import load_vacancy_signals

        signals = load_vacancy_signals(SAMPLE_STATE, as_of=AS_OF)

        self.assertEqual(len(signals), 5)
        golden = [s for s in signals if s.signal_id == "li-9000000001"][0]
        self.assertEqual(golden.signal_class, "vacancy_duration")
        self.assertEqual(golden.company, "Northwind Analytics")
        self.assertEqual(golden.title, "GTM Engineer")
        self.assertEqual(golden.date_posted, date(2026, 3, 2))
        self.assertEqual(golden.last_seen, date(2026, 9, 9))
        self.assertEqual(golden.age_days, 192)


class TestVacancyAdapterRejectsMalformedInput(unittest.TestCase):
    """A malformed source file must fail loudly, never yield a partial list."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, text):
        path = os.path.join(self.tmp, "state.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_missing_state_file_names_the_path(self):
        from sdd.adapters import load_vacancy_signals
        from sdd.model import DriftError

        missing = os.path.join(self.tmp, "nope.json")
        with self.assertRaises(DriftError) as ctx:
            load_vacancy_signals(missing, as_of=AS_OF)
        self.assertIn(missing, str(ctx.exception))

    def test_unparseable_json_reports_the_file_not_a_traceback(self):
        from sdd.adapters import load_vacancy_signals
        from sdd.model import DriftError

        path = self._write("{ this is not json ")
        with self.assertRaises(DriftError) as ctx:
            load_vacancy_signals(path, as_of=AS_OF)
        self.assertIn("not valid JSON", str(ctx.exception))

    def test_top_level_array_is_rejected(self):
        from sdd.adapters import load_vacancy_signals
        from sdd.model import DriftError

        path = self._write('[{"company": "Acme"}]')
        with self.assertRaises(DriftError) as ctx:
            load_vacancy_signals(path, as_of=AS_OF)
        self.assertIn("keyed by posting id", str(ctx.exception))

    def test_posting_missing_a_required_field_names_the_posting_and_field(self):
        from sdd.adapters import load_vacancy_signals
        from sdd.model import DriftError

        path = self._write('{"li-1": {"company": "Acme", "title": "GTM Engineer"}}')
        with self.assertRaises(DriftError) as ctx:
            load_vacancy_signals(path, as_of=AS_OF)
        self.assertIn("li-1", str(ctx.exception))
        self.assertIn("date_posted", str(ctx.exception))

    def test_non_iso_date_is_rejected(self):
        from sdd.adapters import load_vacancy_signals
        from sdd.model import DriftError

        path = self._write(
            '{"li-1": {"company": "Acme", "title": "GTM Engineer",'
            ' "date_posted": "March 2nd"}}'
        )
        with self.assertRaises(DriftError) as ctx:
            load_vacancy_signals(path, as_of=AS_OF)
        self.assertIn("li-1", str(ctx.exception))


def make_signal(**overrides):
    """A vacancy_duration signal, aged relative to AS_OF unless overridden."""
    from sdd.model import Signal

    posted = overrides.pop("date_posted", date(2026, 3, 2))
    fields = dict(
        signal_id="li-test",
        signal_class="vacancy_duration",
        company="Northwind Analytics",
        title="GTM Engineer",
        date_posted=posted,
        last_seen=date(2026, 9, 9),
        age_days=(AS_OF - posted).days,
        url="https://example.invalid/jobs/test",
    )
    fields.update(overrides)
    return Signal(**fields)


class TestAgeCeilingFalsifier(unittest.TestCase):
    """Falsifier: the posting's age is below the point where an unfilled posting
    is more likely evergreen recruiting than a live vacancy."""

    THRESHOLDS = {"suspect_after_days": 120, "invalidate_after_days": 180}

    def _run(self, signal):
        from sdd.checks import check_age_ceiling
        from sdd.checks import CheckContext

        return check_age_ceiling(
            signal, self.THRESHOLDS, CheckContext(as_of=AS_OF)
        )

    def test_young_posting_holds(self):
        from sdd.model import VALID

        result = self._run(make_signal(date_posted=date(2026, 8, 20)))
        self.assertEqual(result.status, VALID)

    def test_posting_past_the_suspect_threshold_is_suspect(self):
        from sdd.model import SUSPECT

        result = self._run(make_signal(date_posted=date(2026, 4, 20)))
        self.assertEqual(result.status, SUSPECT)

    def test_golden_case_192_days_is_invalidated(self):
        from sdd.model import INVALIDATED

        result = self._run(make_signal(date_posted=date(2026, 3, 2)))
        self.assertEqual(result.status, INVALIDATED)

    def test_evidence_names_the_age_and_the_threshold_it_crossed(self):
        result = self._run(make_signal(date_posted=date(2026, 3, 2)))
        self.assertIn("192", result.evidence)
        self.assertIn("180", result.evidence)


class TestEvidenceSources(unittest.TestCase):
    """v1 consumes hire observations, it does not gather them. The source is
    pluggable so a future adapter can replace the file without touching checks."""

    def test_file_source_returns_observations_for_a_known_company(self):
        from sdd.evidence import FileEvidenceSource

        source = FileEvidenceSource(SAMPLE_EVIDENCE)
        record = source.observations("Northwind Analytics")

        self.assertIsNotNone(record)
        self.assertEqual(len(record["hires"]), 1)
        self.assertEqual(record["hires"][0]["function"], "gtm")
        self.assertEqual(record["hires"][0]["date"], date(2026, 6, 14))

    def test_file_source_returns_none_for_a_company_never_looked_at(self):
        from sdd.evidence import FileEvidenceSource

        source = FileEvidenceSource(SAMPLE_EVIDENCE)
        self.assertIsNone(source.observations("Peregrine Labs"))

    def test_file_source_distinguishes_looked_and_found_nothing_from_unknown(self):
        from sdd.evidence import FileEvidenceSource

        source = FileEvidenceSource(SAMPLE_EVIDENCE)
        record = source.observations("Cobalt Systems")

        self.assertIsNotNone(record)
        self.assertEqual(record["hires"], [])

    def test_missing_evidence_file_is_a_hard_error_not_a_silent_empty(self):
        from sdd.evidence import FileEvidenceSource
        from sdd.model import DriftError

        with self.assertRaises(DriftError):
            FileEvidenceSource(os.path.join(FIXTURES, "nope.json"))

    def test_null_source_knows_nothing_about_anybody(self):
        from sdd.evidence import NullEvidenceSource

        self.assertIsNone(NullEvidenceSource().observations("Northwind Analytics"))


FUNCTION_MAP = {
    "gtm": ["gtm", "go-to-market", "revenue operations", "revops", "growth engineering"],
    "ai_eng": ["ai engineer", "machine learning", "ml engineer"],
}


class TestFunctionMapping(unittest.TestCase):
    """A hire only falsifies a posting if it lands in the SAME function. The
    map is config data, so a new title family is a config edit, not a code edit."""

    def test_maps_a_title_to_its_function(self):
        from sdd.checks import map_function

        self.assertEqual(map_function("GTM Engineer", FUNCTION_MAP), "gtm")

    def test_matching_is_case_insensitive_and_substring_based(self):
        from sdd.checks import map_function

        self.assertEqual(
            map_function("Revenue Operations Manager", FUNCTION_MAP), "gtm"
        )
        self.assertEqual(
            map_function("Machine Learning Engineer", FUNCTION_MAP), "ai_eng"
        )

    def test_an_unmappable_title_returns_none_rather_than_guessing(self):
        from sdd.checks import map_function

        self.assertIsNone(map_function("Warehouse Associate", FUNCTION_MAP))


class TestNoHiresSincePostingFalsifier(unittest.TestCase):
    """Falsifier: no hires have been observed into this function at this company
    since the posting date. This is the check that would have caught the worked
    case by design instead of by luck."""

    def _run(self, signal, source=None):
        from sdd.checks import CheckContext, check_no_hires_since_posting
        from sdd.evidence import FileEvidenceSource

        context = CheckContext(
            as_of=AS_OF,
            evidence=source if source is not None else FileEvidenceSource(SAMPLE_EVIDENCE),
            function_map=FUNCTION_MAP,
        )
        return check_no_hires_since_posting(signal, {}, context)

    def test_golden_case_hire_into_the_function_after_posting_invalidates(self):
        from sdd.model import INVALIDATED

        result = self._run(
            make_signal(
                company="Northwind Analytics",
                title="GTM Engineer",
                date_posted=date(2026, 3, 2),
            )
        )
        self.assertEqual(result.status, INVALIDATED)

    def test_invalidated_evidence_names_the_hire_date_and_function(self):
        result = self._run(
            make_signal(
                company="Northwind Analytics",
                title="GTM Engineer",
                date_posted=date(2026, 3, 2),
            )
        )
        self.assertIn("2026-06-14", result.evidence)
        self.assertIn("gtm", result.evidence)

    def test_looked_and_found_no_hires_holds(self):
        from sdd.model import VALID

        result = self._run(
            make_signal(
                company="Cobalt Systems",
                title="AI Engineer, Platform",
                date_posted=date(2026, 7, 15),
            )
        )
        self.assertEqual(result.status, VALID)

    def test_company_nobody_looked_at_is_suspect_not_valid(self):
        from sdd.model import SUSPECT

        result = self._run(
            make_signal(
                company="Peregrine Labs",
                title="Revenue Operations Manager",
                date_posted=date(2026, 4, 20),
            )
        )
        self.assertEqual(result.status, SUSPECT)
        self.assertIn("no hire observations", result.evidence)

    def test_a_hire_before_the_posting_date_does_not_falsify_it(self):
        from sdd.model import VALID

        result = self._run(
            make_signal(
                company="Halcyon Freight",
                title="Machine Learning Engineer",
                date_posted=date(2026, 8, 20),
            )
        )
        self.assertEqual(result.status, VALID)

    def test_a_hire_into_a_different_function_does_not_falsify_it(self):
        from sdd.model import VALID

        # Halcyon's only hire is into gtm, dated after this hypothetical
        # ai_eng posting. Different function, so the posting still stands.
        result = self._run(
            make_signal(
                company="Halcyon Freight",
                title="Machine Learning Engineer",
                date_posted=date(2026, 4, 1),
            )
        )
        self.assertEqual(result.status, VALID)

    def test_unmappable_title_cannot_scope_the_check_so_it_is_suspect(self):
        from sdd.model import SUSPECT

        result = self._run(
            make_signal(
                company="Cobalt Systems",
                title="Warehouse Associate",
                date_posted=date(2026, 7, 15),
            )
        )
        self.assertEqual(result.status, SUSPECT)

    def test_with_no_evidence_source_every_signal_is_suspect(self):
        from sdd.evidence import NullEvidenceSource
        from sdd.model import SUSPECT

        result = self._run(
            make_signal(
                company="Cobalt Systems",
                title="AI Engineer, Platform",
                date_posted=date(2026, 7, 15),
            ),
            source=NullEvidenceSource(),
        )
        self.assertEqual(result.status, SUSPECT)


class TestPostingStillListedFalsifier(unittest.TestCase):
    """Falsifier: the posting was still listed on the most recent source scrape.
    A posting that quietly stopped appearing was filled or pulled, and its age
    keeps climbing in state.json regardless."""

    THRESHOLDS = {"suspect_after_days": 14, "invalidate_after_days": 45}

    def _run(self, last_seen):
        from sdd.checks import CheckContext, check_posting_still_listed

        return check_posting_still_listed(
            make_signal(last_seen=last_seen), self.THRESHOLDS, CheckContext(as_of=AS_OF)
        )

    def test_seen_on_the_latest_scrape_holds(self):
        from sdd.model import VALID

        self.assertEqual(self._run(date(2026, 9, 9)).status, VALID)

    def test_gone_for_three_weeks_is_suspect(self):
        from sdd.model import SUSPECT

        self.assertEqual(self._run(date(2026, 8, 20)).status, SUSPECT)

    def test_gone_for_seventy_days_is_invalidated(self):
        from sdd.model import INVALIDATED

        self.assertEqual(self._run(date(2026, 7, 1)).status, INVALIDATED)

    def test_evidence_names_how_long_it_has_been_missing(self):
        result = self._run(date(2026, 7, 1))
        self.assertIn("71", result.evidence)


if __name__ == "__main__":
    unittest.main(verbosity=2)

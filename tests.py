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


if __name__ == "__main__":
    unittest.main(verbosity=2)

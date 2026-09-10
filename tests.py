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


if __name__ == "__main__":
    unittest.main(verbosity=2)

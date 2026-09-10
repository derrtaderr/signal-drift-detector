"""Test suite for signal-drift-detector.

Deterministic and keyless. No network calls. Run:

    python3 tests.py
"""

import os
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


if __name__ == "__main__":
    unittest.main(verbosity=2)

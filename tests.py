"""Test suite for signal-drift-detector.

Deterministic and keyless. No network calls. Run:

    python3 tests.py
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import re
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


class StubEvidenceSource:
    """An in-memory evidence source, for shapes the bundled fixture does not
    carry (several hires into one function, say). Same interface as
    FileEvidenceSource: absent company returns None, meaning nobody looked."""

    name = "stub"
    configured = True

    def __init__(self, records):
        self._records = records

    def observations(self, company):
        return self._records.get(company)


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


SINGLE_SEAT = ("founding", "head of", "vp ")


class TestSingleSeatTitles(unittest.TestCase):
    """A title that names ONE chair. Matching one is the only thing that lets a
    lone observed hire invalidate a signal on the title alone, so the match has
    to be conservative and it has to report WHICH pattern fired."""

    def test_a_founding_title_names_one_chair(self):
        from sdd.checks import match_single_seat

        self.assertEqual(
            match_single_seat("Founding GTM Engineer", SINGLE_SEAT), "founding"
        )

    def test_matching_is_case_insensitive(self):
        from sdd.checks import match_single_seat

        self.assertEqual(
            match_single_seat("HEAD OF REVENUE OPERATIONS", SINGLE_SEAT), "head of"
        )

    def test_a_plain_function_title_names_no_seat_count(self):
        """"GTM Engineer" could be one chair or five. That ambiguity is the
        whole reason a hire against it cannot invalidate."""
        from sdd.checks import match_single_seat

        self.assertIsNone(match_single_seat("GTM Engineer", SINGLE_SEAT))
        self.assertIsNone(
            match_single_seat("Senior GTM Engineer (3 openings)", SINGLE_SEAT)
        )

    def test_no_patterns_configured_means_nothing_is_single_seat(self):
        from sdd.checks import match_single_seat

        self.assertIsNone(match_single_seat("Founding GTM Engineer", ()))

    def test_the_vp_pattern_keeps_its_trailing_space(self):
        """Without the space, "vp" matches inside ordinary words. With it, the
        pattern misses "Sales VP" -- a miss that fails toward SUSPECT, which is
        the safe direction."""
        from sdd.checks import match_single_seat

        self.assertEqual(match_single_seat("VP of Sales", SINGLE_SEAT), "vp ")
        self.assertIsNone(match_single_seat("Sales VP", SINGLE_SEAT))


class TestNoHiresSincePostingFalsifier(unittest.TestCase):
    """Falsifier: no hires have been observed into this function at this company
    since the posting date. This is the check that would have caught the worked
    case by design instead of by luck."""

    def _run(self, signal, source=None, single_seat_patterns=SINGLE_SEAT):
        from sdd.checks import CheckContext, check_no_hires_since_posting
        from sdd.evidence import FileEvidenceSource

        context = CheckContext(
            as_of=AS_OF,
            evidence=source if source is not None else FileEvidenceSource(SAMPLE_EVIDENCE),
            function_map=FUNCTION_MAP,
            single_seat_patterns=single_seat_patterns,
        )
        return check_no_hires_since_posting(signal, {}, context)

    def _golden(self, **overrides):
        fields = dict(
            company="Northwind Analytics",
            title="GTM Engineer",
            date_posted=date(2026, 3, 2),
        )
        fields.update(overrides)
        return make_signal(**fields)

    def test_an_uncorroborated_hire_degrades_rather_than_invalidating(self):
        """Row 51, the accepted critique. One hire cannot distinguish a filled
        seat from a team still expanding. "GTM Engineer" names no seat count,
        and nothing else here says the seat is gone, so the honest reading is
        counterevidence -- SUSPECT, held for review -- not proof."""
        from sdd.model import SUSPECT

        result = self._run(self._golden())

        self.assertEqual(result.status, SUSPECT)

    def test_the_uncorroborated_line_names_its_path(self):
        """An operator has to be able to grep a run for which argument fired
        and disagree with that argument specifically."""
        result = self._run(self._golden())

        self.assertIn("uncorroborated-hence-suspect", result.evidence)
        self.assertNotIn("corroborated-by-", result.evidence.split("uncorroborated")[0])

    def test_hire_evidence_names_the_hire_date_and_function(self):
        result = self._run(self._golden())

        self.assertIn("2026-06-14", result.evidence)
        self.assertIn("gtm", result.evidence)

    def test_a_single_seat_title_corroborates_the_hire_and_invalidates(self):
        """A company does not hire two founding GTM engineers. The title names
        one chair, the hire took it, the vacancy reading is gone."""
        from sdd.model import INVALIDATED

        result = self._run(self._golden(title="Founding GTM Engineer"))

        self.assertEqual(result.status, INVALIDATED)
        self.assertIn("corroborated-by-single-seat-title", result.evidence)

    def test_the_single_seat_line_names_the_pattern_that_matched(self):
        result = self._run(self._golden(title="Founding GTM Engineer"))

        self.assertIn("founding", result.evidence)
        self.assertIn("Founding GTM Engineer", result.evidence)

    def test_with_no_single_seat_patterns_even_a_founding_title_is_suspect(self):
        """Absent config corroborates nothing. Fail-closed direction."""
        from sdd.model import SUSPECT

        result = self._run(
            self._golden(title="Founding GTM Engineer"), single_seat_patterns=()
        )

        self.assertEqual(result.status, SUSPECT)
        self.assertIn("uncorroborated-hence-suspect", result.evidence)

    def test_several_uncorroborated_hires_are_still_suspect(self):
        """Counting is not corroborating. Three hires into a function reads as
        EXPANSION, which is the opposite of a filled seat; escalating past some
        hire count would re-introduce the v1 error with a number attached."""
        from sdd.model import SUSPECT

        result = self._run(
            self._golden(),
            source=StubEvidenceSource(
                {
                    "Northwind Analytics": {
                        "checked_through": date(2026, 9, 8),
                        "hires": [
                            {
                                "function": "gtm",
                                "date": date(2026, 6, 14),
                                "source": "roster",
                            },
                            {
                                "function": "gtm",
                                "date": date(2026, 7, 20),
                                "source": "roster",
                            },
                            {
                                "function": "gtm",
                                "date": date(2026, 8, 30),
                                "source": "roster",
                            },
                        ],
                    }
                }
            ),
        )

        self.assertEqual(result.status, SUSPECT)
        self.assertIn("uncorroborated-hence-suspect", result.evidence)
        # The count is reported so a human can weigh it; the tool does not.
        self.assertIn("3 hires", result.evidence)
        self.assertIn("2026-06-14", result.evidence)

    def test_a_hire_carries_a_conditional_promotion_for_the_delisting_path(self):
        """The check cannot see a sibling falsifier's outcome, so it hands the
        engine a conditional instead of guessing. The engine applies it."""
        from sdd.model import INVALIDATED

        result = self._run(self._golden())
        trigger, status, evidence = result.corroborated_by

        self.assertEqual(trigger, "posting_still_listed")
        self.assertEqual(status, INVALIDATED)
        self.assertIn("corroborated-by-delisting", evidence)

    def test_a_corroborated_hire_carries_no_further_promotion(self):
        """Already INVALIDATED. A second escalation path would be dead weight
        and an ordering hazard."""
        result = self._run(self._golden(title="Founding GTM Engineer"))

        self.assertEqual(result.corroborated_by, ())

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

    def test_a_null_source_reports_no_source_configured_not_nobody_looked(self):
        """NullEvidenceSource IS "no evidence source configured".

        The CLI always hands the check a source object, so gating the promised
        message on ``evidence is None`` alone made it unreachable in a real run
        and blamed the company for a flag the operator forgot.
        """
        from sdd.evidence import NullEvidenceSource
        from sdd.model import SUSPECT

        result = self._run(
            make_signal(
                company="Northwind Analytics",
                title="GTM Engineer",
                date_posted=date(2026, 3, 2),
            ),
            source=NullEvidenceSource(),
        )

        self.assertEqual(result.status, SUSPECT)
        self.assertEqual(
            result.evidence,
            "no evidence source configured, so the hire check could not run",
        )

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


class TestConfig(unittest.TestCase):
    """The falsifier registry, the schedule and the thresholds are data. Code
    holds only the check implementations."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, obj):
        path = os.path.join(self.tmp, "config.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(obj, handle)
        return path

    def test_repo_config_registers_the_vacancy_duration_class(self):
        from sdd.config import DEFAULT_CONFIG_PATH, load_config

        config = load_config(DEFAULT_CONFIG_PATH)
        klass = config.signal_classes["vacancy_duration"]

        self.assertEqual(klass.recheck_interval_days, 7)
        self.assertEqual(len(klass.falsifiers), 3)

    def test_default_config_path_resolves_without_a_working_directory(self):
        """A clean clone must find its config no matter where it is run from."""
        from sdd.config import DEFAULT_CONFIG_PATH

        self.assertTrue(os.path.isabs(DEFAULT_CONFIG_PATH))
        self.assertTrue(os.path.exists(DEFAULT_CONFIG_PATH))

    def test_each_falsifier_carries_a_statement_thresholds_and_a_callable(self):
        from sdd.config import DEFAULT_CONFIG_PATH, load_config

        config = load_config(DEFAULT_CONFIG_PATH)
        falsifier = config.falsifiers["evergreen_age_ceiling"]

        self.assertTrue(callable(falsifier.check))
        self.assertIn("evergreen", falsifier.statement.lower())
        self.assertEqual(falsifier.thresholds["invalidate_after_days"], 180)

    def test_repo_config_carries_the_single_seat_patterns(self):
        """Titles that name ONE chair. A hire into a one-chair posting is the
        only title-side corroboration that can invalidate a signal."""
        config = repo_config()

        self.assertIn("founding", config.single_seat_patterns)
        self.assertIn("head of", config.single_seat_patterns)
        # Every pattern is a non-empty lowercase string. An empty one would
        # match every title and make every hire corroborated.
        for pattern in config.single_seat_patterns:
            self.assertIsInstance(pattern, str)
            self.assertTrue(pattern.strip())
            self.assertEqual(pattern, pattern.lower())

    def test_repo_config_carries_the_function_map(self):
        from sdd.config import DEFAULT_CONFIG_PATH, load_config

        config = load_config(DEFAULT_CONFIG_PATH)
        self.assertIn("gtm", config.function_map)

    def test_a_falsifier_naming_an_unknown_check_is_rejected_at_load(self):
        from sdd.config import load_config
        from sdd.model import DriftError

        path = self._write(
            {
                "falsifiers": {
                    "made_up": {"check": "vibes", "statement": "x", "thresholds": {}}
                },
                "signal_classes": {},
                "function_map": {},
            }
        )
        with self.assertRaises(DriftError) as ctx:
            load_config(path)
        self.assertIn("vibes", str(ctx.exception))

    def test_a_class_naming_an_undefined_falsifier_is_rejected_at_load(self):
        from sdd.config import load_config
        from sdd.model import DriftError

        path = self._write(
            {
                "falsifiers": {},
                "signal_classes": {
                    "vacancy_duration": {
                        "recheck_interval_days": 7,
                        "falsifiers": ["ghost"],
                    }
                },
                "function_map": {},
            }
        )
        with self.assertRaises(DriftError) as ctx:
            load_config(path)
        self.assertIn("ghost", str(ctx.exception))

    def test_a_falsifier_with_no_statement_is_rejected(self):
        """A claim nobody can read and disagree with is not a falsifier."""
        from sdd.config import load_config
        from sdd.model import DriftError

        path = self._write(
            {
                "falsifiers": {
                    "nameless": {"check": "age_ceiling", "thresholds": {}}
                },
                "signal_classes": {},
                "function_map": {},
            }
        )
        with self.assertRaises(DriftError) as ctx:
            load_config(path)
        self.assertIn("statement", str(ctx.exception))

    def test_a_signal_class_with_no_falsifiers_is_rejected(self):
        """An unfalsifiable signal class would pass every run by default, which
        is the failure mode this whole tool exists to close."""
        from sdd.config import load_config
        from sdd.model import DriftError

        path = self._write(
            {
                "falsifiers": {},
                "signal_classes": {
                    "vacancy_duration": {
                        "recheck_interval_days": 7,
                        "falsifiers": [],
                    }
                },
                "function_map": {},
            }
        )
        with self.assertRaises(DriftError) as ctx:
            load_config(path)
        self.assertIn("no falsifiers", str(ctx.exception))

    def test_missing_config_file_names_the_path(self):
        from sdd.config import load_config
        from sdd.model import DriftError

        missing = os.path.join(self.tmp, "nope.json")
        with self.assertRaises(DriftError) as ctx:
            load_config(missing)
        self.assertIn(missing, str(ctx.exception))


def repo_config():
    from sdd.config import DEFAULT_CONFIG_PATH, load_config

    return load_config(DEFAULT_CONFIG_PATH)


def eval_context(evidence=None):
    from sdd.checks import CheckContext
    from sdd.evidence import FileEvidenceSource

    config = repo_config()
    return CheckContext(
        as_of=AS_OF,
        evidence=evidence if evidence is not None else FileEvidenceSource(SAMPLE_EVIDENCE),
        function_map=config.function_map,
        single_seat_patterns=config.single_seat_patterns,
    )


class TestEngineVerdicts(unittest.TestCase):
    """Worst falsifier wins, and nothing reaches VALID by silence."""

    def _evaluate(self, signal, config=None, evidence=None):
        from sdd.engine import evaluate_signal

        return evaluate_signal(
            signal, config or repo_config(), eval_context(evidence)
        )

    def test_golden_case_is_invalidated_with_every_falsifier_recorded(self):
        from sdd.model import INVALIDATED

        verdict = self._evaluate(
            make_signal(
                signal_id="li-9000000001",
                company="Northwind Analytics",
                title="GTM Engineer",
                date_posted=date(2026, 3, 2),
                last_seen=date(2026, 9, 9),
            )
        )

        self.assertEqual(verdict.verdict, INVALIDATED)
        self.assertEqual(len(verdict.falsifiers), 3)
        self.assertEqual(verdict.checked_on, AS_OF)
        self.assertFalse(verdict.ranking_eligible)

    def test_every_falsifier_holding_yields_valid_and_ranking_eligible(self):
        from sdd.model import VALID

        verdict = self._evaluate(
            make_signal(
                signal_id="li-9000000002",
                company="Cobalt Systems",
                title="AI Engineer, Platform",
                date_posted=date(2026, 7, 15),
                last_seen=date(2026, 9, 9),
            )
        )

        self.assertEqual(verdict.verdict, VALID)
        self.assertTrue(verdict.ranking_eligible)

    def test_one_suspect_among_valids_makes_the_signal_suspect(self):
        from sdd.model import SUSPECT

        # Peregrine Labs: nobody has looked for hires, so the hire falsifier
        # cannot run. 143 days also trips the age warning line.
        verdict = self._evaluate(
            make_signal(
                signal_id="li-9000000003",
                company="Peregrine Labs",
                title="Revenue Operations Manager",
                date_posted=date(2026, 4, 20),
                last_seen=date(2026, 9, 9),
            )
        )

        self.assertEqual(verdict.verdict, SUSPECT)
        self.assertFalse(verdict.ranking_eligible)

    def test_a_verdict_carries_each_falsifier_statement_and_its_evidence(self):
        verdict = self._evaluate(
            make_signal(
                company="Northwind Analytics",
                title="GTM Engineer",
                date_posted=date(2026, 3, 2),
            )
        )
        by_name = {f.name: f for f in verdict.falsifiers}
        hire = by_name["no_hires_since_posting"]

        self.assertIn("No hires have been observed", hire.statement)
        self.assertIn("2026-06-14", hire.evidence)

    def test_a_check_that_raises_makes_the_signal_suspect_not_valid(self):
        from sdd.config import Config, Falsifier, SignalClass
        from sdd.model import SUSPECT

        def exploding_check(signal, thresholds, context):
            raise RuntimeError("upstream lookup blew up")

        falsifier = Falsifier(
            name="boom",
            check_id="boom",
            check=exploding_check,
            statement="Something that cannot be checked today.",
            thresholds={},
        )
        config = Config(
            falsifiers={"boom": falsifier},
            signal_classes={
                "vacancy_duration": SignalClass(
                    name="vacancy_duration",
                    description="",
                    recheck_interval_days=7,
                    falsifiers=(falsifier,),
                )
            },
            function_map={},
        )

        verdict = self._evaluate(make_signal(), config=config)

        self.assertEqual(verdict.verdict, SUSPECT)
        self.assertIn("upstream lookup blew up", verdict.falsifiers[0].evidence)

    def test_an_unknown_signal_class_is_suspect_rather_than_a_crash(self):
        from sdd.model import SUSPECT

        verdict = self._evaluate(make_signal(signal_class="positioning_drift"))

        self.assertEqual(verdict.verdict, SUSPECT)
        self.assertFalse(verdict.ranking_eligible)
        self.assertIn("positioning_drift", verdict.note)


def config_with_falsifier_order(*names):
    """The repo config, with vacancy_duration's falsifiers in a chosen order.

    Corroboration reads a sibling falsifier's outcome, so the order they run in
    is the obvious place for an order-dependence bug to hide. These tests exist
    to prove there is not one.
    """
    from sdd.config import Config, SignalClass

    base = repo_config()
    return Config(
        falsifiers=base.falsifiers,
        signal_classes={
            "vacancy_duration": SignalClass(
                name="vacancy_duration",
                description="",
                recheck_interval_days=7,
                falsifiers=tuple(base.falsifiers[name] for name in names),
            )
        },
        function_map=base.function_map,
        single_seat_patterns=base.single_seat_patterns,
    )


class TestHireCorroboration(unittest.TestCase):
    """Row 51. A hire degrades a signal; corroboration is what breaks it.

    Worst-wins is untouched by any of this. What changed is only what the hire
    falsifier reports about itself.
    """

    def _evaluate(self, config=None, **overrides):
        from sdd.engine import evaluate_signal

        fields = dict(
            signal_id="li-corroborate",
            company="Northwind Analytics",
            title="GTM Engineer",
            # Young enough that the age ceiling holds (101 days, under the
            # 120-day warning line), so the age falsifier cannot do the
            # invalidating and hide what the hire axis did. Still early enough
            # that the fixture's 2026-06-14 gtm hire post-dates it.
            date_posted=date(2026, 6, 1),
            last_seen=date(2026, 9, 9),
        )
        fields.update(overrides)
        return evaluate_signal(
            make_signal(**fields),
            config or config_with_falsifier_order(
                "evergreen_age_ceiling",
                "no_hires_since_posting",
                "posting_still_listed",
            ),
            eval_context(),
        )

    def _hire(self, verdict):
        return {f.name: f for f in verdict.falsifiers}["no_hires_since_posting"]

    def test_a_hire_on_a_live_listing_holds_the_signal_for_review(self):
        from sdd.model import SUSPECT

        verdict = self._evaluate()

        self.assertEqual(verdict.verdict, SUSPECT)
        self.assertFalse(verdict.ranking_eligible)
        self.assertIn("uncorroborated-hence-suspect", self._hire(verdict).evidence)

    def test_a_hire_plus_a_delisted_posting_invalidates(self):
        """Two independent observations pointing the same way. A hire into the
        function AND a listing that stopped appearing is the ordinary signature
        of a filled seat."""
        from sdd.model import INVALIDATED

        verdict = self._evaluate(last_seen=date(2026, 7, 1))  # 71 days missing

        self.assertEqual(verdict.verdict, INVALIDATED)
        hire = self._hire(verdict)
        self.assertEqual(hire.status, INVALIDATED)
        self.assertIn("corroborated-by-delisting", hire.evidence)
        self.assertNotIn("uncorroborated", hire.evidence)

    def test_a_merely_suspect_listing_does_not_corroborate(self):
        """Only INVALIDATED on posting_still_listed corroborates. Its SUSPECT
        band is itself an "I am not sure", and two unsure readings do not add up
        to a sure one."""
        from sdd.model import SUSPECT

        verdict = self._evaluate(last_seen=date(2026, 8, 21))  # 20 days missing

        hire = self._hire(verdict)
        self.assertEqual(hire.status, SUSPECT)
        self.assertIn("uncorroborated-hence-suspect", hire.evidence)
        self.assertEqual(verdict.verdict, SUSPECT)

    def test_corroboration_does_not_depend_on_falsifier_order(self):
        """Listing the hire falsifier before or after the one that corroborates
        it must produce the same verdict and the same evidence."""
        from sdd.model import INVALIDATED

        first = self._evaluate(
            last_seen=date(2026, 7, 1),
            config=config_with_falsifier_order(
                "no_hires_since_posting", "posting_still_listed"
            ),
        )
        second = self._evaluate(
            last_seen=date(2026, 7, 1),
            config=config_with_falsifier_order(
                "posting_still_listed", "no_hires_since_posting"
            ),
        )

        self.assertEqual(first.verdict, INVALIDATED)
        self.assertEqual(second.verdict, INVALIDATED)
        self.assertEqual(
            self._hire(first).evidence, self._hire(second).evidence
        )

    def test_a_class_without_the_listing_falsifier_cannot_corroborate(self):
        """No corroborator registered means nothing corroborates. The signal is
        held, not thrown away."""
        from sdd.model import SUSPECT

        verdict = self._evaluate(
            last_seen=date(2026, 7, 1),
            config=config_with_falsifier_order(
                "evergreen_age_ceiling", "no_hires_since_posting"
            ),
        )

        self.assertEqual(verdict.verdict, SUSPECT)
        self.assertIn("uncorroborated-hence-suspect", self._hire(verdict).evidence)

    def test_a_single_seat_title_needs_no_second_observation(self):
        from sdd.model import INVALIDATED

        verdict = self._evaluate(title="Founding GTM Engineer")

        self.assertEqual(verdict.verdict, INVALIDATED)
        self.assertIn(
            "corroborated-by-single-seat-title", self._hire(verdict).evidence
        )

    def test_a_signal_with_no_hire_is_untouched_by_any_of_this(self):
        """Cobalt Systems was looked at and nothing was found. No hire means no
        promotion machinery runs at all, delisted or not."""
        from sdd.model import INVALIDATED, VALID

        verdict = self._evaluate(
            company="Cobalt Systems",
            title="AI Engineer, Platform",
            last_seen=date(2026, 7, 1),
        )

        self.assertEqual(self._hire(verdict).status, VALID)
        self.assertEqual(verdict.verdict, INVALIDATED)  # delisting alone


class TestScheduleLedger(unittest.TestCase):
    """The schedule is what makes this a re-check rather than a one-time filter."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "ledger.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_missing_ledger_means_everything_is_due(self):
        from sdd.schedule import Ledger

        ledger = Ledger.load(self.path)

        self.assertIsNone(ledger.entry("li-1"))
        self.assertTrue(ledger.is_due("li-1", interval_days=7, as_of=AS_OF))

    def test_a_signal_checked_today_is_not_due_again(self):
        from sdd.model import VALID
        from sdd.schedule import Ledger

        ledger = Ledger.load(self.path)
        ledger.record("li-1", VALID, AS_OF)

        self.assertFalse(ledger.is_due("li-1", interval_days=7, as_of=AS_OF))

    def test_a_signal_checked_past_the_interval_is_due(self):
        from sdd.model import VALID
        from sdd.schedule import Ledger

        ledger = Ledger.load(self.path)
        ledger.record("li-1", VALID, date(2026, 9, 2))

        self.assertTrue(ledger.is_due("li-1", interval_days=7, as_of=AS_OF))

    def test_a_signal_inside_the_interval_is_not_due(self):
        from sdd.model import VALID
        from sdd.schedule import Ledger

        ledger = Ledger.load(self.path)
        ledger.record("li-1", VALID, date(2026, 9, 6))

        self.assertFalse(ledger.is_due("li-1", interval_days=7, as_of=AS_OF))

    def test_the_ledger_round_trips_through_disk(self):
        from sdd.model import INVALIDATED
        from sdd.schedule import Ledger

        ledger = Ledger.load(self.path)
        ledger.record("li-9000000001", INVALIDATED, AS_OF)
        ledger.save()

        reloaded = Ledger.load(self.path)
        entry = reloaded.entry("li-9000000001")

        self.assertEqual(entry["verdict"], INVALIDATED)
        self.assertEqual(entry["last_checked"], AS_OF)

    def test_an_entry_with_an_unreadable_verdict_is_due(self):
        """A cached verdict nobody can classify is not a usable cache entry."""
        from sdd.schedule import Ledger

        ledger = Ledger.load(self.path)
        ledger.record("li-1", None, AS_OF)
        self.assertTrue(ledger.is_due("li-1", interval_days=7, as_of=AS_OF))

        ledger.record("li-2", "PROBABLY_FINE", AS_OF)
        self.assertTrue(ledger.is_due("li-2", interval_days=7, as_of=AS_OF))

    def test_a_row_missing_its_verdict_is_dropped_on_load(self):
        from sdd.schedule import Ledger

        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(
                {"signals": {"li-1": {"last_checked": "2026-09-10"}}}, handle
            )

        ledger = Ledger.load(self.path)

        self.assertIsNone(ledger.entry("li-1"))

    def test_a_ledger_whose_signals_are_not_an_object_is_discarded(self):
        """Same shape as the other input bugs, but a ledger is a CACHE.

        Unreadable means re-check, which is already the fail-closed direction,
        so this one is discarded rather than refused. It must not traceback.
        """
        from sdd.schedule import Ledger

        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"signals": ["li-1"]}, handle)

        ledger = Ledger.load(self.path)

        self.assertIsNone(ledger.entry("li-1"))
        self.assertTrue(ledger.is_due("li-1", interval_days=7, as_of=AS_OF))

    def test_a_corrupt_ledger_is_discarded_so_everything_recheck(self):
        """A ledger is a cache, never a source of truth. Unreadable means
        re-check everything, which is the fail-closed direction."""
        from sdd.schedule import Ledger

        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{ not json")

        ledger = Ledger.load(self.path)

        self.assertTrue(ledger.is_due("li-1", interval_days=7, as_of=AS_OF))


class TestFullRun(unittest.TestCase):
    """End to end over the bundled synthetic fixture."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ledger_path = os.path.join(self.tmp, "ledger.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, ledger=None, force=False):
        from sdd.adapters import load_vacancy_signals
        from sdd.engine import run

        signals = load_vacancy_signals(SAMPLE_STATE, as_of=AS_OF)
        return run(
            signals,
            repo_config(),
            eval_context(),
            ledger=ledger,
            force=force,
        )

    def test_the_fixture_produces_the_expected_spread_of_verdicts(self):
        from sdd.model import INVALIDATED, SUSPECT, VALID

        result = self._run()
        verdicts = {v.signal_id: v.verdict for v in result.verdicts}

        self.assertEqual(verdicts["li-9000000001"], INVALIDATED)  # hired into gtm
        self.assertEqual(verdicts["li-9000000002"], VALID)
        self.assertEqual(verdicts["li-9000000003"], SUSPECT)  # nobody looked
        self.assertEqual(verdicts["li-9000000004"], INVALIDATED)  # scrape lost it
        self.assertEqual(verdicts["li-9000000005"], VALID)

    def test_only_valid_signals_are_ranking_eligible(self):
        result = self._run()
        self.assertEqual(
            sorted(result.eligible_ids), ["li-9000000002", "li-9000000005"]
        )

    def test_summary_counts_every_verdict(self):
        from sdd.model import INVALIDATED, SUSPECT, VALID

        result = self._run()
        self.assertEqual(result.summary[VALID], 2)
        self.assertEqual(result.summary[SUSPECT], 1)
        self.assertEqual(result.summary[INVALIDATED], 2)

    def test_a_run_records_what_it_checked_into_the_ledger(self):
        from sdd.model import INVALIDATED
        from sdd.schedule import Ledger

        ledger = Ledger.load(self.ledger_path)
        self._run(ledger=ledger)
        ledger.save()

        reloaded = Ledger.load(self.ledger_path)
        entry = reloaded.entry("li-9000000001")
        self.assertEqual(entry["verdict"], INVALIDATED)
        self.assertEqual(entry["last_checked"], AS_OF)

    def test_a_signal_inside_its_interval_carries_its_verdict_forward(self):
        from sdd.model import VALID
        from sdd.schedule import Ledger

        ledger = Ledger.load(self.ledger_path)
        # Claim the golden case was checked two days ago and came back VALID.
        # Inside the 7-day interval it must be carried, not re-derived.
        ledger.record("li-9000000001", VALID, date(2026, 9, 8))

        result = self._run(ledger=ledger)
        golden = [v for v in result.verdicts if v.signal_id == "li-9000000001"][0]

        self.assertEqual(golden.verdict, VALID)
        self.assertTrue(golden.carried_forward)
        self.assertEqual(golden.checked_on, date(2026, 9, 8))
        self.assertEqual(golden.falsifiers, ())

    def test_force_rechecks_a_signal_inside_its_interval(self):
        from sdd.model import INVALIDATED, VALID
        from sdd.schedule import Ledger

        ledger = Ledger.load(self.ledger_path)
        ledger.record("li-9000000001", VALID, date(2026, 9, 8))

        result = self._run(ledger=ledger, force=True)
        golden = [v for v in result.verdicts if v.signal_id == "li-9000000001"][0]

        self.assertEqual(golden.verdict, INVALIDATED)
        self.assertFalse(golden.carried_forward)

    def test_run_with_no_ledger_checks_everything(self):
        result = self._run(ledger=None)
        self.assertTrue(all(not v.carried_forward for v in result.verdicts))

    def test_verdicts_serialize_to_json_safe_dicts(self):
        result = self._run()
        payload = result.to_dict()

        text = json.dumps(payload)  # must not raise
        self.assertIn("li-9000000001", text)
        self.assertEqual(payload["as_of"], "2026-09-10")
        golden = [
            v for v in payload["verdicts"] if v["signal_id"] == "li-9000000001"
        ][0]
        self.assertFalse(golden["ranking_eligible"])
        self.assertEqual(len(golden["falsifiers"]), 3)


class TestReport(unittest.TestCase):
    """The report is for the human deciding whether to disagree with a verdict,
    so it has to carry the falsifier statement and the evidence, not just a label."""

    def _report(self):
        from sdd.adapters import load_vacancy_signals
        from sdd.engine import run
        from sdd.report import render

        signals = load_vacancy_signals(SAMPLE_STATE, as_of=AS_OF)
        return render(run(signals, repo_config(), eval_context()))

    def test_header_carries_the_check_date_and_the_counts(self):
        text = self._report()
        self.assertIn("2026-09-10", text)
        self.assertIn("2 VALID", text)
        self.assertIn("1 SUSPECT", text)
        self.assertIn("2 INVALIDATED", text)

    def test_an_invalidated_signal_shows_what_broke_it(self):
        text = self._report()
        self.assertIn("Northwind Analytics", text)
        self.assertIn("192", text)
        self.assertIn("2026-06-14", text)

    def test_the_falsifier_statement_is_printed_not_just_its_name(self):
        text = self._report()
        self.assertIn("No hires have been observed", text)

    def test_a_suspect_signal_says_why_it_could_not_be_confirmed(self):
        text = self._report()
        self.assertIn("Peregrine Labs", text)
        self.assertIn("nobody has looked", text)

    def test_an_empty_run_renders_without_crashing(self):
        from sdd.engine import RunResult
        from sdd.report import render

        text = render(RunResult(as_of=AS_OF, verdicts=()))
        self.assertIn("0 VALID", text)


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _main(self, args):
        """Run the CLI in-process, returning (exit_code, stdout, stderr)."""
        from sdd.cli import main

        out, err = io.StringIO(), io.StringIO()
        code = main(args, stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    BASE = None

    def base_args(self):
        return [
            "check",
            "--state",
            SAMPLE_STATE,
            "--evidence",
            SAMPLE_EVIDENCE,
            "--as-of",
            "2026-09-10",
        ]

    def test_check_prints_a_report_and_exits_zero(self):
        code, out, err = self._main(self.base_args())

        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("Northwind Analytics", out)
        self.assertIn("INVALIDATED", out)

    def _write(self, name, obj):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(obj, handle)
        return path

    def _hire_case(self, title, last_seen="2026-09-09"):
        """One posting, one post-dated gtm hire. Young enough that the age
        ceiling holds, so whatever the verdict is, the hire axis produced it."""
        state = self._write(
            "state.json",
            {
                "li-1": {
                    "company": "Northwind Analytics",
                    "title": title,
                    "date_posted": "2026-07-01",
                    "last_seen": last_seen,
                    "job_url": "https://example.invalid/jobs/1",
                }
            },
        )
        evidence = self._write(
            "evidence.json",
            {
                "companies": {
                    "Northwind Analytics": {
                        "checked_through": "2026-09-08",
                        "hires": [
                            {
                                "function": "gtm",
                                "date": "2026-08-05",
                                "source": "team roster review",
                            }
                        ],
                    }
                }
            },
        )
        return self._main(
            [
                "check",
                "--state",
                state,
                "--evidence",
                evidence,
                "--as-of",
                "2026-09-10",
                "--format",
                "json",
            ]
        )

    def _hire_falsifier(self, out):
        payload = json.loads(out)
        verdict = payload["verdicts"][0]
        hire = [
            f for f in verdict["falsifiers"] if f["name"] == "no_hires_since_posting"
        ][0]
        return verdict, hire

    def test_a_real_run_holds_an_uncorroborated_hire_rather_than_dropping_it(self):
        """End to end, with the repo's own config: a lone hire against a title
        that names no seat count is SUSPECT. It does not rank, and it is not
        thrown away either."""
        code, out, err = self._hire_case("GTM Engineer")
        verdict, hire = self._hire_falsifier(out)

        self.assertEqual(code, 0, err)
        self.assertEqual(verdict["verdict"], "SUSPECT")
        self.assertEqual(hire["status"], "SUSPECT")
        self.assertIn("uncorroborated-hence-suspect", hire["evidence"])

    def test_a_real_run_invalidates_a_hire_into_a_single_seat_title(self):
        """Proves the repo config's single_seat_patterns actually reach the
        check. Wired only at one end, this test is the one that notices."""
        code, out, err = self._hire_case("Founding GTM Engineer")
        verdict, hire = self._hire_falsifier(out)

        self.assertEqual(code, 0, err)
        self.assertEqual(verdict["verdict"], "INVALIDATED")
        self.assertEqual(hire["status"], "INVALIDATED")
        self.assertIn("corroborated-by-single-seat-title", hire["evidence"])
        self.assertIn("founding", hire["evidence"])

    def test_a_real_run_invalidates_a_hire_into_a_delisted_posting(self):
        code, out, err = self._hire_case("GTM Engineer", last_seen="2026-07-05")
        verdict, hire = self._hire_falsifier(out)

        self.assertEqual(code, 0, err)
        self.assertEqual(verdict["verdict"], "INVALIDATED")
        self.assertEqual(hire["status"], "INVALIDATED")
        self.assertIn("corroborated-by-delisting", hire["evidence"])

    def test_json_format_emits_machine_readable_verdicts(self):
        code, out, _ = self._main(self.base_args() + ["--format", "json"])
        payload = json.loads(out)

        self.assertEqual(code, 0)
        self.assertEqual(payload["as_of"], "2026-09-10")
        self.assertEqual(len(payload["verdicts"]), 5)
        self.assertEqual(payload["summary"]["INVALIDATED"], 2)

    def test_ranking_only_emits_just_the_eligible_ids(self):
        code, out, _ = self._main(self.base_args() + ["--ranking-only"])

        self.assertEqual(code, 0)
        self.assertEqual(
            sorted(out.split()), ["li-9000000002", "li-9000000005"]
        )

    def test_without_an_evidence_file_the_hire_check_cannot_run(self):
        """Fail closed: no evidence means SUSPECT everywhere, never VALID."""
        args = [
            "check",
            "--state",
            SAMPLE_STATE,
            "--as-of",
            "2026-09-10",
            "--format",
            "json",
        ]
        code, out, _ = self._main(args)
        payload = json.loads(out)

        self.assertEqual(code, 0)
        self.assertEqual(payload["summary"]["VALID"], 0)

        # The reason must blame the missing flag, not the companies. A run with
        # no evidence source says so on every hire falsifier; it must never
        # report "nobody has looked", which is a claim about a company.
        hire_reasons = [
            f["evidence"]
            for verdict in payload["verdicts"]
            for f in verdict["falsifiers"]
            if f["name"] == "no_hires_since_posting"
        ]
        self.assertTrue(hire_reasons)
        for reason in hire_reasons:
            self.assertIn(
                "no evidence source configured, so the hire check could not run",
                reason,
            )
            self.assertNotIn("nobody has looked", reason)

    def test_a_company_absent_from_a_supplied_evidence_file_says_nobody_looked(self):
        """The two fail-closed reasons must stay distinguishable in output.

        An evidence file WAS configured, so a company missing from it is a
        statement about that company, not about the operator's flags.
        """
        code, out, _ = self._main(self.base_args() + ["--format", "json"])
        payload = json.loads(out)

        self.assertEqual(code, 0)
        peregrine = [
            v for v in payload["verdicts"] if v["company"] == "Peregrine Labs"
        ][0]
        hire = [
            f
            for f in peregrine["falsifiers"]
            if f["name"] == "no_hires_since_posting"
        ][0]

        self.assertEqual(hire["status"], "SUSPECT")
        self.assertIn(
            "no hire observations on record for Peregrine Labs, so nobody has looked",
            hire["evidence"],
        )
        self.assertNotIn("no evidence source configured", hire["evidence"])

    def _ledger_with(self, row):
        """A ledger holding one row for the golden signal, checked today.

        Today means inside the 7-day interval, so an intact row WOULD be
        carried forward. That is what makes these tests about the verdict's
        readability rather than about the schedule.
        """
        path = os.path.join(self.tmp, "ledger.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "signals": {"li-9000000001": row}}, handle)
        return path

    def test_a_ledger_row_with_no_verdict_is_re_checked_not_carried_forward(self):
        """flow.md: a partial ledger is discarded and everything is re-checked.

        A hand-edited row without a verdict used to carry forward as None,
        match no report section, and vanish from the body while still being
        counted in the header.
        """
        ledger = self._ledger_with({"last_checked": "2026-09-10"})

        code, out, _ = self._main(
            self.base_args() + ["--format", "json", "--ledger", ledger]
        )
        payload = json.loads(out)
        golden = [
            v for v in payload["verdicts"] if v["signal_id"] == "li-9000000001"
        ][0]

        self.assertEqual(code, 0)
        self.assertFalse(golden["carried_forward"])
        self.assertEqual(golden["verdict"], "INVALIDATED")
        self.assertTrue(golden["falsifiers"], "a re-checked signal carries receipts")

    def test_a_ledger_row_with_an_unrecognized_verdict_is_re_checked(self):
        ledger = self._ledger_with(
            {"last_checked": "2026-09-10", "verdict": "PROBABLY_FINE"}
        )

        code, out, _ = self._main(
            self.base_args() + ["--format", "json", "--ledger", ledger]
        )
        payload = json.loads(out)
        golden = [
            v for v in payload["verdicts"] if v["signal_id"] == "li-9000000001"
        ][0]

        self.assertEqual(code, 0)
        self.assertFalse(golden["carried_forward"])
        self.assertEqual(golden["verdict"], "INVALIDATED")

    def test_a_re_checked_corrupt_row_is_visible_in_the_report_body(self):
        """The invariant: nothing counted in the header may be absent below it."""
        ledger = self._ledger_with({"last_checked": "2026-09-10"})

        code, out, _ = self._main(self.base_args() + ["--ledger", ledger])

        self.assertEqual(code, 0)
        self.assertIn("li-9000000001", out)
        self.assertIn("Northwind Analytics", out)

        # Every signal the header counts must appear as a row in the body.
        header = [line for line in out.splitlines() if "signals checked" in line][0]
        counted = int(header.split("(")[1].split(" ")[0])
        body_rows = [
            line for line in out.splitlines() if line.startswith("  [")
        ]
        self.assertEqual(len(body_rows), counted)

    def test_a_corrupt_row_is_healed_in_the_ledger_it_writes_back(self):
        ledger = self._ledger_with({"last_checked": "2026-09-10"})

        code, _, _ = self._main(self.base_args() + ["--ledger", ledger])

        self.assertEqual(code, 0)
        with open(ledger, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(
            saved["signals"]["li-9000000001"]["verdict"], "INVALIDATED"
        )

    def test_a_malformed_state_file_exits_nonzero_with_a_message_not_a_traceback(self):
        bad = os.path.join(self.tmp, "state.json")
        with open(bad, "w", encoding="utf-8") as handle:
            handle.write("{ not json")

        code, out, err = self._main(["check", "--state", bad])

        self.assertEqual(code, 2)
        self.assertIn("not valid JSON", err)
        self.assertNotIn("Traceback", err)

    def test_the_ledger_is_written_when_a_path_is_given(self):
        ledger_path = os.path.join(self.tmp, "ledger.json")
        code, _, _ = self._main(self.base_args() + ["--ledger", ledger_path])

        self.assertEqual(code, 0)
        with open(ledger_path, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(
            saved["signals"]["li-9000000001"]["verdict"], "INVALIDATED"
        )
        self.assertEqual(
            saved["signals"]["li-9000000001"]["last_checked"], "2026-09-10"
        )

    def test_a_second_run_inside_the_interval_carries_verdicts_forward(self):
        ledger_path = os.path.join(self.tmp, "ledger.json")
        self._main(self.base_args() + ["--ledger", ledger_path])
        code, out, _ = self._main(
            self.base_args() + ["--ledger", ledger_path, "--format", "json"]
        )
        payload = json.loads(out)

        self.assertEqual(code, 0)
        self.assertTrue(all(v["carried_forward"] for v in payload["verdicts"]))

    def test_json_output_can_be_written_to_a_file(self):
        out_path = os.path.join(self.tmp, "verdicts.json")
        code, _, _ = self._main(self.base_args() + ["--out", out_path])

        self.assertEqual(code, 0)
        with open(out_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual(len(payload["verdicts"]), 5)


class TestRefusesBadInputWithoutATraceback(unittest.TestCase):
    """README promises exit 2 and a one-line message, never a traceback.

    Every case below used to die with a stack trace and exit 1. They are one
    class of bug, not five: an input the tool reads is a claim about the world,
    and reading it must never assume a shape it did not verify. Each refusal
    happens before any verdict is emitted, so the fail-closed direction holds —
    these are clean refusals, never partial output.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _main(self, args):
        from sdd.cli import main

        out, err = io.StringIO(), io.StringIO()
        code = main(args, stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def _write(self, name, obj):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(obj, handle)
        return path

    def _config(self, **overrides):
        """A valid config, mutated by keyword for the case under test."""
        signal_class = {
            "description": "",
            "recheck_interval_days": 7,
            "falsifiers": ["no_hires_since_posting"],
        }
        signal_class.update(overrides.pop("signal_class", {}))
        raw = {
            "falsifiers": {
                "no_hires_since_posting": {
                    "check": "no_hires_since_posting",
                    "statement": "No hires since the posting date.",
                    "thresholds": {},
                }
            },
            "signal_classes": {"vacancy_duration": signal_class},
            "function_map": {"gtm": ["gtm"]},
        }
        raw.update(overrides)
        return self._write("config.json", raw)

    def _state(self, title="Warehouse Associate", company="Northwind Analytics"):
        return self._write(
            "state.json",
            {
                "li-1": {
                    "company": company,
                    "title": title,
                    "date_posted": "2026-03-02",
                    "last_seen": "2026-09-09",
                    "job_url": "https://example.invalid/jobs/1",
                }
            },
        )

    def _assert_refused(self, code, out, err, needle):
        self.assertEqual(code, 2, "expected exit 2 (refused input), got %r" % code)
        self.assertNotIn("Traceback", err)
        self.assertIn("error:", err)
        self.assertIn(needle, err)
        self.assertEqual(err.count("\n"), 1, "refusal must be one line: %r" % err)
        self.assertEqual(out, "", "refused input must emit no verdicts")

    def base_args(self, **kwargs):
        args = ["check", "--state", SAMPLE_STATE, "--as-of", "2026-09-10"]
        for flag, value in kwargs.items():
            args += ["--" + flag.replace("_", "-"), value]
        return args

    # --- site 1: evidence company record is null -------------------------

    def test_evidence_company_record_of_null_is_refused(self):
        evidence = self._write("evidence.json", {"companies": {"Northwind": None}})

        code, out, err = self._main(self.base_args(evidence=evidence))

        self._assert_refused(code, out, err, "Northwind")

    # --- site 2: a hire entry is not an object ---------------------------

    def test_evidence_hire_entry_that_is_not_an_object_is_refused(self):
        evidence = self._write(
            "evidence.json", {"companies": {"Northwind": {"hires": ["2026-06-14"]}}}
        )

        code, out, err = self._main(self.base_args(evidence=evidence))

        self._assert_refused(code, out, err, "Northwind")

    def test_hire_with_a_null_function_is_refused(self):
        """A hire nobody scoped cannot invalidate anything.

        The record says somebody WAS hired, but `function: null` matches no
        function, so the falsifier reported VALID "looked, found nothing" from
        a file that plainly found something. The date field already refuses
        this way; function now does too.
        """
        evidence = self._write(
            "evidence.json",
            {
                "companies": {
                    "Northwind Analytics": {
                        "hires": [{"function": None, "date": "2026-06-14"}]
                    }
                }
            },
        )

        code, out, err = self._main(self.base_args(evidence=evidence))

        self._assert_refused(code, out, err, "function")

    def test_hire_with_a_non_string_function_is_refused(self):
        evidence = self._write(
            "evidence.json",
            {
                "companies": {
                    "Northwind Analytics": {
                        "hires": [{"function": 7, "date": "2026-06-14"}]
                    }
                }
            },
        )

        code, out, err = self._main(self.base_args(evidence=evidence))

        self._assert_refused(code, out, err, "function")

    def test_hire_with_a_missing_function_is_refused(self):
        evidence = self._write(
            "evidence.json",
            {"companies": {"Northwind Analytics": {"hires": [{"date": "2026-06-14"}]}}},
        )

        code, out, err = self._main(self.base_args(evidence=evidence))

        self._assert_refused(code, out, err, "function")

    def test_a_null_function_hire_cannot_be_reported_as_found_nothing(self):
        """The consequence: the run must not claim the company is clean."""
        evidence = self._write(
            "evidence.json",
            {
                "companies": {
                    "Northwind Analytics": {
                        "hires": [{"function": None, "date": "2026-06-14"}]
                    }
                }
            },
        )
        state = self._state(title="GTM Engineer")

        code, out, err = self._main(
            [
                "check",
                "--state",
                state,
                "--evidence",
                evidence,
                "--as-of",
                "2026-09-10",
            ]
        )

        self._assert_refused(code, out, err, "function")
        self.assertNotIn("no hires into", out)

    def test_evidence_hires_that_is_not_a_list_is_refused(self):
        evidence = self._write(
            "evidence.json", {"companies": {"Northwind": {"hires": "none"}}}
        )

        code, out, err = self._main(self.base_args(evidence=evidence))

        self._assert_refused(code, out, err, "hires")

    # --- site 3: recheck_interval_days is not an int ---------------------

    def test_non_integer_recheck_interval_is_refused(self):
        config = self._config(signal_class={"recheck_interval_days": "seven"})

        code, out, err = self._main(self.base_args(config=config))

        self._assert_refused(code, out, err, "recheck_interval_days")

    # --- site 4: a path that is a directory ------------------------------

    def test_state_path_that_is_a_directory_is_refused(self):
        code, out, err = self._main(
            ["check", "--state", self.tmp, "--as-of", "2026-09-10"]
        )

        self._assert_refused(code, out, err, "state file")

    def test_evidence_path_that_is_a_directory_is_refused(self):
        code, out, err = self._main(self.base_args(evidence=self.tmp))

        self._assert_refused(code, out, err, "evidence file")

    def test_config_path_that_is_a_directory_is_refused(self):
        code, out, err = self._main(self.base_args(config=self.tmp))

        self._assert_refused(code, out, err, "config file")

    # --- site 5: --out into a directory that does not exist --------------

    def test_out_path_in_a_nonexistent_directory_is_refused(self):
        target = os.path.join(self.tmp, "nope", "verdicts.json")

        code, out, err = self._main(
            self.base_args(evidence=SAMPLE_EVIDENCE, out=target)
        )

        self._assert_refused(code, out, err, target)

    def test_ledger_path_that_is_a_directory_is_refused_on_save(self):
        code, out, err = self._main(
            self.base_args(evidence=SAMPLE_EVIDENCE, ledger=self.tmp)
        )

        self._assert_refused(code, out, err, "ledger")

    # --- explicit null thresholds disarm a check -------------------------

    def test_explicit_null_thresholds_is_refused(self):
        """`thresholds: null` used to be laundered into {} and disarm the check.

        A disarmed threshold check reports VALID, which is the one outcome that
        must never happen by accident.
        """
        config = self._write(
            "config.json",
            {
                "falsifiers": {
                    "evergreen_age_ceiling": {
                        "check": "age_ceiling",
                        "statement": "Age is below the evergreen ceiling.",
                        "thresholds": None,
                    }
                },
                "signal_classes": {
                    "vacancy_duration": {
                        "recheck_interval_days": 7,
                        "falsifiers": ["evergreen_age_ceiling"],
                    }
                },
                "function_map": {"gtm": ["gtm"]},
            },
        )

        code, out, err = self._main(self.base_args(config=config))

        self._assert_refused(code, out, err, "thresholds")

    def test_a_null_threshold_config_cannot_report_an_old_posting_valid(self):
        """The consequence, stated as behavior: a 192-day posting must not pass."""
        config = self._write(
            "config.json",
            {
                "falsifiers": {
                    "evergreen_age_ceiling": {
                        "check": "age_ceiling",
                        "statement": "Age is below the evergreen ceiling.",
                        "thresholds": None,
                    }
                },
                "signal_classes": {
                    "vacancy_duration": {
                        "recheck_interval_days": 7,
                        "falsifiers": ["evergreen_age_ceiling"],
                    }
                },
                "function_map": {"gtm": ["gtm"]},
            },
        )
        state = self._state(title="GTM Engineer")

        code, out, err = self._main(
            [
                "check",
                "--state",
                state,
                "--config",
                config,
                "--as-of",
                "2026-09-10",
                "--ranking-only",
            ]
        )

        self._assert_refused(code, out, err, "thresholds")
        self.assertNotIn("li-1", out)

    def test_an_omitted_thresholds_key_is_still_fine(self):
        """Absent is not the same as explicitly null. Absent stays legal."""
        config = self._write(
            "config.json",
            {
                "falsifiers": {
                    "no_hires_since_posting": {
                        "check": "no_hires_since_posting",
                        "statement": "No hires since the posting date.",
                    }
                },
                "signal_classes": {
                    "vacancy_duration": {
                        "recheck_interval_days": 7,
                        "falsifiers": ["no_hires_since_posting"],
                    }
                },
                "function_map": {"gtm": ["gtm"]},
            },
        )

        code, _, err = self._main(self.base_args(config=config))

        self.assertEqual(code, 0, err)

    def test_an_empty_thresholds_object_is_still_fine(self):
        """`{}` is legitimate: no_hires_since_posting ships with exactly that."""
        config = self._config()

        code, _, err = self._main(self.base_args(config=config))

        self.assertEqual(code, 0, err)

    # --- function_map VALUES, not just the map ---------------------------

    def test_function_map_value_that_is_a_string_is_refused(self):
        """A bare string is iterated CHARACTER BY CHARACTER by map_function.

        So "growth engineering" makes nearly any title match, and the hire
        check silently scopes itself to a function the operator never meant.
        The config surface the README tells people to hand-edit must not
        accept this.
        """
        config = self._config(function_map={"gtm": "growth engineering"})

        code, out, err = self._main(self.base_args(config=config))

        self._assert_refused(code, out, err, "function_map")

    def test_function_map_value_that_is_an_int_is_refused(self):
        config = self._config(function_map={"gtm": 7})

        code, out, err = self._main(self.base_args(config=config))

        self._assert_refused(code, out, err, "function_map")

    def test_function_map_keyword_that_is_not_a_string_is_refused(self):
        config = self._config(function_map={"gtm": ["gtm", 7]})

        code, out, err = self._main(self.base_args(config=config))

        self._assert_refused(code, out, err, "function_map")

    def test_function_map_empty_keyword_is_refused(self):
        """An empty keyword is `"" in haystack`, which is True for every title."""
        config = self._config(function_map={"gtm": [""]})

        code, out, err = self._main(self.base_args(config=config))

        self._assert_refused(code, out, err, "function_map")

    def test_a_string_function_map_cannot_fabricate_an_affirmative_verdict(self):
        """The reviewer's construction, end to end.

        "Warehouse Associate" maps to no function, so it must fail closed to
        SUSPECT. With `"gtm": "growth engineering"` the character-wise scan
        matched on 'r', scoped the hire check to gtm, found no gtm hires, and
        reported VALID -- an affirmative claim manufactured from a typo, which
        then RANKED. The run must refuse at config load instead.
        """
        config = self._config(function_map={"gtm": "growth engineering"})
        state = self._state(title="Warehouse Associate")
        evidence = self._write(
            "evidence.json",
            {"companies": {"Northwind Analytics": {"hires": []}}},
        )

        code, out, err = self._main(
            [
                "check",
                "--state",
                state,
                "--evidence",
                evidence,
                "--config",
                config,
                "--as-of",
                "2026-09-10",
                "--ranking-only",
            ]
        )

        self._assert_refused(code, out, err, "function_map")
        self.assertNotIn("li-1", out, "a refused run must rank nothing")

    def test_a_well_formed_function_map_still_loads(self):
        """The guard must not reject the shape the repo config actually uses."""
        from sdd.config import DEFAULT_CONFIG_PATH, load_config

        config = load_config(DEFAULT_CONFIG_PATH)

        self.assertIn("gtm", config.function_map)
        self.assertIn("gtm", config.function_map["gtm"])

    # --- single_seat_patterns: the same validation class, one key over -----
    #
    # A single-seat pattern is the ONLY thing that can escalate a hire from
    # SUSPECT to INVALIDATED on the title alone. A malformed one that matches
    # every title therefore restores the exact pre-row-51 behaviour -- every
    # hire invalidates -- from a config that looks configured. Same failure
    # shape as function_map, so it gets the same refusal.

    def test_single_seat_patterns_that_is_a_string_is_refused(self):
        """A bare string is iterated CHARACTER BY CHARACTER, so "founding"
        becomes the patterns f, o, u, n, d, i, n, g -- and 'n' is in nearly
        every title. Every hire would then read as corroborated."""
        config = self._config(single_seat_patterns="founding")

        code, out, err = self._main(self.base_args(config=config))

        self._assert_refused(code, out, err, "single_seat_patterns")

    def test_single_seat_patterns_that_is_an_object_is_refused(self):
        config = self._config(single_seat_patterns={"founding": True})

        code, out, err = self._main(self.base_args(config=config))

        self._assert_refused(code, out, err, "single_seat_patterns")

    def test_single_seat_patterns_entry_that_is_not_a_string_is_refused(self):
        config = self._config(single_seat_patterns=["founding", 7])

        code, out, err = self._main(self.base_args(config=config))

        self._assert_refused(code, out, err, "single_seat_patterns")

    def test_single_seat_patterns_empty_entry_is_refused(self):
        """`"" in title` is True for every title, so one empty string makes
        every posting a single-seat posting."""
        config = self._config(single_seat_patterns=[""])

        code, out, err = self._main(self.base_args(config=config))

        self._assert_refused(code, out, err, "single_seat_patterns")

    def test_single_seat_patterns_whitespace_entry_is_refused(self):
        config = self._config(single_seat_patterns=["   "])

        code, out, err = self._main(self.base_args(config=config))

        self._assert_refused(code, out, err, "single_seat_patterns")

    def test_a_string_single_seat_patterns_cannot_invalidate_every_hire(self):
        """The consequence, end to end.

        "GTM Engineer" is not a single-seat title, so a lone hire must land
        SUSPECT and the posting must be held for review rather than thrown
        away. With `"single_seat_patterns": "founding"` the character-wise scan
        matches on 'n', reports corroborated-by-single-seat-title, and ranks the
        account as INVALIDATED on evidence that does not support it. The run
        must refuse at config load instead.
        """
        config = self._config(single_seat_patterns="founding")
        state = self._state(title="GTM Engineer")
        evidence = self._write(
            "evidence.json",
            {
                "companies": {
                    "Northwind Analytics": {
                        "hires": [{"function": "gtm", "date": "2026-06-14"}]
                    }
                }
            },
        )

        code, out, err = self._main(
            [
                "check",
                "--state",
                state,
                "--evidence",
                evidence,
                "--config",
                config,
                "--as-of",
                "2026-09-10",
            ]
        )

        self._assert_refused(code, out, err, "single_seat_patterns")
        self.assertNotIn("single-seat", out)

    def test_an_absent_single_seat_patterns_key_still_loads(self):
        """Absence is an empty list: no title corroborates, every hire lands
        SUSPECT. That is the fail-closed direction, so absence is legal where
        malformation is not."""
        from sdd.config import load_config

        config_path = self._config()  # the helper writes no such key
        config = load_config(config_path)

        self.assertEqual(config.single_seat_patterns, ())

    # --- a refused run must not have already changed state ---------------

    def test_a_failed_out_write_leaves_the_ledger_untouched(self):
        """Exit 2 means nothing happened, including to the ledger.

        The --out write used to run AFTER ledger.save(), so a refused run had
        already overwritten the pre-run ledger. That destroys the baseline the
        next run compares against, and the operator has no way to know.
        """
        ledger = os.path.join(self.tmp, "ledger.json")
        before = {
            "version": 1,
            "signals": {
                "li-9000000001": {"last_checked": "2026-09-01", "verdict": "VALID"}
            },
        }
        with open(ledger, "w", encoding="utf-8") as handle:
            json.dump(before, handle)

        code, out, err = self._main(
            self.base_args(evidence=SAMPLE_EVIDENCE)
            + ["--ledger", ledger, "--out", os.path.join(self.tmp, "nope", "v.json")]
        )

        self._assert_refused(code, out, err, "--out")
        with open(ledger, "r", encoding="utf-8") as handle:
            after = json.load(handle)
        self.assertEqual(after, before, "a refused run must not rewrite the ledger")

    def test_a_successful_run_still_writes_both_the_ledger_and_out(self):
        """The reorder must not cost either write on the happy path."""
        ledger = os.path.join(self.tmp, "ledger.json")
        out_path = os.path.join(self.tmp, "verdicts.json")

        code, _, err = self._main(
            self.base_args(evidence=SAMPLE_EVIDENCE)
            + ["--ledger", ledger, "--out", out_path]
        )

        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.exists(ledger))
        self.assertTrue(os.path.exists(out_path))
        with open(out_path, "r", encoding="utf-8") as handle:
            self.assertEqual(len(json.load(handle)["verdicts"]), 5)

    # --- the same shape, one level up: config sections -------------------

    def test_config_falsifiers_section_that_is_not_an_object_is_refused(self):
        config = self._write("config.json", {"falsifiers": [], "signal_classes": {}})

        code, out, err = self._main(self.base_args(config=config))

        self._assert_refused(code, out, err, "falsifiers")

    def test_config_falsifier_entry_that_is_not_an_object_is_refused(self):
        config = self._write(
            "config.json", {"falsifiers": {"bad": "age_ceiling"}, "signal_classes": {}}
        )

        code, out, err = self._main(self.base_args(config=config))

        self._assert_refused(code, out, err, "bad")


class TestCleanCloneUsability(unittest.TestCase):
    """Definition of done: runs from a clean clone with no machine state."""

    def test_runs_as_a_module_from_an_unrelated_working_directory(self):
        tmp = tempfile.mkdtemp()
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sdd",
                    "check",
                    "--state",
                    SAMPLE_STATE,
                    "--evidence",
                    SAMPLE_EVIDENCE,
                    "--as-of",
                    "2026-09-10",
                ],
                cwd=tmp,
                env=dict(os.environ, PYTHONPATH=HERE),
                capture_output=True,
                text=True,
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Northwind Analytics", proc.stdout)

    def test_the_repo_contains_no_absolute_path_to_the_authors_machine(self):
        """A path baked in here is machine state, and the tool must not carry it."""
        offenders = []
        for root, dirs, files in os.walk(HERE):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
            for name in files:
                if not name.endswith((".py", ".json", ".md")):
                    continue
                path = os.path.join(root, name)
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    body = handle.read()
                if "/Users/derr" in body and name != "tests.py":
                    offenders.append(path)
        self.assertEqual(offenders, [])




class ReadmeFreshnessTest(unittest.TestCase):
    """The README's stated test count must equal the live suite. This artifact's
    own thesis is that stated numbers drift from the facts beneath them; the
    readme_test_count check is that thesis applied to the repo itself. When you
    add a test, the README's counts change in the SAME commit or this goes red."""

    def test_readme_test_count_matches_the_suite(self):
        import sys
        loader = unittest.TestLoader()
        live = loader.loadTestsFromModule(sys.modules[__name__]).countTestCases()
        readme = open(os.path.join(os.path.dirname(__file__), "README.md")).read()
        stated = re.findall(r"(?<!\w)(\d+) tests", readme)
        self.assertTrue(stated, "README no longer states a test count anywhere")
        for n in stated:
            self.assertEqual(int(n), live,
                f"README says {n} tests; the suite runs {live}. Same-commit rule: "
                f"update every count in README.md in the commit that changed the suite.")

if __name__ == "__main__":
    unittest.main(verbosity=2)

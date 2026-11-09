"""SQLite integration tests using temporary databases and synthetic facts only."""

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import monitor
from seat_history import CourseObservation, HistoryError, SeatHistory
import demo_history


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "history.sqlite3"
        self.store = SeatHistory(self.path)
        self.store.initialize()
        self.start = datetime(2026, 11, 9, tzinfo=timezone.utc)
        self.course = CourseObservation("202710", "IT", "304", "DL1", "17582", 0,
                                        "0 of 10 unreserved")

    def record(self, index, course=None, **kwargs):
        return self.store.record_check(str(index), self.start + timedelta(minutes=5 * index),
                                       0.5, [course or self.course], **kwargs)

    def test_initialization_is_idempotent_and_persistent(self):
        self.record(0)
        reopened = SeatHistory(self.path)
        reopened.initialize()
        self.assertEqual(reopened.statistics()["completed_checks"], 1)

    def test_schema_keys_indexes_and_foreign_keys(self):
        with self.store._connection() as db:
            self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(tables, {"checks", "observations", "opening_events"})
            self.assertEqual(len(db.execute("PRAGMA foreign_key_list(opening_events)").fetchall()), 2)
            self.assertEqual(len(db.execute("PRAGMA foreign_key_list(observations)").fetchall()), 1)
            self.assertIn("idx_observations_course_time",
                          {r[1] for r in db.execute("PRAGMA index_list(observations)")})
        self.record(0)
        with self.assertRaises(HistoryError), self.store._connection() as db:
            db.execute("DELETE FROM checks")

    def test_insert_history_latest_and_utc_conversion(self):
        self.store.record_check("tz", self.start.astimezone(timezone(timedelta(hours=-5))),
                                1, [self.course])
        self.record(1, replace(self.course, total_available=2, reserved_summary=None))
        rows = self.store.history("202710", "17582")
        self.assertEqual([r["total_available"] for r in rows], [0, 2])
        self.assertEqual(rows[0]["observed_at_utc"], "2026-11-09T00:00:00.000000+00:00")
        self.assertEqual(self.store.latest("202710", "17582")["classification"], "possible")
        self.assertIsNone(self.store.latest("202710", "99999"))
        self.assertEqual(self.store.history("202710", "' OR 1=1 --"), [])

    def test_unreserved_increase_no_change_decrease_and_reopening(self):
        for i, seats in enumerate((0, 1, 1, 0, 2)):
            self.record(i, replace(self.course, total_available=seats,
                                   reserved_summary=f"{seats} of 10 unreserved"))
        events = self.store.opening_events("202710", "17582")
        self.assertEqual([(e["previous_available"], e["new_available"]) for e in events],
                         [(0, 1), (0, 2)])
        self.assertTrue(all(e["classification"] == "unreserved_increase" for e in events))

    def test_first_sample_is_baseline(self):
        self.record(0, replace(self.course, total_available=4, reserved_summary=None))
        self.assertEqual(self.store.statistics()["opening_events"], 0)

    def test_reserved_only_increases_excluded_then_release_detected(self):
        for i, seats in enumerate((9, 10)):
            self.record(i, replace(self.course, total_available=seats))
        self.assertEqual(self.store.statistics()["opening_events"], 0)
        self.record(2, replace(self.course, total_available=10,
                               reserved_summary={"unreservedAvailable": 1, "reservedAvailable": 9}))
        self.assertEqual(self.store.statistics()["opening_events"], 1)

    def test_monitor_eligibility_parity(self):
        for total in (None, 0, 1, 9):
            for summary in (None, "", {}, "unknown restrictions", "0 of 10 unreserved",
                            "1 of 10 unreserved", {"unreservedRemaining": 20}):
                with self.subTest(total=total, summary=summary):
                    row = replace(self.course, total_available=total, reserved_summary=summary)
                    self.assertEqual(row.values()[-1], monitor.alertable_open_seats({
                        "seatsAvailable": total, "reservedSeatSummary": summary}))

    def test_unknown_classifications_and_missing_total_not_false_baseline(self):
        self.record(0, replace(self.course, total_available=None))
        self.record(1, replace(self.course, total_available=1, reserved_summary=None))
        self.assertEqual(self.store.statistics()["opening_events"], 0)
        self.record(2, replace(self.course, total_available=2, reserved_summary="restrictions unknown"))
        self.assertEqual(self.store.latest("202710", "17582")["classification"], "unknown")

    def test_identical_retry_deduplicates_check_observation_and_event(self):
        self.record(0)
        opening = replace(self.course, total_available=1, reserved_summary="1 of 10 unreserved")
        self.record(1, opening)
        self.record(1, opening)
        self.assertEqual(self.store.statistics(), dict(completed_checks=2, successful_checks=2,
            failed_checks=0, success_rate_percent=100, observations=2, opening_events=1))
        with self.assertRaises(ValueError):
            self.record(1)

    def test_database_rejects_duplicate_event_and_invalid_count(self):
        self.record(0)
        self.record(1, replace(self.course, total_available=1, reserved_summary=None))
        with self.assertRaises(HistoryError), self.store._connection() as db:
            db.execute("INSERT INTO opening_events (observation_id, previous_observation_id, "
                       "previous_available, new_available, detected_at_utc, classification) "
                       "SELECT observation_id, previous_observation_id, previous_available, "
                       "new_available, detected_at_utc, classification FROM opening_events")
        with self.assertRaises(HistoryError), self.store._connection() as db:
            db.execute("UPDATE observations SET total_available=-1")

    def test_failed_check_and_partial_failure(self):
        self.store.record_failure("failure", self.start, 20, "TIMEOUT")
        self.record(1, status="failed", error_code="COURSE_LOOKUP_FAILED")
        stats = self.store.statistics()
        self.assertEqual((stats["failed_checks"], stats["observations"], stats["success_rate_percent"]),
                         (2, 1, 0))
        with self.store._connection() as db:
            self.assertEqual(db.execute("SELECT error_code FROM checks WHERE check_id='failure'").fetchone()[0], "TIMEOUT")

    def test_failed_retry_is_idempotent(self):
        for _ in range(2):
            self.store.record_failure("timeout", self.start, 1, "TIMEOUT")
        self.assertEqual(self.store.statistics()["failed_checks"], 1)

    def test_concurrent_identical_retries_create_one_event(self):
        self.record(0)
        opening = replace(self.course, total_available=1, reserved_summary=None)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.record(1, opening), range(2)))
        self.assertEqual(results, ["1", "1"])
        self.assertEqual(self.store.statistics()["opening_events"], 1)
        self.assertEqual(self.store.statistics()["observations"], 2)

    def test_missing_foreign_key_rejected(self):
        self.record(0)
        with self.assertRaises(HistoryError), self.store._connection() as db:
            db.execute("UPDATE observations SET check_id='nonexistent'")

    def test_multicourse_validation_failure_rolls_back_prior_inserts(self):
        second = replace(self.course, crn="17593", course_number="341")
        self.record(2, second)
        with self.assertRaises(ValueError):
            self.store.record_check("batch", self.start, 1, [self.course, second])
        self.assertEqual(self.store.history("202710", "17582"), [])
        self.assertEqual(self.store.statistics()["completed_checks"], 1)

    def test_empty_statistics(self):
        stats = self.store.statistics()
        self.assertEqual(stats["completed_checks"], 0)
        self.assertIsNone(stats["success_rate_percent"])
        self.assertEqual(self.store.openings_by_day("202710", "17582"), [])

    def test_daily_analytics_include_zero_days(self):
        self.record(0)
        self.record(1, replace(self.course, total_available=1, reserved_summary=None))
        self.record(288)
        self.store.record_failure("failed", self.start, 1, "TIMEOUT")
        self.assertEqual(self.store.openings_by_day("202710", "17582"), [
            {"day_utc": "2026-11-09", "sampled_observations": 2, "opening_events": 1},
            {"day_utc": "2026-11-10", "sampled_observations": 1, "opening_events": 0}])
        self.assertEqual(self.store.statistics()["success_rate_percent"], 75)

    def test_term_and_course_baselines_are_isolated(self):
        self.record(0)
        self.record(1, replace(self.course, term="202810", total_available=2, reserved_summary=None))
        self.record(2, replace(self.course, crn="17593", course_number="341",
                               total_available=3, reserved_summary=None))
        self.assertEqual(self.store.statistics()["opening_events"], 0)

    def test_order_and_identity_rejected_atomically(self):
        self.record(2)
        for index, course in ((1, self.course), (3, replace(self.course, section="DL2"))):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.record(index, course)
        self.assertEqual(self.store.statistics()["completed_checks"], 1)

    def test_duplicate_course_rejected_even_with_missing_count(self):
        with self.assertRaises(ValueError):
            self.store.record_check("dup", self.start, 1,
                                    [self.course, replace(self.course, total_available=None)])
        self.assertEqual(self.store.statistics()["completed_checks"], 0)

    def test_invalid_course_data(self):
        for changes in ({"total_available": True}, {"total_available": -1},
                        {"total_available": "1"}, {"subject": "it"}, {"term": None},
                        {"crn": "invalid"}, {"reserved_summary": []},
                        {"reserved_summary": {"studentName": "private"}},
                        {"reserved_summary": {"unreservedAvailable": True}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.record(0, replace(self.course, **changes))
        self.assertEqual(self.store.statistics()["completed_checks"], 0)

    def test_invalid_check_metadata(self):
        for args in (("bad id", self.start, 1), ("id", datetime(2026, 1, 1), 1),
                     ("id", self.start, float("nan")), ("id", self.start, float("inf")),
                     ("id", self.start, -1), ("id", self.start, True)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.store.record_check(*args, [self.course])
        for kwargs in ({"status": "partial"}, {"error_code": "TIMEOUT"},
                       {"status": "failed"}, {"status": "failed", "error_code": "secret text"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.record(0, **kwargs)
        with self.assertRaises(ValueError):
            self.store.record_check("empty", self.start, 1)
        with self.assertRaises(ValueError):
            self.store.record_check("bad", self.start, 1, [{}])

    def test_connection_errors_are_wrapped(self):
        with self.assertRaises(HistoryError):
            SeatHistory(Path(self.temp.name) / "missing" / "history.db").initialize()
        self.path.write_bytes(b"not a database")
        with self.assertRaises(HistoryError):
            self.store.statistics()

    def test_write_failure_rolls_back_entire_check(self):
        self.record(0)
        with self.store._connection() as db:
            db.execute("""CREATE TRIGGER reject_event BEFORE INSERT ON opening_events
                          BEGIN SELECT RAISE(ABORT, 'synthetic error'); END""")
        with self.assertRaises(HistoryError):
            self.record(1, replace(self.course, total_available=1, reserved_summary=None))
        self.assertEqual(self.store.statistics()["completed_checks"], 1)
        self.assertEqual(len(self.store.history("202710", "17582")), 1)

    def test_demo_is_offline(self):
        with patch("requests.sessions.Session.request", side_effect=AssertionError("network")), \
                patch.object(monitor, "notify", side_effect=AssertionError("notification")):
            demo_history.demo()


if __name__ == "__main__":
    unittest.main()

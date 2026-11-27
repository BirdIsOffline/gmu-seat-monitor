"""Offline regressions for cached state and conservative seat interpretation."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import monitor
from seat_history import CourseObservation


def seats(summary, total=4):
    return {"seatsAvailable": total, "reservedSeatSummary": summary}


class CachedStateTests(unittest.TestCase):
    def test_valid_json_with_invalid_top_level_types_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            for value in (None, False, True, 0, 1.5, "text", [], [{"17593": {}}]):
                with self.subTest(value=value), patch.object(monitor, "STATE_PATH", path):
                    path.write_text(json.dumps(value), encoding="utf-8")
                    with contextlib.redirect_stdout(io.StringIO()) as output:
                        self.assertEqual(monitor.load_state(), {})
                    self.assertIn("Previous state could not be read.", output.getvalue())

    def test_valid_active_and_retired_entries_survive_loading(self):
        state = {"17582": seats("1 of 10 unreserved", 1),
                 "17593": seats({"unreservedAvailable": 2})}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(json.dumps(state), encoding="utf-8")
            before = path.read_bytes()
            with patch.object(monitor, "STATE_PATH", path):
                self.assertEqual(monitor.load_state(), state)
            self.assertEqual(path.read_bytes(), before)

    def test_polling_active_course_preserves_retired_cached_history(self):
        retired = seats({"unreservedAvailable": 1}, 1)
        initial = {"17582": retired, "17593": seats(None, 0)}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(json.dumps(initial), encoding="utf-8")
            with (patch.object(monitor, "STATE_PATH", path),
                  patch.object(monitor, "COURSES", {"17593": monitor.COURSES["17593"]}),
                  patch.object(monitor, "get_section", return_value=seats(None, 0)) as lookup,
                  patch.object(monitor, "notify") as notify,
                  patch.object(monitor.time, "sleep"),
                  contextlib.redirect_stdout(io.StringIO())):
                state = monitor.load_state()
                self.assertEqual(monitor.check_once("202710", state), 0)
                self.assertEqual(monitor.load_state()["17582"], retired)
            lookup.assert_called_once_with("202710", "17593", "341")
            notify.assert_not_called()

    def test_invalid_cache_retains_existing_cold_start_alert_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("[]", encoding="utf-8")
            with (patch.object(monitor, "STATE_PATH", path),
                  patch.object(monitor, "COURSES", {"17593": monitor.COURSES["17593"]}),
                  patch.object(monitor, "get_section", return_value=seats({"unreservedAvailable": 1})),
                  patch.object(monitor, "notify", return_value=True) as notify,
                  patch.object(monitor.time, "sleep"),
                  contextlib.redirect_stdout(io.StringIO())):
                self.assertEqual(monitor.check_once("202710", monitor.load_state()), 0)
            self.assertEqual(notify.call_count, 1)
            self.assertIs(notify.call_args.kwargs["actionable"], True)


class ReservationConsistencyTests(unittest.TestCase):
    def test_conflicting_recognized_keys_are_unknown_in_both_orders(self):
        items = [("unreservedAvailable", 0), ("unreservedRemaining", 3)]
        for summary in (dict(items), dict(reversed(items))):
            with self.subTest(summary=summary):
                self.assertIsNone(monitor.parse_unreserved(summary))
                self.assertEqual(monitor.alertable_open_seats(seats(summary)), 0)
                self.assertFalse(monitor.possible_eligible(seats(summary)))
                self.assertIn("Eligibility UNKNOWN", monitor.seat_message("IT 341 DL1", seats(summary)))
                values = CourseObservation("202710", "IT", "341", "DL1", "17593", 4, summary).values()
                self.assertIsNone(values[6])
                self.assertEqual(values[-2:], ("unknown", 0))

    def test_agreeing_aliases_have_one_order_independent_count(self):
        items = [(key, 2) for key in ("unreservedAvailable", "unreservedRemaining",
                                     "unreservedSeatsAvailable", "unreservedSeatsRemaining")]
        for summary in (dict(items), dict(reversed(items))):
            self.assertEqual(monitor.parse_unreserved(summary), 2)
            self.assertEqual(monitor.alertable_open_seats(seats(summary)), 2)

    def test_invalid_recognized_value_cannot_be_overridden_by_valid_key(self):
        for value in (True, False, -1, 1_000_001, "2", None, [], {}):
            items = [("unreservedAvailable", 2), ("unreservedRemaining", value)]
            for summary in (dict(items), dict(reversed(items))):
                with self.subTest(value=value, summary=summary):
                    self.assertIsNone(monitor.parse_unreserved(summary))
                    self.assertEqual(monitor.alertable_open_seats(seats(summary)), 0)

    def test_unrecognized_keys_never_contribute_an_actionable_count(self):
        for summary in ({"studentUnreservedAvailable": 2}, {"unreservedavailable": 2},
                        {"reservedAvailable": 2}, [2], "restricted"):
            with self.subTest(summary=summary):
                self.assertIsNone(monitor.parse_unreserved(summary))
                self.assertEqual(monitor.alertable_open_seats(seats(summary)), 0)

    def test_absent_reservations_remain_distinct_from_ambiguous_information(self):
        for summary in (None, "", [], {}):
            self.assertEqual(monitor.alertable_open_seats(seats(summary)), 4)
            self.assertIn("no reserved-seat summary", monitor.seat_message("IT 341 DL1", seats(summary)))
        for summary in ("unknown", {"unreservedAvailable": 0, "unreservedRemaining": 2}):
            self.assertEqual(monitor.alertable_open_seats(seats(summary)), 0)
            self.assertIn("Eligibility UNKNOWN", monitor.seat_message("IT 341 DL1", seats(summary)))

    def test_conflicting_text_counts_are_unknown_in_both_orders(self):
        parts = ["0 of 10 unreserved", "unreserved seats remaining: 3"]
        for summary in ("; ".join(parts), "; ".join(reversed(parts))):
            self.assertIsNone(monitor.parse_unreserved(summary))
            self.assertEqual(monitor.alertable_open_seats(seats(summary)), 0)

    def test_repeated_agreeing_text_counts_remain_actionable(self):
        summary = "2 of 10 unreserved; unreserved seats remaining: 2; unreserved: 2 of 10"
        self.assertEqual(monitor.parse_unreserved(summary), 2)
        self.assertEqual(monitor.alertable_open_seats(seats(summary)), 2)

    def test_text_counts_and_length_remain_bounded(self):
        for summary in ("1000001 of 2000000 unreserved", "-1 of 10 unreserved",
                        "unreserved seats remaining: -1", "1 of 10 unreserved" + "x" * 2000):
            with self.subTest(summary=summary[:60]):
                self.assertIsNone(monitor.parse_unreserved(summary))
                self.assertEqual(monitor.alertable_open_seats(seats(summary)), 0)
        self.assertEqual(monitor.parse_unreserved("1000000 of 1000000 unreserved"), 1_000_000)

    def test_strict_history_validation_still_rejects_out_of_bounds_text(self):
        for summary in ("1000001 of 2000000 unreserved", "-1 of 10 unreserved"):
            with self.subTest(summary=summary):
                with self.assertRaises(ValueError):
                    monitor.parse_unreserved(summary, strict_bounds=True)
                with self.assertRaises(ValueError):
                    CourseObservation("202710", "IT", "341", "DL1", "17593", 4, summary).values()

    def test_supported_text_separators_are_preserved(self):
        for summary in ("unreserved seats remaining - 2", "unreserved seats remaining=2",
                        "unreserved seats available: 2", "unreserved - 2 of 10"):
            with self.subTest(summary=summary):
                self.assertEqual(monitor.parse_unreserved(summary), 2)

    def test_possible_eligible_matches_canonical_actionable_count(self):
        summaries = [None, "restricted", {"unreservedAvailable": 0}, {"unreservedAvailable": 9},
                     {"unreservedAvailable": 0, "unreservedRemaining": 3}]
        for total in (None, False, True, -1, 0, "2", 1, 4, 1_000_000, 1_000_001):
            for summary in summaries:
                data = seats(summary, total)
                with self.subTest(total=total, summary=summary):
                    self.assertEqual(monitor.possible_eligible(data), monitor.alertable_open_seats(data) > 0)
        self.assertEqual(monitor.alertable_open_seats(seats(None, 1_000_001)), 0)

    def test_ambiguous_current_observation_does_not_notify(self):
        items = [("unreservedAvailable", 0), ("unreservedRemaining", 3)]
        for summary in (dict(items), dict(reversed(items))):
            with (self.subTest(summary=summary),
                  patch.object(monitor, "COURSES", {"17593": monitor.COURSES["17593"]}),
                  patch.object(monitor, "get_section", return_value=seats(summary)),
                  patch.object(monitor, "notify") as notify,
                  patch.object(monitor, "save_state"), patch.object(monitor.time, "sleep"),
                  contextlib.redirect_stdout(io.StringIO())):
                state = {"17593": seats(None, 0)}
                self.assertEqual(monitor.check_once("202710", state), 0)
                notify.assert_not_called()
                self.assertEqual(state["17593"]["reservedSeatSummary"], summary)

    def test_operational_workflow_remains_dispatch_only_and_approval_gated(self):
        source = (Path(__file__).resolve().parents[1] / ".github/workflows/seat-monitor.yml").read_text(encoding="utf-8")
        self.assertIn("if: vars.GMU_POLLING_APPROVED == 'true' && github.event_name == 'workflow_dispatch' && inputs.mode == 'check'", source)
        self.assertNotIn("github.event_name == 'schedule'", source)
        self.assertNotIn("schedule:", source)


if __name__ == "__main__":
    unittest.main()

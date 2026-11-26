"""Active/retired lifecycle regressions; public synthetic facts and mocked I/O only."""
import contextlib
from datetime import datetime, timezone, timedelta
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

import monitor
import health_runner
from cloud_history import prepare_check, validate_prepared
from seat_history import CourseObservation, SeatHistory

ROOT = Path(__file__).resolve().parents[1]
ACTIVE_LINE = "[2026-11-09 01:00:00] IT 341 DL1: 1 unreserved seats available; 1 total seats visible.\n"


class CourseStatusTests(unittest.TestCase):
    def setUp(self):
        legacy = {crn: c for crn, c in monitor.COURSES.items() if crn in ("17582", "17593")}
        self.enterContext(patch.object(monitor, "COURSES", legacy))
        self.enterContext(patch.object(health_runner, "COURSES", legacy))

    def test_retired_lookup_alert_and_capture_are_skipped_and_cached_state_preserved(self):
        old = {"seatsAvailable": 9, "reservedSeatSummary": "0 of 10 unreserved"}
        state = {"17582": dict(old), "17593": {"seatsAvailable": 0}}
        current = {"seatsAvailable": 1, "reservedSeatSummary": "1 of 10 unreserved"}
        capture = Mock()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seat_state.json"
            with (patch.object(monitor, "STATE_PATH", path),
                  patch.object(monitor, "get_section", return_value=current) as lookup,
                  patch.object(monitor, "notify") as notify,
                  patch.object(monitor.time, "sleep"), contextlib.redirect_stdout(io.StringIO())):
                self.assertEqual(monitor.check_once("202710", state, capture), 0)
            lookup.assert_called_once_with("202710", "17593", "341")
            notify.assert_called_once()
            self.assertIn("IT 341 DL1", notify.call_args.args[0])
            self.assertNotIn("IT 304", str(notify.call_args))
            self.assertEqual(state["17582"], old)
            self.assertEqual(json.loads(path.read_text())["17582"], old)
            capture.observe.assert_called_once_with("202710", "17593", "341", monitor.snapshot(current))
            self.assertTrue(health_runner.state_is_valid(path))

    def test_it341_no_change_and_reserved_only_changes_remain_silent(self):
        for current in ({"seatsAvailable": 0},
                        {"seatsAvailable": 9, "reservedSeatSummary": "0 of 10 unreserved"}):
            with (self.subTest(current=current),
                  patch.object(monitor, "get_section", return_value=current) as lookup,
                  patch.object(monitor, "notify") as notify,
                  patch.object(monitor, "save_state"), patch.object(monitor.time, "sleep"),
                  contextlib.redirect_stdout(io.StringIO())):
                self.assertEqual(monitor.check_once("202710", {"17593": {"seatsAvailable": 0}}), 0)
                lookup.assert_called_once_with("202710", "17593", "341")
                notify.assert_not_called()

    def test_it341_opening_notifies_once_then_unchanged_sample_is_silent(self):
        state = {"17593": {"seatsAvailable": 0}}
        current = {"seatsAvailable": 1, "reservedSeatSummary": "1 of 10 unreserved"}
        with (patch.object(monitor, "get_section", return_value=current) as lookup,
              patch.object(monitor, "notify") as notify,
              patch.object(monitor, "save_state"), patch.object(monitor.time, "sleep"),
              contextlib.redirect_stdout(io.StringIO())):
            self.assertEqual(monitor.check_once("202710", state), 0)
            self.assertEqual(monitor.check_once("202710", state), 0)
        self.assertEqual(lookup.call_count, 2)
        self.assertTrue(all(call.args == ("202710", "17593", "341") for call in lookup.call_args_list))
        notify.assert_called_once()
        self.assertIn("IT 341 DL1", notify.call_args.args[0])

    def test_it341_lookup_failure_is_not_hidden_by_retired_course(self):
        state = {"17582": {"seatsAvailable": 1}}
        with (patch.object(monitor, "get_section", side_effect=monitor.SearchError("synthetic")) as lookup,
              patch.object(monitor, "save_state") as save, patch.object(monitor, "notify") as notify,
              contextlib.redirect_stdout(io.StringIO())):
            self.assertEqual(monitor.check_once("202710", state), 1)
        lookup.assert_called_once_with("202710", "17593", "341")
        notify.assert_not_called();save.assert_not_called()
        self.assertEqual(state, {"17582": {"seatsAvailable": 1}})

    def test_invalid_or_missing_status_fails_before_polling(self):
        for status in (None, "ACTIVE", "retire", True):
            with (self.subTest(status=status), patch.object(monitor, "COURSES", {
                    "17593": {"name": "IT 341 DL1", "number": "341", "status": status}}),
                  patch.object(monitor, "get_section") as lookup):
                with self.assertRaises(ValueError):
                    monitor.check_once("202710", {})
                lookup.assert_not_called()

    def test_all_retired_exits_without_term_lookup_state_or_capture(self):
        retired = {crn: dict(c, status="retired") for crn, c in monitor.COURSES.items()}
        with (patch.object(monitor, "COURSES", retired),
              patch.object(sys, "argv", ["monitor.py", "--once"]),
              patch.object(monitor, "resolve_term_code") as term,
              patch.object(monitor, "start_history_capture") as capture,
              patch.object(monitor, "load_state") as load,
              contextlib.redirect_stdout(io.StringIO())):
            self.assertEqual(monitor.main(), 0)
        term.assert_not_called();capture.assert_not_called();load.assert_not_called()

    def test_cli_describes_only_active_targets(self):
        output = io.StringIO()
        with (patch.object(sys, "argv", ["monitor.py", "--once"]),
              patch.object(monitor, "resolve_term_code", return_value="202710"),
              patch.object(monitor, "check_once", return_value=0),
              patch.object(monitor, "start_history_capture", return_value=None),
              patch.object(monitor, "load_state", return_value={}),
              contextlib.redirect_stdout(output)):
            self.assertEqual(monitor.main(), 0)
        self.assertIn("Watching: IT 341 DL1 CRN 17593", output.getvalue())
        self.assertIn("1 active, 1 retired", output.getvalue())
        self.assertNotIn("Watching: IT 304", output.getvalue())

    def test_test_notification_description_excludes_retired_course(self):
        with (patch.object(sys, "argv", ["monitor.py", "--test-notification"]),
              patch.object(monitor, "notify", return_value=True) as notify):
            self.assertEqual(monitor.main(), 0)
        self.assertIn("IT 341 DL1", notify.call_args.args[1])
        self.assertNotIn("IT 304", str(notify.call_args))

    def test_health_accepts_active_success_and_marks_retired_without_lookup(self):
        success, courses = health_runner.summarize_output(ACTIVE_LINE, 0)
        self.assertTrue(success)
        self.assertEqual([r["status"] for r in courses], ["retired", "success"])
        self.assertIn("History only", courses[0]["detail"])

    def test_health_report_counts_only_active_lookups(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "health.json"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = health_runner.run_once(
                    runner=Mock(return_value=Mock(returncode=0, stdout=ACTIVE_LINE, stderr="")),
                    report_path=path)
            self.assertEqual(result, 0)
            self.assertIn("1/1 active course checks", output.getvalue())
            self.assertEqual([c["status"] for c in json.loads(path.read_text())["courses"]],
                             ["retired", "success"])

    def test_retired_output_cannot_satisfy_missing_active_result(self):
        success, courses = health_runner.summarize_output(ACTIVE_LINE.replace("341", "304"), 0)
        self.assertFalse(success)
        self.assertEqual([r["status"] for r in courses], ["retired", "not_reported"])
        self.assertFalse(health_runner.summarize_output(ACTIVE_LINE, 1)[0])

    def test_cache_validation_accepts_retired_history_without_requiring_new_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seat_state.json"
            path.write_text(json.dumps({"17582": {"seatsAvailable": 0}}))
            self.assertTrue(health_runner.state_is_valid(path))
            path.write_text(json.dumps({"99999": {"seatsAvailable": 0}}))
            self.assertFalse(health_runner.state_is_valid(path))

    def test_retired_history_preparation_validation_and_retries_keep_identical_bytes(self):
        observation = CourseObservation("202710", "IT", "304", "DL1", "17582", 1)
        check_id, at = str(uuid.uuid4()), datetime(2026, 11, 9, tzinfo=timezone.utc)
        first = prepare_check(check_id, at, .25, [observation])
        validate_prepared(first)
        active = {crn: dict(c, status="active") for crn, c in monitor.COURSES.items()}
        with patch.object(monitor, "COURSES", active):
            self.assertEqual(first, prepare_check(check_id, at, .25, [observation]))
        self.assertNotIn(b'"status":"retired"', first.body)
        self.assertEqual(json.loads(first.body)["observations"][0]["crn"], "17582")

    def test_retired_sqlite_observations_and_opening_events_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SeatHistory(Path(tmp) / "history.sqlite3");store.initialize()
            at = datetime(2026, 11, 9, tzinfo=timezone.utc)
            for i in (0, 1):
                store.record_check(f"retired-{i}", at + timedelta(minutes=i*5), .25,
                    [CourseObservation("202710", "IT", "304", "DL1", "17582", i)])
            before = store.history("202710", "17582")
            events = store.opening_events("202710", "17582")
            self.assertEqual(len(before), 2);self.assertEqual(len(events), 1)
            store.record_check("active-1", at + timedelta(minutes=10), .25,
                [CourseObservation("202710", "IT", "341", "DL1", "17593", 0)])
            self.assertEqual(store.history("202710", "17582"), before)
            self.assertEqual(store.opening_events("202710", "17582"), events)

    def test_polling_approval_gate_is_preserved(self):
        source = (ROOT / ".github/workflows/seat-monitor.yml").read_text()
        self.assertIn("if: vars.GMU_POLLING_APPROVED == 'true' &&", source)
        self.assertIn("Check active courses once", source)


class CurrentCourseConfigurationTests(unittest.TestCase):
    def test_configuration_keeps_retired_history_and_both_active_targets(self):
        self.assertEqual(set(monitor.COURSES), {"17582", "17593", "21051"})
        self.assertEqual(monitor.COURSES["17582"]["status"], "retired")
        self.assertEqual(list(monitor.active_courses()), ["17593", "21051"])

    def test_dashboard_statuses_match_monitor_configuration(self):
        source = (ROOT / "dashboard/worker.js").read_text(encoding="utf-8")
        data = json.loads(re.search(r"export const COURSES = (\[.*?\]);", source, re.S)[1])
        self.assertEqual({c["crn"]: (c["name"], c["status"], c["number"], c["section"]) for c in data},
                         {crn: (c["name"], c["status"], c["number"], c["section"]) for crn, c in monitor.COURSES.items()})

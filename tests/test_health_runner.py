"""Offline tests of the cloud health wrapper; no university or Bark requests."""
import contextlib
import io
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import monitor

import health_runner

GOOD_LOG = (
    "GMU public class search monitor — READ ONLY\n"
    "[2026-11-09 01:00:00] IT 304 DL1: 0 unreserved seats available; 0 total seats visible.\n"
    "[2026-11-09 01:00:02] IT 341 DL1: 0 unreserved seats available; 9 total seats visible.\n"
)


class HealthReportTests(unittest.TestCase):
    def test_both_courses_successful(self):
        success, courses = health_runner.summarize_output(GOOD_LOG, 0)
        self.assertTrue(success)
        self.assertEqual([row["status"] for row in courses], ["success", "success"])

    def test_one_course_missing_causes_failure(self):
        success, courses = health_runner.summarize_output(GOOD_LOG.splitlines()[1] + "\n", 0)
        self.assertFalse(success)
        self.assertEqual(courses[1]["status"], "not_reported")

    def test_nonzero_process_return_causes_failure(self):
        self.assertFalse(health_runner.summarize_output(GOOD_LOG, 1)[0])

    def test_course_error_is_reported(self):
        log = GOOD_LOG.splitlines()[1] + "\nERROR IT 341 DL1 (CRN 17593): sample failure\n"
        success, courses = health_runner.summarize_output(log, 1)
        self.assertFalse(success)
        self.assertEqual(courses[1]["status"], "failed")

    def test_report_writes_public_status_to_json_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "run_health.json"
            summary_path = Path(tmp) / "summary.md"
            report = {
                "checked_at_utc": "2026-11-09T01:00:00+00:00",
                "duration_seconds": 1.45,
                "success": True,
                "return_code": 0,
                "courses": [{"crn": "17582", "course": "IT 304 DL1", "status": "success", "detail": "1 unreserved seat"}],
            }
            health_runner.write_report(report, report_path, str(summary_path))
            self.assertEqual(json.loads(report_path.read_text()), report)
            self.assertIn("## GMU seat-check health", summary_path.read_text())

    def test_successful_runner_calls_monitor_only_once(self):
        proc = Mock(returncode=0, stdout=GOOD_LOG, stderr="")
        runner = Mock(return_value=proc)
        with tempfile.TemporaryDirectory() as tmp:
            result = health_runner.run_once(
                runner=runner,
                report_path=Path(tmp) / "run_health.json",
            )
            runner.assert_called_once_with(
                [sys.executable, "-u", str(health_runner.HERE / "monitor.py"), "--once"],
                cwd=str(health_runner.HERE), capture_output=True, text=True,
                timeout=health_runner.CHECK_TIMEOUT_SECONDS, check=False,
            )
            self.assertEqual(result, 0)
            self.assertTrue(json.loads((Path(tmp) / "run_health.json").read_text())["success"])

    def test_timeout_is_failure_without_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = health_runner.run_once(
                runner=Mock(side_effect=health_runner.subprocess.TimeoutExpired("monitor", 240)),
                report_path=Path(tmp) / "run_health.json",
            )
            self.assertEqual(result, 1)
            self.assertFalse(json.loads((Path(tmp) / "run_health.json").read_text())["success"])

    def test_timeout_preserves_partial_output_and_completed_lookup(self):
        first = GOOD_LOG.splitlines()[1] + "\n"
        for output in (first, first.encode() + b"\xff"):
            with self.subTest(output_type=type(output)), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "health.json"
                runner = Mock(side_effect=health_runner.subprocess.TimeoutExpired(
                    "monitor", 240, output=output, stderr=b"partial stderr\xff"))
                logs = io.StringIO()
                with contextlib.redirect_stdout(logs):
                    result = health_runner.run_once(runner=runner, report_path=path)
                report = json.loads(path.read_text())
                self.assertEqual(result, 1)
                runner.assert_called_once()
                self.assertFalse(report["success"])
                self.assertIn("timed out", report["failure_reason"])
                self.assertEqual([r["status"] for r in report["courses"]],
                                 ["success", "not_reported"])
                self.assertIn(first.strip(), logs.getvalue())
                self.assertIn("partial stderr\ufffd", logs.getvalue())

    def test_timeout_after_both_lookups_still_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "health.json"
            result = health_runner.run_once(
                runner=Mock(side_effect=health_runner.subprocess.TimeoutExpired(
                    "monitor", 240, output=GOOD_LOG)), report_path=path)
            self.assertEqual(result, 1)
            report = json.loads(path.read_text())
            self.assertTrue(all(r["status"] == "success" for r in report["courses"]))
            self.assertFalse(report["success"])

    def test_startup_failure_is_reported_without_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "health.json"
            runner = Mock(side_effect=OSError("synthetic error"))
            result = health_runner.run_once(runner=runner, report_path=path)
            runner.assert_called_once()
            self.assertEqual(result, 1)
            report = json.loads(path.read_text())
            self.assertIn("Could not start", report["failure_reason"])
            self.assertTrue(all(r["status"] == "not_reported" for r in report["courses"]))

    def test_optional_write_failures_do_not_change_monitor_result(self):
        for failed_target in ("json", "summary"):
            for code in (0, 1):
                with self.subTest(target=failed_target, code=code), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    report_path = root / ("missing/report.json" if failed_target == "json" else "report.json")
                    summary = root / ("missing/summary.md" if failed_target == "summary" else "summary.md")
                    logs = io.StringIO()
                    with contextlib.redirect_stderr(logs):
                        result = health_runner.run_once(
                            runner=Mock(return_value=Mock(returncode=code, stdout=GOOD_LOG, stderr="")),
                            report_path=report_path, summary_path=str(summary))
                    self.assertEqual(result, code)
                    self.assertIn("WARNING", logs.getvalue())
                    self.assertIn("JSON" if failed_target == "json" else "Actions", logs.getvalue())
                    self.assertTrue(summary.exists() if failed_target == "json" else report_path.exists())

    def test_success_never_claims_bark_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, summary = Path(tmp) / "health.json", Path(tmp) / "summary.md"
            result = health_runner.run_once(runner=Mock(return_value=Mock(
                returncode=0, stdout=GOOD_LOG + "Bark did not confirm delivery.\n", stderr="")),
                report_path=path, summary_path=str(summary))
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(path.read_text())["bark_delivery"], "not_verified")
            self.assertIn("Not verified", summary.read_text())

    def test_partial_monitor_state_survives_check_and_reporting_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_path = root / "seat_state.json"
            row = {"seatsAvailable": 0, "reservedSeatSummary": "0 of 10 unreserved"}
            def offline_runner(*args, **kwargs):
                captured = io.StringIO()
                with (patch.object(monitor, "STATE_PATH", state_path),
                      patch.object(monitor, "get_section", side_effect=[row, monitor.SearchError("offline failure")]),
                      patch.object(monitor, "notify") as notify,
                      patch.object(monitor.time, "sleep"), contextlib.redirect_stdout(captured)):
                    failures = monitor.check_once("202710", {})
                notify.assert_not_called()
                return Mock(returncode=1 if failures else 0, stdout=captured.getvalue(), stderr="")
            result = health_runner.run_once(runner=offline_runner,
                report_path=root / "missing/health.json", summary_path=str(root / "missing/summary.md"))
            self.assertEqual(result, 1)
            self.assertTrue(health_runner.state_is_valid(state_path))
            self.assertEqual(json.loads(state_path.read_text())["17582"]["seatsAvailable"], 0)

    def test_cache_validation_rejects_invalid_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            self.assertFalse(health_runner.state_is_valid(path))
            for contents in ("not json", "[]", "{}", '{"17582": null}',
                             '{"17582": {"seatsAvailable": true}}',
                             '{"17582": {"seatsAvailable": -1}}'):
                path.write_text(contents)
                self.assertFalse(health_runner.state_is_valid(path))
            path.write_bytes(b"\xff")
            self.assertFalse(health_runner.state_is_valid(path))

    def test_existing_state_survives_timeout_and_startup_failure(self):
        for error in (OSError("offline startup failure"),
                      health_runner.subprocess.TimeoutExpired("monitor", 240)):
            with self.subTest(error=type(error)), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                state_path = root / "seat_state.json"
                saved = '{"17582": {"seatsAvailable": 1}}'
                state_path.write_text(saved)
                result = health_runner.run_once(runner=Mock(side_effect=error),
                    report_path=root / "missing/health.json")
                self.assertEqual(result, 1)
                self.assertEqual(state_path.read_text(), saved)
                self.assertTrue(health_runner.state_is_valid(state_path))


if __name__ == "__main__":
    unittest.main()

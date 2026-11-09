"""Offline tests of the cloud health wrapper; no university or Bark requests."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

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
        with tempfile.TemporaryDirectory() as tmp:
            result = health_runner.run_once(
                runner=Mock(return_value=proc),
                report_path=Path(tmp) / "run_health.json",
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


if __name__ == "__main__":
    unittest.main()

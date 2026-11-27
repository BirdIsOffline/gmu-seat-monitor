"""Offline endpoint-privacy checks; all destinations and credentials are synthetic."""

import contextlib
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests

from cloud_history import CloudHistoryClient, persist_best_effort, prepare_check
import history_submit
from seat_history import CourseObservation

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "https://history.example.invalid/v1/checks"
TOKEN = "synthetic_endpoint_privacy_token_" + "0" * 16


class WorkflowEndpointPrivacyTests(unittest.TestCase):
    def test_endpoint_is_a_step_scoped_secret_without_variable_fallback(self):
        source = (ROOT / ".github/workflows/seat-monitor.yml").read_text(encoding="utf-8")
        self.assertEqual(source.count("GMU_HISTORY_PRODUCTION_ENDPOINT:"), 1)
        self.assertIn("          GMU_HISTORY_PRODUCTION_ENDPOINT: ${{ secrets.GMU_HISTORY_PRODUCTION_ENDPOINT }}", source)
        self.assertNotIn("vars.GMU_HISTORY_PRODUCTION_ENDPOINT", source)
        submission = source.split("      - name: Submit original historical observations", 1)[1]
        command = submission.split("        run:", 1)[1]
        self.assertIn("python history_submit.py", command)
        self.assertNotIn("GMU_HISTORY_PRODUCTION_ENDPOINT", command)
        self.assertNotIn("GMU_HISTORY_PRODUCTION_TOKEN", command)
        self.assertNotIn("add-mask", command)
        self.assertIn("GMU_HISTORY_PRODUCTION_TOKEN: ${{ secrets.GMU_HISTORY_PRODUCTION_TOKEN }}", submission)
        self.assertIn("vars.GMU_HISTORY_CAPTURE_ENABLED == 'true'", submission)
        self.assertIn("vars.GMU_HISTORY_ENABLED == 'true'", submission)


class EndpointFailurePrivacyTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.client = CloudHistoryClient(ENDPOINT, TOKEN, session=self.session,
                                         max_attempts=1, sleeper=Mock())
        self.addCleanup(self.client.close)
        self.prepared = prepare_check("00000000-0000-4000-8000-000000000001",
            datetime(2026, 11, 9, tzinfo=timezone.utc), .25,
            [CourseObservation("202710", "IT", "341", "DL1", "17593", 0)])

    def assert_redacted(self, *values):
        for value in values:
            text = value.decode("utf-8") if isinstance(value, bytes) else str(value)
            self.assertNotIn(ENDPOINT, text)
            self.assertNotIn(TOKEN, text)

    def test_request_exceptions_never_disclose_destination_or_token(self):
        for error, code in ((requests.Timeout, "TIMEOUT"),
                            (requests.ConnectionError, "NETWORK_FAILURE"),
                            (requests.RequestException, "NETWORK_FAILURE")):
            self.session.post.reset_mock()
            self.session.post.side_effect = error(ENDPOINT + " " + TOKEN)
            output, errors = io.StringIO(), io.StringIO()
            with (self.subTest(error=error.__name__), self.assertLogs("cloud_history") as logs,
                  contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors)):
                result = self.client.submit(self.prepared)
            self.assertEqual((result.success, result.code, result.attempts), (False, code, 1))
            self.assertEqual(self.session.post.call_count, 1)
            self.assert_redacted(logs.output, output.getvalue(), errors.getvalue(), result, repr(self.client))

    def test_error_and_invalid_acknowledgement_bodies_are_never_logged(self):
        for status, code in ((200, "INVALID_ACKNOWLEDGEMENT"), (403, "HTTP_FAILURE"),
                             (429, "RATE_LIMITED"), (503, "RETRYABLE_HTTP_FAILURE")):
            reply = Mock(status_code=status)
            reply.__enter__ = Mock(return_value=reply)
            reply.__exit__ = Mock(return_value=False)
            reply.iter_content.return_value = [json.dumps({"endpoint": ENDPOINT, "token": TOKEN}).encode()]
            self.session.post.reset_mock()
            self.session.post.return_value = reply
            output, errors = io.StringIO(), io.StringIO()
            with (self.subTest(status=status), self.assertLogs("cloud_history") as logs,
                  contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors)):
                result = self.client.submit(self.prepared)
            self.assertEqual((result.success, result.code, result.attempts), (False, code, 1))
            self.assertEqual(self.session.post.call_count, 1)
            self.assert_redacted(logs.output, output.getvalue(), errors.getvalue(), result)

    def test_generic_submission_boundary_drops_raw_exception_details(self):
        client = Mock()
        client.submit.side_effect = RuntimeError(ENDPOINT + " " + TOKEN)
        with self.assertLogs("cloud_history") as logs:
            result = persist_best_effort(client, self.prepared)
        self.assertEqual(result.code, "CLIENT_ERROR")
        self.assert_redacted(logs.output, result)

    def test_invalid_results_cannot_inject_endpoints_into_stdout_or_summaries(self):
        results = [dict(history_submit.outcome("failed", "TIMEOUT"), endpoint=ENDPOINT),
                   history_submit.outcome("failed", ENDPOINT + " " + TOKEN),
                   {"outcome": ENDPOINT, "code": "TIMEOUT", "checks": 0, "ingested": 0}]
        with tempfile.TemporaryDirectory() as tmp:
            for index, result in enumerate(results):
                summary = Path(tmp) / (str(index) + ".md")
                output, errors = io.StringIO(), io.StringIO()
                with (self.subTest(index=index), contextlib.redirect_stdout(output),
                      contextlib.redirect_stderr(errors)):
                    history_submit.report(result, summary_path=summary)
                self.assertIn("WORKER_FAILED", output.getvalue())
                self.assert_redacted(output.getvalue(), errors.getvalue(), summary.read_text(encoding="utf-8"))

    def test_report_file_payload_contains_only_the_fixed_outcome_schema(self):
        writer = Mock()
        module = SimpleNamespace(atomic_write=writer)
        invalid = dict(history_submit.outcome("success", "INGESTED", 1, 1), endpoint=ENDPOINT, token=TOKEN)
        output = io.StringIO()
        with (patch.dict(sys.modules, {"history_capture": module}), contextlib.redirect_stdout(output)):
            history_submit.report(invalid, Path("synthetic-report.json"))
        writer.assert_called_once()
        payload = writer.call_args.args[1]
        self.assertEqual(json.loads(payload), history_submit.outcome("failed", "WORKER_FAILED"))
        self.assert_redacted(payload, output.getvalue())

    def test_report_write_errors_do_not_print_exception_details(self):
        output, errors = io.StringIO(), io.StringIO()
        with (patch("builtins.open", side_effect=OSError(ENDPOINT + " " + TOKEN)),
              contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors)):
            history_submit.report(history_submit.outcome("unconfirmed", "TIMEOUT"),
                                  summary_path=Path("synthetic-summary.md"))
        self.assertIn("HISTORY REPORT: WRITE_FAILED", output.getvalue())
        self.assert_redacted(output.getvalue(), errors.getvalue())

    def test_parent_rejects_untrusted_child_output_without_echoing_it(self):
        payloads = [json.dumps(dict(history_submit.outcome("success", "INGESTED", 1, 1),
                                   endpoint=ENDPOINT, token=TOKEN)).encode(),
                    json.dumps(history_submit.outcome("failed", ENDPOINT + " " + TOKEN)).encode(),
                    ("Traceback: " + ENDPOINT + " " + TOKEN).encode()]
        env = {"GMU_HISTORY_ENABLED": "true", "GMU_HISTORY_PRODUCTION_ENDPOINT": ENDPOINT,
               "GMU_HISTORY_PRODUCTION_TOKEN": TOKEN}
        for payload in payloads:
            child = Mock(returncode=0)
            child.poll.return_value = 0
            child.communicate.return_value = (payload, None)
            output, errors = io.StringIO(), io.StringIO()
            with (self.subTest(payload=payload), patch.dict(os.environ, env, clear=True),
                  patch.object(history_submit.subprocess, "Popen", return_value=child) as start,
                  patch.object(history_submit.signal, "signal"), contextlib.redirect_stdout(output),
                  contextlib.redirect_stderr(errors)):
                result = history_submit.submit_with_deadline("synthetic-handoff", 2)
                history_submit.report(result)
            self.assertEqual(result, history_submit.outcome("unconfirmed", "WORKER_FAILED"))
            self.assertEqual(start.call_args.kwargs["stderr"], subprocess.DEVNULL)
            self.assert_redacted(start.call_args.args, output.getvalue(), errors.getvalue(), result)

    def test_startup_exceptions_return_a_fixed_code_without_destination_details(self):
        output, errors = io.StringIO(), io.StringIO()
        with (patch.dict(os.environ, {"GMU_HISTORY_ENABLED": "true"}, clear=True),
              patch.object(history_submit.subprocess, "Popen", side_effect=OSError(ENDPOINT + " " + TOKEN)),
              patch.object(history_submit.signal, "signal"), contextlib.redirect_stdout(output),
              contextlib.redirect_stderr(errors)):
            result = history_submit.submit_with_deadline("synthetic-handoff", 2)
            history_submit.report(result)
        self.assertEqual(result, history_submit.outcome("failed", "WORKER_FAILED"))
        self.assert_redacted(output.getvalue(), errors.getvalue(), result)


if __name__ == "__main__":
    unittest.main()

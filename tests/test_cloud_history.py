"""Offline cloud client tests. All HTTP and notification calls are mocked."""

import contextlib
from dataclasses import replace
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import uuid

import requests

from cloud_history import (
    CloudHistoryClient, PreparedCheck, capture_completed_best_effort, persist_best_effort, prepare_check,
)
import monitor
from seat_history import CourseObservation

TOKEN = "synthetic_local_only_token_00000000000000000000000"
ENDPOINT = "https://history.invalid/v1/checks"


class CloudClientTests(unittest.TestCase):
    def setUp(self):
        self.course = CourseObservation("202710", "IT", "304", "DL1", "17582", 1, "1 of 10 unreserved")
        self.check_id = str(uuid.uuid4())
        self.start = datetime(2026, 11, 9, tzinfo=timezone.utc)
        self.prepared = self.prepare()
        self.session = Mock()
        self.sleep = Mock()
        self.client = CloudHistoryClient(ENDPOINT, TOKEN, session=self.session, sleeper=self.sleep)

    def prepare(self, **changes):
        return prepare_check(self.check_id, self.start, 0.25, [replace(self.course, **changes)])

    def reply(self, status=200, body=None):
        reply = Mock(status_code=status)
        reply.__enter__ = Mock(return_value=reply)
        reply.__exit__ = Mock(return_value=False)
        reply.iter_content.return_value = [json.dumps(body if body is not None else {
            "code": "INGESTED", "check_id": self.check_id}).encode()]
        return reply

    def test_success_requires_matching_commit_acknowledgement(self):
        self.session.post.return_value = self.reply()
        result = self.client.submit(self.prepared)
        self.assertEqual((result.success, result.code, result.attempts), (True, "INGESTED", 1))
        call = self.session.post.call_args
        self.assertEqual(call.args, (ENDPOINT,))
        self.assertEqual(call.kwargs["timeout"], (3.05, 10))
        self.assertFalse(call.kwargs["allow_redirects"])
        self.assertTrue(call.kwargs["stream"])
        self.assertEqual(call.kwargs["data"], self.prepared.body)

    def test_timeout_then_success_reuses_exact_completed_payload(self):
        self.session.post.side_effect = [requests.Timeout(TOKEN), self.reply()]
        with self.assertLogs("cloud_history", "WARNING") as logs:
            result = self.client.submit(self.prepared)
        self.assertTrue(result.success)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(self.session.post.call_args_list[0], self.session.post.call_args_list[1])
        self.assertNotIn(TOKEN, "".join(logs.output))
        self.sleep.assert_called_once_with(0.25)

    def test_repeated_timeouts_are_bounded_and_report_failure(self):
        self.session.post.side_effect = requests.Timeout("synthetic")
        with self.assertLogs("cloud_history"):
            result = self.client.submit(self.prepared)
        self.assertEqual((result.success, result.code, result.attempts), (False, "TIMEOUT", 3))
        self.assertEqual(self.session.post.call_count, 3)
        self.assertEqual(self.sleep.call_count, 2)

    def test_database_unavailable_then_retry_commits(self):
        self.session.post.side_effect = [self.reply(503), self.reply()]
        with self.assertLogs("cloud_history"):
            self.assertTrue(self.client.submit(self.prepared).success)

    def test_temporary_network_failure_and_rate_limit(self):
        self.session.post.side_effect = [requests.ConnectionError(TOKEN), self.reply(429), self.reply()]
        with self.assertLogs("cloud_history") as logs:
            self.assertTrue(self.client.submit(self.prepared).success)
        self.assertNotIn(TOKEN, "".join(logs.output))

    def test_permanent_failures_and_redirects_are_not_retried(self):
        for status in (301, 302, 307, 400, 401, 403, 404, 409, 413, 415):
            self.session.post.reset_mock()
            self.session.post.return_value = self.reply(status)
            with self.subTest(status=status), self.assertLogs("cloud_history"):
                result = self.client.submit(self.prepared)
                self.assertFalse(result.success)
                self.assertEqual(result.attempts, 1)
                self.assertEqual(self.session.post.call_count, 1)

    def test_invalid_acknowledgement_cannot_be_success(self):
        for body in ({"code": "INGESTED", "check_id": "wrong"}, {"code": "OK"}):
            self.session.post.return_value = self.reply(body=body)
            with self.assertLogs("cloud_history"):
                result = self.client.submit(self.prepared)
            self.assertEqual(result.code, "INVALID_ACKNOWLEDGEMENT")
            self.assertFalse(result.success)

    def test_oversized_ack_is_bounded(self):
        reply = self.reply()
        reply.iter_content.return_value = [b"x" * 4097]
        self.session.post.return_value = reply
        with self.assertLogs("cloud_history"):
            self.assertFalse(self.client.submit(self.prepared).success)

    def test_prepared_payload_omits_free_text_and_only_permits_public_facts(self):
        prepared = self.prepare(reserved_summary="PRIVATE_TEXT not parseable")
        self.assertNotIn(b"PRIVATE_TEXT", prepared.body)
        self.assertEqual(json.loads(prepared.body)["observations"][0]["reserved_summary"], "unknown")
        self.assertEqual(json.loads(self.prepared.body)["observations"][0]["reserved_summary"],
                         {"unreservedAvailable": 1})
        for changes in ({"reserved_summary": {"studentId": "private"}}, {"total_available": True},
                        {"reserved_summary": {"unreservedAvailable": 0, "unreservedRemaining": 1}},
                        {"reserved_summary": "1000001 of 1000002 unreserved"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.prepare(**changes)
        self.assertNotIn(TOKEN, repr(self.client))
        self.assertNotIn("17582", repr(self.prepared))

    def test_preparation_validation_and_stable_canonical_bytes(self):
        self.assertEqual(self.prepare().body, self.prepared.body)
        for args in (("student-id", self.start, 1, [self.course]),
                     (self.check_id, datetime(2026, 1, 1), 1, [self.course]),
                     (self.check_id, self.start, float("nan"), [self.course]),
                     (self.check_id, self.start, True, [self.course]),
                     (self.check_id, self.start, 86401, [self.course]),
                     (self.check_id, self.start, 1, []),
                     (self.check_id, self.start, 1, [self.course, self.course]),
                     (self.check_id, self.start, 1, [self.course] * 51),
                     (self.check_id, self.start, 1, [{"studentId": "private"}])):
            with self.subTest(args=args), self.assertRaises(ValueError):
                prepare_check(*args)
        for kwargs in ({"status": "bad"}, {"error_code": "TIMEOUT"},
                       {"status": "failed", "error_code": "PRIVATE"}):
            with self.assertRaises(ValueError):
                prepare_check(self.check_id, self.start, 1, [self.course], **kwargs)
        failed = prepare_check(self.check_id, self.start, 20, status="failed", error_code="TIMEOUT")
        self.assertEqual(json.loads(failed.body)["observations"], [])

    def test_forged_or_oversized_prepared_snapshot_cannot_send_private_data(self):
        for changes in ({"studentId": "private"}, {"observations": [{"studentName": "private"}]}):
            body = json.loads(self.prepared.body)
            body.update(changes)
            with self.assertRaises(ValueError):
                self.client.submit(PreparedCheck(self.check_id, json.dumps(body).encode()))
        for prepared in (PreparedCheck(self.check_id, b"x" * 65537),
                         PreparedCheck(self.check_id, b"null"),
                         PreparedCheck("wrong", self.prepared.body)):
            with self.assertRaises(ValueError):
                self.client.submit(prepared)
        self.session.post.assert_not_called()

    def test_prepared_bytes_survive_mutation_of_source_counts(self):
        summary = {"unreservedAvailable": 1}
        prepared = self.prepare(reserved_summary=summary)
        summary["unreservedAvailable"] = 99
        self.session.post.return_value = self.reply()
        self.assertTrue(self.client.submit(prepared).success)
        self.assertEqual(json.loads(self.session.post.call_args.kwargs["data"])["observations"][0]["reserved_summary"],
                         {"unreservedAvailable": 1})

    def test_configuration_rejects_unsafe_endpoint_and_invalid_secrets(self):
        for endpoint in ("http://example.com/v1/checks", "https://user:pass@example.com/v1/checks",
                         "https://example.com/sql", ENDPOINT + "?token=secret", ENDPOINT + "#secret"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                CloudHistoryClient(endpoint, TOKEN)
        for token in (None, "", "short", TOKEN + "\n"):
            with self.assertRaises(ValueError):
                CloudHistoryClient(ENDPOINT, token)
        for kwargs in ({"timeout": (0, 10)}, {"timeout": (3, float("inf"))}, {"max_attempts": 0}):
            with self.assertRaises(ValueError):
                CloudHistoryClient(ENDPOINT, TOKEN, **kwargs)
        local = CloudHistoryClient("http://127.0.0.1:8787/v1/checks", TOKEN, allow_local_http=True)
        self.assertFalse(local._session.trust_env)
        local.close()

    def test_opt_in_configuration_is_explicit_and_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(CloudHistoryClient.from_env())
        with patch.dict(os.environ, {"GMU_HISTORY_ENABLED": "true", "GMU_HISTORY_ENDPOINT": ENDPOINT,
                                    "GMU_HISTORY_TOKEN": TOKEN}, clear=True):
            client = CloudHistoryClient.from_env()
            client.close()

    def test_best_effort_catches_unexpected_failures_without_secret_logs(self):
        self.session.post.side_effect = RuntimeError(TOKEN)
        with self.assertLogs("cloud_history") as logs:
            result = persist_best_effort(self.client, self.prepared)
        self.assertEqual((result.success, result.code), (False, "CLIENT_ERROR"))
        self.assertNotIn(TOKEN, "".join(logs.output))
        self.assertEqual(persist_best_effort(None, self.prepared).code, "DISABLED")

    def test_history_outage_and_retries_do_not_change_checks_or_bark_decisions(self):
        state = {"17582": {"seatsAvailable": 0, "reservedSeatSummary": "0 of 10 unreserved"}}
        current = {"seatsAvailable": 1, "reservedSeatSummary": "1 of 10 unreserved"}
        self.session.post.side_effect = requests.Timeout("synthetic database outage")
        with (patch.object(monitor, "COURSES", {"17582": {"name": "IT 304 DL1", "number": "304"}}),
              patch.object(monitor, "get_section", return_value=current) as fetch,
              patch.object(monitor, "notify", return_value=True) as notify,
              patch.object(monitor, "save_state"), patch.object(monitor.time, "sleep"),
              patch("requests.sessions.Session.request", side_effect=AssertionError("external network forbidden")),
              contextlib.redirect_stdout(io.StringIO())):
            self.assertEqual(monitor.check_once("202710", state), 0)
            with self.assertLogs("cloud_history"):
                result = persist_best_effort(self.client, self.prepared)
            self.assertFalse(result.success)
            # Another ordinary check still works after historical persistence fails.
            self.assertEqual(monitor.check_once("202710", state), 0)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(notify.call_count, 1)  # Only the original 0 -> 1 alert.
        self.assertEqual(self.session.post.call_count, 3)

    def test_monitoring_entry_points_do_not_import_http_history_client(self):
        root = Path(__file__).resolve().parents[1]
        for path in ("monitor.py", "health_runner.py"):
            self.assertNotIn("cloud_history", (root / path).read_text())

    def test_completed_capture_reports_only_matching_durable_acknowledgement(self):
        self.session.post.return_value = self.reply()
        result = capture_completed_best_effort(
            self.client, self.check_id, self.start, .25, [self.course])
        self.assertEqual((result.success, result.code, result.attempts), (True, "INGESTED", 1))
        self.assertEqual(self.session.post.call_args.kwargs["data"], self.prepared.body)

    def test_completed_capture_preparation_failure_is_isolated_and_redacted(self):
        with self.assertLogs("cloud_history") as logs:
            result = capture_completed_best_effort(
                self.client, self.check_id, self.start, .25,
                [replace(self.course, reserved_summary={"studentName": TOKEN})])
        self.assertEqual((result.success, result.code, result.attempts), (False, "CAPTURE_PREPARATION_FAILED", 0))
        self.assertNotIn(TOKEN, "".join(logs.output))
        self.session.post.assert_not_called()

    def test_completed_capture_exhausted_retries_report_unconfirmed_and_keep_identity(self):
        # A timeout is deliberately not reported as proof that D1 has no record.
        self.session.post.side_effect = requests.Timeout(TOKEN)
        with self.assertLogs("cloud_history") as logs:
            result = capture_completed_best_effort(
                self.client, self.check_id, self.start, .25, [self.course])
        self.assertEqual((result.success, result.code, result.attempts), (False, "TIMEOUT", 3))
        self.assertIn("Historical capture unconfirmed", "".join(logs.output))
        self.assertNotIn(TOKEN, "".join(logs.output))
        self.assertTrue(all(c.kwargs["data"] == self.prepared.body for c in self.session.post.call_args_list))
        # Later retry uses original completed facts, without refetching anything.
        self.session.post.side_effect = None
        self.session.post.return_value = self.reply()
        self.assertTrue(capture_completed_best_effort(
            self.client, self.check_id, self.start, .25, [self.course]).success)
        self.assertEqual(self.session.post.call_args.kwargs["data"], self.prepared.body)

    def test_completed_capture_disabled_does_no_preparation_or_network(self):
        with patch("cloud_history.prepare_check", side_effect=AssertionError("must not prepare")):
            self.assertEqual(capture_completed_best_effort(None).code, "DISABLED")
        self.session.post.assert_not_called()

    def test_completed_capture_errors_preserve_monitoring_and_notification_results(self):
        state = {"17582": {"seatsAvailable": 0, "reservedSeatSummary": "0 of 10 unreserved"}}
        current = {"seatsAvailable": 1, "reservedSeatSummary": "1 of 10 unreserved"}
        self.session.post.side_effect = [self.reply(503), self.reply(503), self.reply(503)]
        with (patch.object(monitor, "COURSES", {"17582": {"name": "IT 304 DL1", "number": "304"}}),
              patch.object(monitor, "get_section", return_value=current) as fetch,
              patch.object(monitor, "notify", return_value=True) as notify,
              patch.object(monitor, "save_state"), patch.object(monitor.time, "sleep"),
              patch("requests.sessions.Session.request", side_effect=AssertionError("external network forbidden")),
              contextlib.redirect_stdout(io.StringIO())):
            monitoring_result = monitor.check_once("202710", state)
            with self.assertLogs("cloud_history"):
                history = capture_completed_best_effort(self.client, self.check_id, self.start, .25, [self.course])
            self.assertEqual(monitoring_result, 0)
            self.assertFalse(history.success)
            self.assertEqual(monitor.check_once("202710", state), monitoring_result)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(notify.call_count, 1)
        self.assertEqual(self.session.post.call_count, 3)


if __name__ == "__main__":
    unittest.main()

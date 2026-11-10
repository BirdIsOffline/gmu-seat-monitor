"""Offline outbox tests: temporary disk, synthetic facts, no external requests."""

import contextlib
from datetime import datetime, timedelta, timezone
import io
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

import requests

from cloud_history import CloudHistoryClient, IngestionResult, PreparedCheck, prepare_check
from history_outbox import HistoryOutbox, OutboxError, enqueue_completed_best_effort, main
import monitor
from seat_history import CourseObservation


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "outbox.sqlite3"
        self.box = HistoryOutbox(self.path)
        self.box.initialize()
        self.start = datetime(2026, 11, 9, tzinfo=timezone.utc)
        self.now = 1000.0
        self.client = Mock()
        self.client.submit.return_value = IngestionResult(True, "INGESTED", 1)
        self.network = patch("requests.sessions.Session.request", side_effect=AssertionError("External network forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def prepared(self, minute=0, crns=("17582",), total=0, **kwargs):
        return prepare_check(str(uuid.uuid4()), self.start + timedelta(minutes=minute), .25,
                             [CourseObservation("202710", "IT", "304", "DL1", c, total) for c in crns], **kwargs)

    def drain(self, **kwargs):
        return self.box.drain(self.client, clock=lambda: self.now, **kwargs)

    def test_success_is_durable_and_exact_duplicate_enqueue_never_resends(self):
        p = self.prepared()
        self.assertTrue(self.box.enqueue(p))
        self.box.initialize()  # Reinitialization must preserve queued data.
        self.assertFalse(self.box.enqueue(p))
        self.assertEqual(self.drain(), {"attempted": 1, "delivered": 1})
        self.assertEqual(self.box.records(), [])
        self.assertFalse(HistoryOutbox(self.path).enqueue(p))
        self.assertEqual(self.drain()["attempted"], 0)
        self.client.submit.assert_called_once_with(p)
        self.assertEqual(self.box.records(include_delivered=True)[0]["last_code"], "INGESTED")

    def test_failed_submission_blocks_later_course_until_restart_recovery(self):
        first, later = self.prepared(), self.prepared(5, total=1)
        for p in (later, first):  # Arrival order is not observation order.
            self.box.enqueue(p)
        self.client.submit.return_value = IngestionResult(False, "TIMEOUT", 1)
        with self.assertLogs("history_outbox"):
            self.assertEqual(self.drain(), {"attempted": 1, "delivered": 0})
        self.assertEqual(self.drain()["attempted"], 0)  # Persisted delay, no overtaking.
        self.box = HistoryOutbox(self.path)
        self.now += 60
        self.client.submit.return_value = IngestionResult(True, "INGESTED", 1)
        self.assertEqual(self.drain(), {"attempted": 2, "delivered": 2})
        self.assertEqual([c.args[0] for c in self.client.submit.call_args_list], [first, first, later])

    def test_pending_records_sort_chronologically_per_course_and_limit_resumes(self):
        samples = [self.prepared(i) for i in range(4)]
        for p in reversed(samples):
            self.box.enqueue(p)
        self.assertEqual(self.drain(limit=2)["delivered"], 2)
        self.assertEqual(self.drain(limit=2)["delivered"], 2)
        self.assertEqual([c.args[0] for c in self.client.submit.call_args_list], samples)

    def test_failed_batch_blocks_all_member_courses_but_not_unrelated_course(self):
        head = self.prepared(crns=("17582", "17593"))
        later = self.prepared(5, crns=("17593",))
        unrelated = self.prepared(10, crns=("99999",))
        for p in (head, later, unrelated):
            self.box.enqueue(p)
        self.client.submit.side_effect = [IngestionResult(False, "CONFLICT", 1), IngestionResult(True, "INGESTED", 1)]
        with self.assertLogs("history_outbox"):
            self.assertEqual(self.drain(), {"attempted": 2, "delivered": 1})
        self.assertEqual([c.args[0] for c in self.client.submit.call_args_list], [head, unrelated])
        self.now += 100000
        self.assertEqual(self.drain()["attempted"], 0)  # Permanent conflict needs diagnosis.
        self.box.retry(head.check_id)
        self.client.submit.side_effect = None
        self.assertEqual(self.drain()["delivered"], 2)

    def test_waiting_multicourse_batch_propagates_ordering_dependency(self):
        samples = [self.prepared(), self.prepared(5, crns=("17582", "17593")),
                   self.prepared(10, crns=("17593",))]
        for p in samples:
            self.box.enqueue(p)
        self.client.submit.return_value = IngestionResult(False, "RATE_LIMITED", 1)
        with self.assertLogs("history_outbox"):
            self.assertEqual(self.drain()["attempted"], 1)
        self.assertEqual(self.drain()["attempted"], 0)

    def test_late_or_same_time_import_after_uncertain_attempt_is_rejected(self):
        head = self.prepared(10)
        self.box.enqueue(head)
        self.client.submit.return_value = IngestionResult(False, "TIMEOUT", 1)
        with self.assertLogs("history_outbox"):
            self.drain()
        self.assertFalse(self.box.enqueue(head))
        for minute in (5, 10):
            with self.assertRaisesRegex(OutboxError, "OUTBOX_LATE_RECORD"):
                self.box.enqueue(self.prepared(minute))
        self.assertTrue(self.box.enqueue(self.prepared(5, crns=("17593",))))

    def test_delivered_frontier_and_uuid_contents_survive_restart(self):
        p = self.prepared(10)
        self.box.enqueue(p)
        self.drain()
        self.box = HistoryOutbox(self.path)
        with self.assertRaisesRegex(OutboxError, "OUTBOX_LATE_RECORD"):
            self.box.enqueue(self.prepared())
        with self.assertRaisesRegex(OutboxError, "OUTBOX_IDENTITY_CONFLICT"):
            self.box.enqueue(prepare_check(p.check_id, self.start + timedelta(minutes=10), 3,
                                         [CourseObservation("202710", "IT", "304", "DL1", "17582", 0)]))
        self.assertEqual(len(self.box.records(include_delivered=True)), 1)

    def test_equal_course_timestamps_are_ambiguous_and_batch_enqueue_is_atomic(self):
        self.box.enqueue(self.prepared(crns=("17593",)))
        with self.assertRaisesRegex(OutboxError, "OUTBOX_AMBIGUOUS_TIME"):
            self.box.enqueue(self.prepared(crns=("17582", "17593")))
        self.assertEqual(len(self.box.records()), 1)
        self.assertTrue(self.box.enqueue(self.prepared(crns=("17582",))))

    def test_real_client_outage_is_retained_and_backoff_persists(self):
        self.box.enqueue(self.prepared())
        session = Mock()
        reply = Mock(status_code=522)  # Cloudflare edge timeout, outside Worker.
        reply.__enter__ = Mock(return_value=reply)
        reply.__exit__ = Mock(return_value=False)
        session.post.return_value = reply
        client = CloudHistoryClient("https://history.invalid/v1/checks", "s" * 43,
                                    session=session, max_attempts=1)
        with self.assertLogs():
            self.assertEqual(self.box.drain(client, clock=lambda: self.now)["delivered"], 0)
        self.assertEqual(self.box.records()[0]["last_code"], "RETRYABLE_HTTP_FAILURE")
        self.assertEqual(self.box.records()[0]["next_attempt"], 1060)
        self.now = 1060
        session.post.side_effect = requests.ConnectionError("private error text")
        with self.assertLogs() as logs:
            self.box.drain(client, clock=lambda: self.now)
        self.assertNotIn("private error text", "".join(logs.output))
        self.assertEqual(self.box.records()[0]["next_attempt"], 1180)
        self.now = 1180
        self.assertEqual(self.drain()["delivered"], 1)

    def test_permanent_errors_require_manual_retry_and_delay_is_capped(self):
        p = self.prepared()
        self.box.enqueue(p)
        for code in ("UNAUTHORIZED", "INVALID_REQUEST", "TOO_LARGE", "HTTP_FAILURE"):
            self.box.retry(p.check_id)
            self.client.submit.return_value = IngestionResult(False, code, 1)
            with self.assertLogs("history_outbox"):
                self.drain()
            self.assertEqual(self.box.records()[0]["blocked"], 1)
        self.box.retry(p.check_id)
        self.client.submit.return_value = IngestionResult(False, "RATE_LIMITED", 1)
        for i in range(8):
            with self.assertLogs("history_outbox"):
                self.drain()
            record = self.box.records()[0]
            self.assertLessEqual(record["next_attempt"] - self.now, 3600)
            self.now = record["next_attempt"]

    def test_unknown_client_exception_is_sanitized_and_stays_pending(self):
        self.box.enqueue(self.prepared())
        self.client.submit.side_effect = RuntimeError("secret exception detail")
        with self.assertLogs("history_outbox") as logs:
            self.assertEqual(self.drain()["delivered"], 0)
        self.assertNotIn("secret exception detail", "".join(logs.output))
        self.assertEqual(self.box.records()[0]["last_code"], "CLIENT_ERROR")

    def test_process_death_during_submit_preserves_exact_payload_and_releases_lock(self):
        p = self.prepared()
        self.box.enqueue(p)
        code = """
import os, sys
from history_outbox import HistoryOutbox
class Interrupted:
    def submit(self, prepared):
        os._exit(23)
HistoryOutbox(sys.argv[1]).drain(Interrupted(), clock=lambda: 1000)
"""
        child = subprocess.run([sys.executable, "-c", code, str(self.path)], timeout=10, capture_output=True)
        self.assertEqual(child.returncode, 23, child.stderr)
        self.box = HistoryOutbox(self.path)
        self.assertEqual(self.box.records()[0]["last_code"], "ATTEMPT_STARTED")
        self.assertEqual(self.drain()["attempted"], 0)
        self.now += 60
        self.assertEqual(self.drain()["delivered"], 1)
        self.client.submit.assert_called_once_with(p)
        self.assertEqual(self.box.records(include_delivered=True)[0]["attempts"], 2)

    def test_supervisor_timeout_kills_hung_submission_without_losing_pending_record(self):
        p = self.prepared()
        self.box.enqueue(p)
        marker = self.root / "started"
        code = """
import sys, threading
from pathlib import Path
from history_outbox import HistoryOutbox
class HungClient:
    def submit(self, prepared):
        Path(sys.argv[2]).write_text('started')
        threading.Event().wait()
HistoryOutbox(sys.argv[1]).drain(HungClient(), clock=lambda: 1000)
"""
        with self.assertRaises(subprocess.TimeoutExpired):
            subprocess.run([sys.executable, "-c", code, str(self.path), str(marker)],
                           timeout=1, capture_output=True)
        self.assertTrue(marker.exists())
        self.assertEqual(self.box.records()[0]["last_code"], "ATTEMPT_STARTED")
        self.now += 60
        self.assertEqual(self.drain()["delivered"], 1)
        self.client.submit.assert_called_once_with(p)

    def test_single_owner_lock_prevents_concurrent_enqueue_or_drain(self):
        p = self.prepared(5)
        self.box.enqueue(p)
        contender = HistoryOutbox(self.path)
        def submit(prepared):
            for call in (lambda: contender.enqueue(self.prepared()),
                         lambda: contender.drain(Mock())):
                with self.assertRaisesRegex(OutboxError, "OUTBOX_BUSY"):
                    call()
            return IngestionResult(True, "INGESTED", 1)
        self.client.submit.side_effect = submit
        self.assertEqual(self.drain()["delivered"], 1)

    def test_corrupt_record_cannot_be_submitted(self):
        p = self.prepared()
        self.box.enqueue(p)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE submissions SET body=?", (self.prepared().body,))
        with self.assertRaisesRegex(OutboxError, "OUTBOX_CORRUPT_RECORD"):
            self.drain()
        self.client.submit.assert_not_called()
        self.assertEqual(self.box.records()[0]["attempts"], 0)

    def test_missing_store_is_not_silently_recreated_and_invalid_payload_is_rejected(self):
        missing = HistoryOutbox(self.root / "missing.sqlite3")
        with self.assertRaisesRegex(OutboxError, "OUTBOX_STORAGE_ERROR"):
            missing.enqueue(self.prepared())
        self.assertFalse(missing.path.exists())
        with self.assertRaises(ValueError):
            self.box.enqueue(PreparedCheck(str(uuid.uuid4()), b'{"studentId":"private"}'))
        self.assertEqual(self.box.records(), [])

    def test_failed_and_partial_checks_preserve_only_completed_observations(self):
        empty = self.prepared(crns=(), status="failed", error_code="TIMEOUT")
        partial = self.prepared(5, status="failed", error_code="COURSE_LOOKUP_FAILED")
        for p in (empty, partial):
            self.box.enqueue(p)
        self.assertEqual(self.drain()["delivered"], 2)
        self.assertEqual([c.args[0] for c in self.client.submit.call_args_list], [empty, partial])

    def test_capture_and_bark_failures_do_not_change_monitoring_or_history(self):
        current = {"seatsAvailable": 1, "reservedSeatSummary": "1 of 10 unreserved"}
        state = {"17582": {"seatsAvailable": 0}}
        with (patch.object(monitor, "COURSES", {"17582": {"name": "IT 304 DL1", "number": "304"}}),
              patch.object(monitor, "get_section", return_value=current) as lookup,
              patch.object(monitor, "notify", return_value=False) as bark,
              patch.object(monitor, "save_state"), patch.object(monitor.time, "sleep"),
              contextlib.redirect_stdout(io.StringIO())):
            self.assertEqual(monitor.check_once("202710", state), 0)
            bad_box = HistoryOutbox(self.root / "missing" / "outbox.sqlite3")
            with self.assertLogs("history_outbox"):
                failed = enqueue_completed_best_effort(bad_box, str(uuid.uuid4()), self.start, .25,
                    [CourseObservation("202710", "IT", "304", "DL1", "17582", 1)])
            self.assertFalse(failed.success)
            result = enqueue_completed_best_effort(self.box, str(uuid.uuid4()), self.start, .25,
                [CourseObservation("202710", "IT", "304", "DL1", "17582", 1)])
            self.assertTrue(result.success)  # Bark failure does not suppress history.
            self.assertEqual(self.drain()["delivered"], 1)
            self.assertEqual(monitor.check_once("202710", state), 0)
        self.assertEqual(lookup.call_count, 2)
        bark.assert_called_once()
        self.assertEqual(state["17582"], monitor.snapshot(current))

    def test_preparation_failure_and_disabled_capture_are_isolated(self):
        self.assertEqual(enqueue_completed_best_effort(None).code, "DISABLED")
        with self.assertLogs("history_outbox"):
            result = enqueue_completed_best_effort(self.box, "not UUID", self.start, .25)
        self.assertEqual(result.code, "OUTBOX_CAPTURE_FAILED")
        self.assertEqual(self.box.records(), [])

    def test_cli_local_commands_and_explicit_disabled_drain(self):
        p = self.prepared()
        payload = self.root / "prepared.json"
        payload.write_bytes(p.body)
        args = ["--db", str(self.path)]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(args + ["enqueue", str(payload)]), 0)
            self.assertEqual(main(args + ["status"]), 0)
        with patch.dict("os.environ", {}, clear=True), self.assertLogs("history_outbox"):
            self.assertEqual(main(args + ["drain"]), 1)
        self.assertEqual(self.box.records()[0]["attempts"], 0)

    def test_operational_entrypoints_do_not_import_or_activate_outbox(self):
        root = Path(__file__).resolve().parents[1]
        for filename in ("monitor.py", "health_runner.py", ".github/workflows/seat-monitor.yml"):
            self.assertNotIn("history_outbox", (root / filename).read_text())


if __name__ == "__main__":
    unittest.main()

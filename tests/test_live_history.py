"""Offline structured producer and subprocess adapter integration tests.

Only synthetic facts, mocked GMU/Bark, and loopback HTTP are used. HTTP workers
run in real subprocesses so hard deadlines/cancellation are exercised, not mocked.
"""

import contextlib
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import uuid

import health_runner
import history_capture
from history_capture import CheckCapture, read_prepared
import history_submit
import monitor

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "synthetic_local_only_token_00000000000000000000000"
PRIVATE = "PRIVATE_STUDENT_DO_NOT_STORE"


def row(total=1, summary="1 of 10 unreserved"):
    return {"seatsAvailable": total, "reservedSeatSummary": summary,
            "studentId": PRIVATE, "courseTitle": "Synthetic public course"}


@contextlib.contextmanager
def local_api(mode="ok", status=503, duration=3.6):
    """Bounded local server; received bodies stand in for committed originals."""
    bodies = []
    arrived = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            bodies.append(self.rfile.read(int(self.headers["Content-Length"])))
            arrived.set()
            if mode == "lost_first" and len(bodies) == 1:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if mode in {"hang", "trickle"}:
                self.send_response(200)
                self.end_headers()
                try:
                    for _ in range(int(duration / 0.03)):
                        self.wfile.write(b" " if mode == "trickle" else b"")
                        self.wfile.flush()
                        time.sleep(0.03)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            payload = json.loads(bodies[-1])
            ack = json.dumps({"code": "INGESTED", "check_id": payload["check_id"]}).encode()
            self.send_response(status if mode == "http_error" else 200)
            self.send_header("Content-Length", str(len(ack)))
            self.end_headers()
            self.wfile.write(ack)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/checks", bodies, arrived
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def submission_env(endpoint):
    return {"GMU_HISTORY_ENABLED": "true", "GMU_HISTORY_PRODUCTION_ENDPOINT": endpoint,
            "GMU_HISTORY_PRODUCTION_TOKEN": TOKEN}


class ProducerTests(unittest.TestCase):
    def run_monitor(self, root, lookups, enabled=True, state=None):
        logs = io.StringIO()
        capture_root = root / "history-handoff"
        env = {"GMU_HISTORY_CAPTURE_ENABLED": "true" if enabled else "false",
               "GMU_HISTORY_CAPTURE_DIR": str(capture_root)}
        if state is not None:
            (root / "seat_state.json").write_text(json.dumps(state))
        with (patch.dict(os.environ, env, clear=True),
              patch.object(sys, "argv", ["monitor.py", "--once"]),
              patch.object(monitor, "STATE_PATH", root / "seat_state.json"),
              patch.object(monitor, "resolve_term_code", return_value="202710") as term,
              patch.object(monitor, "get_section", side_effect=lookups) as fetch,
              patch.object(monitor, "notify", return_value=True) as bark,
              patch.object(monitor.time, "sleep"),
              patch("requests.sessions.Session.request", side_effect=AssertionError("external HTTP forbidden")),
              contextlib.redirect_stdout(logs)):
            result = monitor.main()
        term.assert_called_once()
        self.assertEqual(fetch.call_count, 2)
        return result, bark.call_args_list, logs.getvalue()

    def prepared(self, root):
        directory, = (root / "history-handoff").iterdir()
        return read_prepared(directory, "pending.json")

    def test_two_successful_original_lookups_share_one_uuid_and_utc_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result, alerts, _ = self.run_monitor(root, [row(), row(9, "0 of 10 unreserved")])
            prepared = self.prepared(root)
            body = json.loads(prepared.body)
            self.assertEqual((result, len(alerts), body["status"]), (0, 1, "success"))
            self.assertEqual(uuid.UUID(prepared.check_id).version, 4)
            self.assertEqual(datetime.fromisoformat(body["executed_at_utc"]).utcoffset().total_seconds(), 0)
            self.assertEqual([o["crn"] for o in body["observations"]], ["17582", "17593"])
            self.assertNotIn(PRIVATE.encode(), prepared.body)

    def test_partial_failure_in_either_order_retains_only_original_success(self):
        for results, expected in (([row(), monitor.SearchError("synthetic")], "17582"),
                                  ([monitor.SearchError("synthetic"), row()], "17593")):
            with self.subTest(crn=expected), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                result, alerts, _ = self.run_monitor(root, results)
                body = json.loads(self.prepared(root).body)
                self.assertEqual((result, body["status"], body["error_code"]),
                                 (1, "failed", "COURSE_LOOKUP_FAILED"))
                self.assertEqual([o["crn"] for o in body["observations"]], [expected])
                self.assertEqual(len(alerts), 1)
                self.assertEqual(set(json.loads((root / "seat_state.json").read_text())), {expected})

    def test_complete_lookup_failure_never_invents_cached_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = {"17582": row(99)}
            result, alerts, _ = self.run_monitor(root, [monitor.SearchError("synthetic")] * 2, state=old)
            body = json.loads(self.prepared(root).body)
            self.assertEqual((result, alerts, body["observations"]), (1, [], []))
            self.assertEqual(json.loads((root / "seat_state.json").read_text()), old)

    def test_term_failure_is_empty_failed_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (patch.dict(os.environ, {"GMU_HISTORY_CAPTURE_ENABLED": "true", "GMU_HISTORY_CAPTURE_DIR": tmp}, clear=True),
                  patch.object(sys, "argv", ["monitor.py", "--once"]),
                  patch.object(monitor, "load_state", return_value={}),
                  patch.object(monitor, "resolve_term_code", side_effect=monitor.SearchError("synthetic")),
                  patch.object(monitor, "get_section") as fetch,
                  patch.object(monitor, "notify") as bark, contextlib.redirect_stdout(io.StringIO())):
                self.assertEqual(monitor.main(), 1)
            directory, = root.iterdir()
            self.assertEqual(json.loads(read_prepared(directory, "pending.json").body)["observations"], [])
            fetch.assert_not_called()
            bark.assert_not_called()

    def test_enabled_and_disabled_preserve_exact_alerts_state_and_health(self):
        for rows in ([row(), row(9, "0 of 10 unreserved")],
                     [row(9, "0 of 10 unreserved"), row(0)],
                     [row(), monitor.SearchError("synthetic")]):
            outcomes = []
            for enabled in (False, True):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    code, alerts, logs = self.run_monitor(root, rows, enabled=enabled)
                    health = health_runner.summarize_output(logs, code)
                    outcomes.append((code, alerts, (root / "seat_state.json").read_bytes(), health))
            self.assertEqual(outcomes[0], outcomes[1])

    def test_capture_disabled_by_default_despite_ingestion_configuration(self):
        for value in (None, "false", "TRUE", "1"):
            env = submission_env("https://history.invalid/v1/checks")
            if value is not None:
                env["GMU_HISTORY_CAPTURE_ENABLED"] = value
            with (patch.dict(os.environ, env, clear=True),
                  patch("history_capture.CheckCapture", side_effect=AssertionError("must not create files"))):
                self.assertIsNone(monitor.start_history_capture())

    def test_fresh_disabled_entry_points_do_not_import_capture_or_http_history(self):
        code = "import sys, monitor, health_runner; assert monitor.start_history_capture() is None; assert 'history_capture' not in sys.modules; assert 'cloud_history' not in sys.modules"
        proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                              env={"PATH": os.environ.get("PATH", "")}, capture_output=True, timeout=3)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, b"")

    def test_history_preparation_and_storage_errors_do_not_change_monitoring(self):
        for target in ("public_observation", "atomic_write", "CheckCapture"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with patch("history_capture." + target, side_effect=RuntimeError(TOKEN)):
                    code, alerts, logs = self.run_monitor(root, [row(), row()])
                self.assertEqual((code, len(alerts)), (0, 2))
                self.assertTrue(health_runner.summarize_output(logs, code)[0])
                self.assertEqual(len(json.loads((root / "seat_state.json").read_text())), 2)
                self.assertIn("CAPTURE_PREPARATION_FAILED", logs)
                self.assertNotIn(TOKEN, logs)

    def test_slow_or_failing_capture_runs_after_bark_and_persisted_state(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as tmp:
                events = []
                state = {}
                state_path = Path(tmp) / "seat_state.json"
                capture = Mock()
                def observe(term, crn, number, current):
                    self.assertEqual(events, ["bark", "save"])
                    self.assertEqual(json.loads(state_path.read_text())[crn], current)
                    self.assertEqual((term, crn, number), ("202710", "17582", "304"))
                    events.append("capture")
                    time.sleep(0.05)  # Deliberate synchronous capture latency.
                    if fail:
                        raise OSError(TOKEN)
                capture.observe.side_effect = observe
                real_save = monitor.save_state
                def save(current):
                    real_save(current)
                    events.append("save")
                logs = io.StringIO()
                with (patch.object(monitor, "COURSES", {"17582": {"name": "IT 304 DL1", "number": "304"}}),
                      patch.object(monitor, "STATE_PATH", state_path),
                      patch.object(monitor, "get_section", return_value=row()) as lookup,
                      patch.object(monitor, "notify", side_effect=lambda *args: events.append("bark")),
                      patch.object(monitor, "save_state", side_effect=save),
                      patch.object(monitor, "time", Mock(sleep=lambda seconds: events.append(("sleep", seconds)))),
                      contextlib.redirect_stdout(logs)):
                    self.assertEqual(monitor.check_once("202710", state, capture), 0)
                self.assertEqual(events, ["bark", "save", "capture", ("sleep", 2)])
                lookup.assert_called_once_with("202710", "17582", "304")
                self.assertNotIn(TOKEN, logs.getvalue())

    def test_interruption_after_second_state_save_keeps_only_first_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            capture = CheckCapture(root / "history-handoff")
            state = {}
            real_save = monitor.save_state
            def save(current):
                real_save(current)
                if "17593" in current:
                    raise KeyboardInterrupt()
            try:
                with (patch.object(monitor, "STATE_PATH", root / "seat_state.json"),
                      patch.object(monitor, "get_section", side_effect=[row(), row()]) as lookup,
                      patch.object(monitor, "notify", return_value=True) as bark,
                      patch.object(monitor, "save_state", side_effect=save),
                      patch.object(monitor.time, "sleep"), contextlib.redirect_stdout(io.StringIO())):
                    with self.assertRaises(KeyboardInterrupt):
                        monitor.check_once("202710", state, capture)
                    self.assertEqual((lookup.call_count, bark.call_count), (2, 2))
            finally:
                capture.close()
            payload = json.loads(read_prepared(capture.directory, "pending.json").body)
            self.assertEqual((payload["status"], payload["error_code"]), ("failed", "CHECK_FAILED"))
            self.assertEqual([o["crn"] for o in payload["observations"]], ["17582"])
            self.assertEqual(payload["check_id"], capture.check_id)
            self.assertEqual(datetime.fromisoformat(payload["executed_at_utc"]), capture.sampled_at)
            self.assertEqual(set(json.loads((root / "seat_state.json").read_text())), {"17582", "17593"})

    def test_one_preparation_failure_does_not_poison_other_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = history_capture.public_observation
            calls = 0
            def sometimes(*args):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise ValueError(PRIVATE)
                return real(*args)
            with patch("history_capture.public_observation", side_effect=sometimes):
                code, alerts, logs = self.run_monitor(root, [row(), row()])
            body = json.loads(self.prepared(root).body)
            self.assertEqual((code, len(alerts), body["status"], body["error_code"]),
                             (0, 2, "failed", "CHECK_FAILED"))
            self.assertEqual([o["crn"] for o in body["observations"]], ["17593"])
            self.assertNotIn(PRIVATE, logs)

    def test_timestamp_uuid_stable_from_initial_checkpoint_through_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture = CheckCapture(tmp)
            before = json.loads(read_prepared(capture.directory, "pending.json").body)
            capture.observe("202710", "17582", "304", row())
            capture.finish(0)
            after = json.loads(read_prepared(capture.directory, "pending.json").body)
            for key in ("check_id", "executed_at_utc"):
                self.assertEqual(before[key], after[key])
            self.assertEqual(after["status"], "success")

    def test_closed_producer_cannot_revise_completed_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture = CheckCapture(tmp)
            capture.observe("202710", "17582", "304", row())
            capture.finish(0)
            original = read_prepared(capture.directory, "pending.json")
            with self.assertRaises(ValueError):
                capture.observe("202710", "17593", "341", row(99))
            with self.assertRaises(ValueError):
                capture.finish(1)
            self.assertEqual(read_prepared(capture.directory, "pending.json"), original)

    def test_sanitization_drops_private_fields_text_and_unknown_dict_keys(self):
        samples = [row(1, f"1 of 10 unreserved; {PRIVATE}"), row(1, PRIVATE),
                   row(1, {"unreservedSeatsRemaining": 1, "studentId": PRIVATE, "token": TOKEN}),
                   row(True, [PRIVATE]), row(1, {"unreservedAvailable": 0, "unreservedRemaining": 1})]
        with tempfile.TemporaryDirectory() as tmp:
            for sample in samples:
                capture = CheckCapture(tmp)
                capture.observe("202710", "17582", "304", sample)
                capture.finish(0)
                for path in capture.directory.iterdir():
                    self.assertNotIn(PRIVATE.encode(), path.read_bytes())
                    self.assertNotIn(TOKEN.encode(), path.read_bytes())
                body = json.loads(read_prepared(capture.directory, "pending.json").body)
                observation = body["observations"][0]
                self.assertEqual(set(observation), {"term", "subject", "course_number", "section", "crn", "total_available", "reserved_summary"})
                self.assertEqual((capture.directory / "pending.json").stat().st_mode & 0o777, 0o600)
                if sample["seatsAvailable"] is True:
                    self.assertIsNone(observation["total_available"])


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "history-handoff"
        self.capture = CheckCapture(self.root)
        self.capture.observe("202710", "17582", "304", row())
        self.capture.finish(0)

    def submit(self, endpoint, deadline=5):
        with patch.dict(os.environ, submission_env(endpoint), clear=True):
            return history_submit.submit_with_deadline(self.root, deadline, allow_local_http=True)

    def test_disabled_does_not_start_worker_read_files_or_network(self):
        with patch.dict(os.environ, {}, clear=True), patch("subprocess.Popen") as start:
            self.assertEqual(history_submit.submit_with_deadline("/does/not/exist")["outcome"], "disabled")
            start.assert_not_called()

    def test_lost_ack_retries_identical_uuid_bytes_and_receipt_skips_resending(self):
        original = read_prepared(self.capture.directory, "pending.json").body
        with local_api("lost_first") as (endpoint, bodies, _):
            result = self.submit(endpoint)
            self.assertEqual((result["outcome"], result["ingested"]), ("success", 1))
            self.assertEqual(bodies, [original, original])
            self.assertEqual(self.submit(endpoint)["outcome"], "success")
            self.assertEqual(len(bodies), 2)
        self.assertEqual(read_prepared(self.capture.directory).body, original)
        self.assertFalse((self.capture.directory / "pending.json").exists())

    def test_http_outage_bounded_then_later_retry_is_identical(self):
        with local_api("http_error") as (endpoint, bodies, _):
            result = self.submit(endpoint)
            self.assertEqual((result["outcome"], result["code"]), ("unconfirmed", "RETRYABLE_HTTP_FAILURE"))
            self.assertEqual(len(bodies), 3)
            self.assertEqual(len(set(bodies)), 1)
            original = bodies[0]
        with local_api() as (endpoint, bodies, _):
            self.assertEqual(self.submit(endpoint)["outcome"], "success")
            self.assertEqual(bodies, [original])

    def test_permanent_http_failure_has_one_attempt(self):
        with local_api("http_error", status=401) as (endpoint, bodies, _):
            result = self.submit(endpoint)
            self.assertEqual((result["outcome"], result["code"]), ("unconfirmed", "UNAUTHORIZED"))
            self.assertEqual(len(bodies), 1)

    def test_timeout_and_trickling_ack_have_hard_total_wall_clock_deadline(self):
        for mode in ("hang", "trickle"):
            with self.subTest(mode=mode), local_api(mode) as (endpoint, bodies, arrived):
                started = time.monotonic()
                result = self.submit(endpoint, deadline=0.7)
                self.assertEqual(result["outcome"], "unconfirmed")
                self.assertIn(result["code"], {"DEADLINE", "WORKER_FAILED"})
                self.assertLess(time.monotonic() - started, 2)
                self.assertTrue(arrived.is_set())
                self.assertEqual(len(bodies), 1)
                self.assertFalse((self.capture.directory / "receipt.sha256").exists())

    def test_default_twenty_second_deadline_stops_continuously_active_response(self):
        with local_api("trickle", duration=30) as (endpoint, bodies, arrived):
            started = time.monotonic()
            with patch.dict(os.environ, submission_env(endpoint), clear=True):
                result = history_submit.submit_with_deadline(self.root, allow_local_http=True)
            elapsed = time.monotonic() - started
            self.assertEqual((result["outcome"], result["code"]), ("unconfirmed", "DEADLINE"))
            self.assertGreater(elapsed, 19)
            self.assertLess(elapsed, 23)
            self.assertTrue(arrived.is_set())
            self.assertEqual(len(bodies), 1)
            self.assertFalse((self.capture.directory / "receipt.sha256").exists())

    def test_unexpected_client_errors_after_send_remain_unconfirmed_and_redacted(self):
        original = read_prepared(self.capture.directory, "pending.json")
        for error in (ValueError, TypeError, OSError, RuntimeError):
            with self.subTest(error=error.__name__), patch.dict(os.environ, submission_env("https://history.invalid/v1/checks"), clear=True):
                with patch("cloud_history.CloudHistoryClient.submit", side_effect=error(TOKEN)) as submit:
                    result = history_submit._worker(self.root)
                self.assertEqual((result["outcome"], result["code"]), ("unconfirmed", "CLIENT_ERROR"))
                self.assertEqual(submit.call_args.args, (original,))
                self.assertEqual(read_prepared(self.capture.directory), original)
                self.assertNotIn(TOKEN, json.dumps(result))
                self.assertFalse((self.capture.directory / "receipt.sha256").exists())

    def test_active_producer_cannot_be_sealed_or_submitted(self):
        active = CheckCapture(self.root)
        self.addCleanup(active.close)
        with local_api() as (endpoint, _, _):
            result = self.submit(endpoint)
        self.assertEqual((result["outcome"], result["code"]), ("failed", "CAPTURE_BUSY"))
        self.assertFalse((active.directory / "check.json").exists())

    def test_missing_or_invalid_production_config_never_uses_staging_fallback(self):
        for env in ({}, {"GMU_HISTORY_ENDPOINT": "https://staging.invalid/v1/checks", "GMU_HISTORY_TOKEN": TOKEN},
                    {"GMU_HISTORY_PRODUCTION_ENDPOINT": "https://secret:private@history.invalid/v1/checks",
                     "GMU_HISTORY_PRODUCTION_TOKEN": TOKEN}):
            with patch.dict(os.environ, {**env, "GMU_HISTORY_ENABLED": "true"}, clear=True):
                result = history_submit.submit_with_deadline(self.root, 2)
            self.assertEqual((result["outcome"], result["code"]), ("failed", "CONFIGURATION_ERROR"))
            self.assertFalse((self.capture.directory / "check.json").exists())

    def test_forged_payload_never_reaches_http_or_leaks_into_report(self):
        (self.capture.directory / "pending.json").write_text(json.dumps({"studentId": PRIVATE, "token": TOKEN}))
        with local_api() as (endpoint, bodies, _):
            result = self.submit(endpoint)
            self.assertEqual((result["outcome"], result["code"]), ("failed", "CAPTURE_INVALID"))
            self.assertEqual(bodies, [])
        report_path = Path(self.tmp.name) / "report.json"
        summary = Path(self.tmp.name) / "summary.md"
        logs = io.StringIO()
        with contextlib.redirect_stdout(logs):
            history_submit.report(result, report_path, summary)
        for text in (report_path.read_text(), summary.read_text(), logs.getvalue()):
            self.assertNotIn(PRIVATE, text)
            self.assertNotIn(TOKEN, text)
            self.assertNotIn("staging.invalid", text)

    def test_receipts_are_scoped_to_endpoint(self):
        with local_api() as (endpoint, bodies, _):
            self.assertEqual(self.submit(endpoint)["outcome"], "success")
            self.assertEqual(len(bodies), 1)
        with local_api() as (endpoint, bodies, _):
            self.assertEqual(self.submit(endpoint)["outcome"], "success")
            self.assertEqual(len(bodies), 1)

    def test_interrupted_producer_preserves_completed_lookup_without_new_requests(self):
        root = Path(self.tmp.name) / "interrupted"
        marker = Path(self.tmp.name) / "second-lookup"
        code = '''
import contextlib, io, sys, time
from pathlib import Path
from unittest.mock import patch
import monitor
from history_capture import CheckCapture
capture = CheckCapture(sys.argv[1])
def lookup(term, crn, number):
    if crn == "17582":
        return {"seatsAvailable": 1, "reservedSeatSummary": "1 of 10 unreserved"}
    Path(sys.argv[2]).touch()
    time.sleep(100)
with patch.object(monitor, "get_section", side_effect=lookup), patch.object(monitor, "notify"), patch.object(monitor, "save_state"), contextlib.redirect_stdout(io.StringIO()):
    monitor.check_once("202710", {}, capture)
'''
        proc = subprocess.Popen([sys.executable, "-c", code, str(root), str(marker)], cwd=ROOT,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            limit = time.monotonic() + 6
            while not marker.exists() and time.monotonic() < limit:
                time.sleep(0.02)
            self.assertTrue(marker.exists())
        finally:
            proc.kill()
            proc.wait(timeout=2)
        directory, = root.iterdir()
        original = read_prepared(directory, "pending.json").body
        with local_api() as (endpoint, bodies, _), patch.dict(os.environ, submission_env(endpoint), clear=True):
            result = history_submit.submit_with_deadline(root, 3, allow_local_http=True)
        self.assertEqual(result["outcome"], "success")
        self.assertEqual(bodies, [original])
        payload = json.loads(original)
        self.assertEqual((payload["status"], payload["error_code"]), ("failed", "CHECK_FAILED"))
        self.assertEqual([o["crn"] for o in payload["observations"]], ["17582"])

    def test_submitter_sigterm_stops_child_and_retains_original_for_retry(self):
        original = read_prepared(self.capture.directory, "pending.json").body
        report = Path(self.tmp.name) / "report.json"
        synthetic_summary = Path(self.tmp.name) / "synthetic-actions-summary.md"
        with local_api("trickle") as (endpoint, bodies, arrived):
            proc = subprocess.Popen([sys.executable, str(ROOT / "history_submit.py"), str(self.root),
                                     "--deadline-seconds", "5", "--allow-local-http", "--report", str(report)],
                                    env={**os.environ, **submission_env(endpoint),
                                         "GITHUB_STEP_SUMMARY": str(synthetic_summary)}, cwd=ROOT,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                self.assertTrue(arrived.wait(3))
                proc.send_signal(signal.SIGTERM)
                output, errors = proc.communicate(timeout=2)
                self.assertEqual(proc.returncode, 1)
                self.assertEqual(json.loads(report.read_text())["code"], "INTERRUPTED")
                self.assertIn("unconfirmed: INTERRUPTED", synthetic_summary.read_text())
                self.assertEqual(bodies, [original])
                self.assertNotIn(TOKEN.encode(), output + errors)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()
        self.assertEqual(read_prepared(self.capture.directory).body, original)
        with local_api() as (endpoint, bodies, _):
            self.assertEqual(self.submit(endpoint)["outcome"], "success")
            self.assertEqual(bodies, [original])

    def test_supervisor_sigkill_leaves_worker_bounded_by_its_own_deadline(self):
        import fcntl
        with local_api("trickle") as (endpoint, bodies, arrived):
            proc = subprocess.Popen([sys.executable, str(ROOT / "history_submit.py"), str(self.root),
                                     "--deadline-seconds", "0.8", "--allow-local-http"],
                                    env={**os.environ, **submission_env(endpoint)}, cwd=ROOT,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                self.assertTrue(arrived.wait(3))
                proc.kill()
                proc.wait(timeout=2)
                limit = time.monotonic() + 2
                released = False
                with (self.capture.directory / "owner.lock").open("a+b") as lock:
                    while time.monotonic() < limit:
                        try:
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            released = True
                            break
                        except BlockingIOError:
                            time.sleep(0.02)
                self.assertTrue(released, "orphan HTTP worker exceeded its independent deadline")
                self.assertEqual(len(bodies), 1)
                self.assertFalse((self.capture.directory / "receipt.sha256").exists())
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=2)

    def test_lost_local_receipt_safely_resubmits_same_body(self):
        with local_api() as (endpoint, bodies, _):
            self.assertEqual(self.submit(endpoint)["outcome"], "success")
            (self.capture.directory / "receipt.sha256").unlink()
            self.assertEqual(self.submit(endpoint)["outcome"], "success")
            self.assertEqual(len(bodies), 2)
            self.assertEqual(bodies[0], bodies[1])

    def test_bounded_configuration_and_separate_report_failure(self):
        with patch.dict(os.environ, {"GMU_HISTORY_ENABLED": "true"}, clear=True):
            for deadline in (0, -1, float("inf"), float("nan"), 46, True):
                self.assertEqual(history_submit.submit_with_deadline(self.root, deadline)["code"], "CONFIGURATION_ERROR")
        logs = io.StringIO()
        with contextlib.redirect_stdout(logs):
            history_submit.report(history_submit.outcome("unconfirmed", "TIMEOUT"),
                                  Path(self.tmp.name) / "missing/report.json", "/missing/summary.md")
        self.assertIn("WRITE_FAILED", logs.getvalue())
        self.assertFalse((self.root / "run_health.json").exists())


if __name__ == "__main__":
    unittest.main()

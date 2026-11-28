"""Offline checks for independent active and retired IT 304 sections."""
import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch
import uuid

import health_runner
import monitor
from cloud_history import prepare_check, validate_prepared
from seat_history import CourseObservation, SeatHistory


def sample(crn="21146", unreserved=0):
    course = monitor.COURSES[crn]
    return {"term": "202710", "subject": "IT", "courseNumber": course["number"],
            "sequenceNumber": course["section"], "courseReferenceNumber": crn,
            "seatsAvailable": 4 + unreserved,
            "reservedSeatSummary": {"reservedAvailable": 4, "unreservedAvailable": unreserved}}


class IT304DL2Tests(unittest.TestCase):
    def test_preserves_all_original_targets_and_distinct_section_identity(self):
        expected = {"17582": ("304", "DL1", "retired"), "17593": ("341", "DL1", "active"),
                    "17596": ("342", "DL1", "active"), "17599": ("343", "DL1", "active"),
                    "20981": ("343", "DL2", "active"), "21051": ("343", "DL3", "active"),
                    "21146": ("304", "DL2", "active")}
        self.assertEqual({crn: (c["number"], c["section"], c["status"]) for crn,c in monitor.COURSES.items()}, expected)
        self.assertEqual(len(monitor.active_courses()), 6)
        self.assertEqual(monitor.course_section("17582", "304"), "DL1")
        self.assertEqual(monitor.course_section("21146", "304"), "DL2")

    def lookup(self, rows):
        manager = MagicMock()
        with patch.object(monitor, "new_session", return_value=manager), patch.object(monitor, "_json_request", side_effect=[{"fwdURL": "public"}, {"success": True, "data": rows}]):
            return monitor.get_section("202710", "21146", "304")

    def test_exact_identity_rejects_retired_section_and_each_mismatch(self):
        correct = sample()
        wrong = [sample("17582")] + [dict(correct, **change) for change in (
            {"term": "202810"}, {"subject": "CS"}, {"courseNumber": "341"},
            {"sequenceNumber": "DL1"}, {"courseReferenceNumber": "17582"})]
        self.assertIs(self.lookup(wrong + [correct]), correct)
        for candidate in wrong:
            with self.subTest(candidate=candidate), self.assertRaises(monitor.SearchError):
                self.lookup([candidate])

    def test_reserved_only_then_opening_and_duplicates_preserve_retired_state(self):
        state = {crn: monitor.snapshot(sample(crn)) for crn in monitor.COURSES}
        retired = dict(state["17582"])
        current = {crn: sample(crn) for crn in monitor.active_courses()}
        with tempfile.TemporaryDirectory() as tmp, patch.object(monitor, "STATE_PATH", Path(tmp)/"state.json"), patch.object(monitor, "get_section", side_effect=lambda term, crn, number: current[crn]) as lookup, patch.object(monitor, "notify", return_value=True) as notify, patch.object(monitor.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(monitor.check_once("202710", state), 0)
            notify.assert_not_called()
            current["21146"] = sample(unreserved=1)
            self.assertEqual(monitor.check_once("202710", state), 0)
            notify.assert_called_once()
            self.assertIn("IT 304 DL2 (CRN 21146)", notify.call_args.args[0])
            self.assertEqual(notify.call_args.kwargs, {"actionable": True})
            self.assertEqual(monitor.check_once("202710", state), 0)
            self.assertEqual(notify.call_count, 1)
            self.assertEqual(state["17582"], retired)
            self.assertNotIn("17582", [call.args[1] for call in lookup.call_args_list])
            self.assertTrue(health_runner.state_is_valid(monitor.STATE_PATH))

    def test_six_target_health_requires_new_section(self):
        lines = {crn: f"[fixture] {c['name']}: 0 total seats remaining.\n" for crn,c in monitor.active_courses().items()}
        self.assertTrue(health_runner.summarize_output("".join(lines.values()), 0)[0])
        self.assertFalse(health_runner.summarize_output("".join(v for k,v in lines.items() if k != "21146"), 0)[0])

    def test_history_and_protocol_keep_both_it304_sections_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SeatHistory(Path(tmp)/"history.sqlite3"); store.initialize()
            at = datetime.now(timezone.utc)
            original = [CourseObservation("202710", "IT", c["number"], c["section"], crn, 0) for crn,c in monitor.COURSES.items() if crn != "21146"]
            store.record_check("original", at, .25, original)
            before = {o.crn: store.history("202710", o.crn) for o in original}
            for i, count in enumerate([0, 1]):
                store.record_check("new-"+str(i), at+timedelta(minutes=(i+1)*5), .25,
                    [CourseObservation("202710", "IT", "304", "DL2", "21146", 4+count, {"unreservedAvailable": count})])
            self.assertEqual(len(store.history("202710", "21146")), 2)
            self.assertEqual(len(store.opening_events("202710", "21146")), 1)
            for crn, rows in before.items(): self.assertEqual(store.history("202710", crn), rows)
            prepared = prepare_check(str(uuid.uuid4()), at, .25, original + [CourseObservation("202710", "IT", "304", "DL2", "21146", 0)])
            validate_prepared(prepared)
            rows = json.loads(prepared.body)["observations"]
            self.assertEqual(len(rows), 7)
            self.assertEqual([(o["crn"], o["section"]) for o in rows if o["course_number"] == "304"], [("17582", "DL1"), ("21146", "DL2")])

    def test_six_target_request_count_and_synthetic_runtime_budget(self):
        elapsed = [0.0]
        def reply(*args, **kwargs):
            elapsed[0] += .25
            self.assertEqual(kwargs["timeout"], 20)
            response = Mock()
            response.json.return_value = {"fwdURL": "public"} if args[0] == "POST" else {"success": True, "data": [sample(crn) for crn in monitor.COURSES]}
            return response
        session = Mock(); session.request.side_effect = reply
        session.get.side_effect = lambda *a, **kw: reply("GET", *a, **kw)
        manager = MagicMock(); manager.__enter__.return_value = session
        with patch.object(monitor, "new_session", return_value=manager), patch.object(monitor, "save_state"), patch.object(monitor, "notify") as notify, patch.object(monitor.time, "sleep", side_effect=lambda seconds: elapsed.__setitem__(0, elapsed[0]+seconds)) as sleep, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(monitor.check_once("202710", {}), 0)
        self.assertEqual(session.get.call_count + session.request.call_count, 24)
        self.assertEqual(sleep.call_count, 6)
        self.assertEqual(elapsed[0], 18)
        self.assertLess(elapsed[0], health_runner.CHECK_TIMEOUT_SECONDS)
        self.assertEqual(health_runner.CHECK_TIMEOUT_SECONDS, 240)
        notify.assert_not_called()

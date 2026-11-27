"""Offline IT342 identity, notification and historical-storage regressions."""
import contextlib
from datetime import datetime, timezone, timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, MagicMock, patch
import uuid
import monitor
import health_runner
from cloud_history import prepare_check, validate_prepared
from seat_history import CourseObservation, SeatHistory


def row(crn="17596", unreserved=0):
    c=monitor.COURSES[crn]
    return {"term":"202710","subject":"IT","courseNumber":c["number"],
            "sequenceNumber":c["section"],"courseReferenceNumber":crn,
            "seatsAvailable":unreserved+4,"reservedSeatSummary":{"unreservedAvailable":unreserved,"reservedAvailable":4}}


class IT342Tests(unittest.TestCase):
    def test_identity_and_existing_lifecycle_are_preserved(self):
        self.assertEqual(list(monitor.active_courses()),["17593","17596","17599","20981","21051"])
        self.assertEqual(monitor.COURSES["17596"],{"name":"IT 342 DL1","number":"342","section":"DL1","status":"active"})
        self.assertEqual(monitor.COURSES["17582"]["status"],"retired")
        self.assertEqual(monitor.course_section("17596","342"),"DL1")
        with self.assertRaises(ValueError):monitor.course_section("17596","343")

    def lookup(self,rows):
        session=Mock();manager=MagicMock();manager.__enter__.return_value=session
        with patch.object(monitor,"new_session",return_value=manager),patch.object(monitor,"_json_request",side_effect=[{"fwdURL":"public"},{"success":True,"data":rows}]):
            return monitor.get_section("202710","17596","342")

    def test_exact_term_subject_number_section_and_crn_are_required(self):
        correct=row()
        wrong=[dict(correct,**changes) for changes in [{"term":"202810"},{"subject":"CS"},{"courseNumber":"343"},{"sequenceNumber":"DL2"},{"courseReferenceNumber":"17599"}]]
        self.assertIs(self.lookup(wrong+[correct]),correct)
        for candidate in wrong:
            with self.subTest(candidate=candidate),self.assertRaises(monitor.SearchError):self.lookup([candidate])

    def test_reserved_only_opening_and_duplicate_state_are_independent(self):
        state={crn:monitor.snapshot(row(crn)) for crn in monitor.COURSES}
        before={crn:dict(value) for crn,value in state.items() if crn!="17596"}
        current={crn:row(crn) for crn in monitor.active_courses()}
        with tempfile.TemporaryDirectory() as tmp,patch.object(monitor,"STATE_PATH",Path(tmp)/"state.json"),patch.object(monitor,"get_section",side_effect=lambda term,crn,number:current[crn]) as lookup,patch.object(monitor,"notify",return_value=True) as notify,patch.object(monitor.time,"sleep"),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(monitor.check_once("202710",state),0);notify.assert_not_called()
            current["17596"]=row(unreserved=1)
            self.assertEqual(monitor.check_once("202710",state),0);notify.assert_called_once()
            self.assertIn("IT 342 DL1 (CRN 17596)",notify.call_args.args[0]);self.assertEqual(notify.call_args.kwargs,{"actionable":True})
            self.assertEqual(monitor.check_once("202710",state),0);self.assertEqual(notify.call_count,1)
            self.assertEqual(json.loads(monitor.STATE_PATH.read_text())["17596"],state["17596"])
            self.assertTrue(health_runner.state_is_valid(monitor.STATE_PATH))
            self.assertEqual([c.args for c in lookup.call_args_list[:5]],[("202710",crn,c["number"]) for crn,c in monitor.active_courses().items()])
        for crn,original in before.items():self.assertEqual(state[crn],original)

    def test_health_requires_the_new_target(self):
        lines={crn:f"[fixture] {c['name']}: 0 total seats remaining.\n" for crn,c in monitor.active_courses().items()}
        self.assertTrue(health_runner.summarize_output("".join(lines.values()),0)[0])
        self.assertFalse(health_runner.summarize_output("".join(text for crn,text in lines.items() if crn!="17596"),0)[0])

    def test_payload_accepts_six_independent_identities_without_rewriting_bytes(self):
        observations=[CourseObservation("202710","IT",c["number"],c["section"],crn,0) for crn,c in monitor.COURSES.items()]
        prepared=prepare_check(str(uuid.uuid4()),datetime.now(timezone.utc),.25,observations)
        before=prepared.body;validate_prepared(prepared);self.assertEqual(before,prepared.body)
        rows=json.loads(before)["observations"];self.assertEqual(len(rows),6)
        self.assertEqual(next(r for r in rows if r["crn"]=="17596")["course_number"],"342")

    def test_new_history_preserves_all_existing_course_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=SeatHistory(Path(tmp)/"history.sqlite3");store.initialize();at=datetime.now(timezone.utc)
            old=[CourseObservation("202710","IT",c["number"],c["section"],crn,0) for crn,c in monitor.COURSES.items() if crn!="17596"]
            store.record_check("original",at,.25,old)
            before={o.crn:store.history("202710",o.crn) for o in old}
            for i,count in enumerate([0,1]):store.record_check("new-"+str(i),at+timedelta(minutes=5+i*5),.25,[CourseObservation("202710","IT","342","DL1","17596",4,{"unreservedAvailable":count})])
            self.assertEqual(len(store.history("202710","17596")),2);self.assertEqual(len(store.opening_events("202710","17596")),1)
            for crn,original in before.items():self.assertEqual(store.history("202710",crn),original)

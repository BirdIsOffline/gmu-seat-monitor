"""IT343 reference fixtures only; no catalog, Bark or production history requests."""
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


def row(unreserved=0, reserved=16):
    return {"term":"202710", "subject":"IT", "courseNumber":"343", "sequenceNumber":"DL3",
            "courseReferenceNumber":"21051", "seatsAvailable":unreserved+reserved,
            "reservedSeatSummary":f"{unreserved} of 16 unreserved; {reserved} of 16 reserved"}


class IT343Tests(unittest.TestCase):
    def test_configured_identity_and_active_allowlist(self):
        self.assertEqual(monitor.COURSES["17582"]["status"], "retired")
        self.assertEqual(list(monitor.active_courses()), ["17593","21051"])
        self.assertEqual(monitor.course_section("21051","343"),"DL3")
        self.assertEqual(monitor.course_section("17593","341"),"DL1")
        with self.assertRaises(ValueError):monitor.course_section("21051","341")

    def lookup(self,rows):
        session=Mock(); context=MagicMock();context.__enter__.return_value=session
        with (patch.object(monitor,"new_session",return_value=context),
              patch.object(monitor,"_json_request",side_effect=[{"fwdURL":"public"},{"success":True,"data":rows}])):
            return monitor.get_section("202710","21051","343")

    def test_exact_dl3_section_is_selected_over_dl1(self):
        correct=row();wrong=dict(correct,sequenceNumber="DL1")
        self.assertIs(self.lookup([wrong,correct]),correct)

    def test_wrong_section_term_subject_number_and_crn_are_rejected(self):
        for values in ({"sequenceNumber":"DL1"},{"term":"202810"},{"subject":"CS"},
                       {"courseNumber":"341"},{"courseReferenceNumber":"17593"}):
            with self.subTest(values=values),self.assertRaises(monitor.SearchError):self.lookup([dict(row(),**values)])

    def test_reference_reserved_only_counts_are_not_alertable(self):
        reference=monitor.snapshot(row())
        self.assertEqual(reference["seatsAvailable"],16)
        self.assertEqual(monitor.parse_unreserved(reference["reservedSeatSummary"]),0)
        self.assertEqual(monitor.alertable_open_seats(reference),0)
        self.assertFalse(monitor.possible_eligible(reference))

    def cycle(self,current,state):
        with (patch.object(monitor,"get_section",side_effect=lambda term,crn,number:current[crn]) as lookup,
              patch.object(monitor,"notify") as notify,patch.object(monitor,"save_state"),
              patch.object(monitor.time,"sleep"),contextlib.redirect_stdout(io.StringIO())):
            failures=monitor.check_once("202710",state)
        self.assertEqual([c.args for c in lookup.call_args_list],[("202710","17593","341"),("202710","21051","343")])
        return failures,notify.call_args_list

    def test_two_active_lookups_retired_cache_untouched_reserved_reference_silent(self):
        retired={"seatsAvailable":18,"reservedSeatSummary":"unknown"};state={"17582":dict(retired)}
        failures,alerts=self.cycle({"17593":{"seatsAvailable":0},"21051":row()},state)
        self.assertEqual((failures,alerts),(0,[]));self.assertEqual(state["17582"],retired)
        self.assertEqual(state["21051"]["sequenceNumber"],"DL3")

    def test_new_unreserved_opening_notifies_and_duplicate_is_silent(self):
        state={"21051":monitor.snapshot(row())};current={"17593":{"seatsAvailable":0},"21051":row(1,15)}
        failures,alerts=self.cycle(current,state)
        self.assertEqual((failures,len(alerts)),(0,1))
        self.assertIn("IT 343 DL3",alerts[0].args[0]);self.assertIn("CRN 21051",alerts[0].args[0])
        self.assertIn("0 -> 1",alerts[0].args[1])
        self.assertEqual(self.cycle(current,state),(0,[]))

    def test_reserved_only_increase_does_not_notify(self):
        state={"21051":monitor.snapshot(row(0,15))}
        self.assertEqual(self.cycle({"17593":{"seatsAvailable":0},"21051":row()},state),(0,[]))

    def test_it341_alert_behavior_is_preserved_with_new_reserved_only_target(self):
        failures,alerts=self.cycle({"17593":{"seatsAvailable":1},"21051":row()}, {})
        self.assertEqual((failures,len(alerts)),(0,1));self.assertIn("IT 341 DL1",alerts[0].args[0])

    def test_failure_of_one_target_keeps_the_other_observation_and_retired_cache(self):
        state={"17582":{"seatsAvailable":18}}
        def lookup(term,crn,number):
            if crn=="21051":raise monitor.SearchError("synthetic")
            return {"seatsAvailable":0}
        with (patch.object(monitor,"get_section",side_effect=lookup),patch.object(monitor,"notify") as notify,
              patch.object(monitor,"save_state"),patch.object(monitor.time,"sleep"),contextlib.redirect_stdout(io.StringIO())):
            self.assertEqual(monitor.check_once("202710",state),1)
        self.assertEqual(set(state),{"17582","17593"});notify.assert_not_called()

    def test_health_requires_both_active_results_and_ignores_retired(self):
        first="[synthetic] IT 341 DL1: 0 total seats remaining.\n"
        second="[synthetic] IT 343 DL3: 0 unreserved seats available; 16 total seats visible.\n"
        self.assertFalse(health_runner.summarize_output(first,0)[0])
        success,courses=health_runner.summarize_output(first+second,0)
        self.assertTrue(success);self.assertEqual([c["status"] for c in courses],["retired","success","success"])
        with tempfile.TemporaryDirectory() as tmp,contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(health_runner.run_once(runner=Mock(return_value=Mock(returncode=0,stdout=first+second,stderr="")),report_path=Path(tmp)/"health.json"),0)
            self.assertIn("2/2 active course checks",output.getvalue())

    def test_cache_allowlist_accepts_new_section_and_retains_old_courses(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"state.json";path.write_text(json.dumps({"17582":{"seatsAvailable":18},"17593":{"seatsAvailable":0},"21051":monitor.snapshot(row())}))
            self.assertTrue(health_runner.state_is_valid(path))

    def test_historical_payload_validation_keeps_dl3_and_original_retry_bytes(self):
        obs=CourseObservation("202710","IT","343","DL3","21051",16,{"unreservedAvailable":0,"reservedAvailable":16})
        self.assertEqual(obs.values()[-2:],("reserved_only",0))
        check=prepare_check(str(uuid.uuid4()),datetime(2026,11,26,tzinfo=timezone.utc),.25,[obs])
        before=check.body;validate_prepared(check);self.assertEqual(check.body,before)
        stored=json.loads(before)["observations"][0]
        self.assertEqual((stored["crn"],stored["section"],stored["reserved_summary"]["unreservedAvailable"]),("21051","DL3",0))

    def test_new_course_sqlite_timeline_preserves_both_old_histories_and_reservations(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=SeatHistory(Path(tmp)/"history.sqlite3");store.initialize();at=datetime(2026,11,26,tzinfo=timezone.utc)
            for i,crn,number in [(0,"17582","304"),(1,"17593","341")]:
                store.record_check("old-"+crn,at+timedelta(minutes=i),.25,[CourseObservation("202710","IT",number,"DL1",crn,0)])
            before={crn:store.history("202710",crn) for crn in ("17582","17593")}
            for i,count in enumerate((0,0,1)):
                store.record_check("new-"+str(i),at+timedelta(minutes=5+i*5),.25,[CourseObservation("202710","IT","343","DL3","21051",16,{"unreservedAvailable":count,"reservedAvailable":16-count})])
            self.assertEqual(len(store.opening_events("202710","21051")),1)
            self.assertEqual([o["section"] for o in store.history("202710","21051")],["DL3"]*3)
            for crn in before:self.assertEqual(store.history("202710",crn),before[crn])

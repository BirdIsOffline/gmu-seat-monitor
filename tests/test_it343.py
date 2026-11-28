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
    def setUp(self):
        legacy = {crn: c for crn, c in monitor.COURSES.items() if crn in ("17582", "17593", "21051")}
        self.enterContext(patch.object(monitor, "COURSES", legacy))
        self.enterContext(patch.object(health_runner, "COURSES", legacy))

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


SECTIONS = {"17599": "DL1", "20981": "DL2", "21051": "DL3"}


def section_row(crn, unreserved=0, reserved=16):
    return dict(row(unreserved, reserved), courseReferenceNumber=crn,
                sequenceNumber=SECTIONS[crn])


class IT343MultiSectionTests(unittest.TestCase):
    def setUp(self):
        original = {crn:c for crn,c in monitor.COURSES.items() if crn not in ("17596", "21146")}
        self.enterContext(patch.object(monitor,"COURSES",original))
        self.enterContext(patch.object(health_runner,"COURSES",original))

    def test_all_sections_are_active_and_have_distinct_exact_identities(self):
        self.assertEqual(list(monitor.active_courses()), ["17593", "17599", "20981", "21051"])
        self.assertEqual(monitor.COURSES["17582"]["status"], "retired")
        for crn, section in SECTIONS.items():
            with self.subTest(crn=crn):
                self.assertEqual(monitor.course_section(crn, "343"), section)
                self.assertEqual(monitor.COURSES[crn]["name"], "IT 343 " + section)
                with self.assertRaises(ValueError):
                    monitor.course_section(crn, "341")

    def test_mixed_catalog_results_require_both_exact_crn_and_section(self):
        for crn, section in SECTIONS.items():
            correct = section_row(crn)
            wrong_section = dict(correct, sequenceNumber="DL9")
            wrong_crn = dict(correct, courseReferenceNumber="99999")
            rows = [wrong_section, wrong_crn] + [section_row(c) for c in reversed(SECTIONS) if c != crn] + [correct]
            session = Mock(); manager = MagicMock(); manager.__enter__.return_value = session
            with self.subTest(crn=crn), patch.object(monitor, "new_session", return_value=manager):
                with patch.object(monitor, "_json_request", side_effect=[{"fwdURL":"public"}, {"success":True,"data":rows}]):
                    self.assertIs(monitor.get_section("202710", crn, "343"), correct)
                with patch.object(monitor, "_json_request", side_effect=[{"fwdURL":"public"}, {"success":True,"data":rows[:-1]}]):
                    with self.assertRaises(monitor.SearchError):
                        monitor.get_section("202710", crn, "343")

    def cycle(self, state, current):
        with (patch.object(monitor, "get_section", side_effect=lambda term,crn,number:current[crn]) as lookup,
              patch.object(monitor, "notify", return_value=True) as notify,
              patch.object(monitor, "save_state"), patch.object(monitor.time, "sleep"),
              contextlib.redirect_stdout(io.StringIO())):
            self.assertEqual(monitor.check_once("202710", state), 0)
        self.assertEqual([call.args for call in lookup.call_args_list],
                         [("202710",crn,c["number"]) for crn,c in monitor.active_courses().items()])
        return notify.call_args_list

    def test_independent_state_alerts_and_duplicate_suppression(self):
        retired = {"seatsAvailable":18}
        state = {"17582":dict(retired), "17593":{"seatsAvailable":0},
                 **{crn:monitor.snapshot(section_row(crn)) for crn in SECTIONS}}
        current = {"17593":{"seatsAvailable":0}, **{crn:section_row(crn,1,15) for crn in SECTIONS}}
        alerts = self.cycle(state, current)
        self.assertEqual(len(alerts), 3)
        for alert, (crn, section) in zip(alerts, SECTIONS.items()):
            self.assertIn(f"IT 343 {section} (CRN {crn})", alert.args[0])
            self.assertIn("0 -> 1", alert.args[1])
            self.assertEqual(alert.kwargs, {"actionable":True})
            self.assertEqual(state[crn]["sequenceNumber"], section)
        self.assertEqual(self.cycle(state, current), [])
        unchanged = {crn:dict(state[crn]) for crn in ("17599","21051")}
        current["20981"] = section_row("20981",2,14)
        alerts = self.cycle(state, current)
        self.assertEqual(len(alerts),1)
        self.assertIn("IT 343 DL2 (CRN 20981)",alerts[0].args[0])
        self.assertIn("1 -> 2",alerts[0].args[1])
        for crn in unchanged:self.assertEqual(state[crn],unchanged[crn])
        self.assertEqual(state["17582"],retired)

    def test_reserved_only_increases_across_three_sections_remain_silent(self):
        state = {crn:monitor.snapshot(section_row(crn,0,15)) for crn in SECTIONS}
        current = {"17593":{"seatsAvailable":0}, **{crn:section_row(crn,0,16) for crn in SECTIONS}}
        self.assertEqual(self.cycle(state,current), [])
        for crn in SECTIONS:
            self.assertEqual(monitor.alertable_open_seats(state[crn]),0)
            self.assertEqual(state[crn]["seatsAvailable"],16)

    def test_failed_dl1_does_not_lose_dl2_dl3_or_retired_state(self):
        state = {"17582":{"seatsAvailable":18}, "17599":monitor.snapshot(section_row("17599"))}
        before = dict(state["17599"])
        def lookup(term,crn,number):
            if crn=="17599":raise monitor.SearchError("synthetic")
            return section_row(crn) if crn in SECTIONS else {"seatsAvailable":0}
        with (patch.object(monitor,"get_section",side_effect=lookup),patch.object(monitor,"save_state"),
              patch.object(monitor,"notify") as notify,patch.object(monitor.time,"sleep"),contextlib.redirect_stdout(io.StringIO())):
            self.assertEqual(monitor.check_once("202710",state),1)
        self.assertEqual(state["17599"],before)
        self.assertEqual(state["20981"]["sequenceNumber"],"DL2")
        self.assertEqual(state["21051"]["sequenceNumber"],"DL3")
        self.assertEqual(state["17582"],{"seatsAvailable":18});notify.assert_not_called()

    def test_health_requires_each_of_the_four_active_results(self):
        lines = {crn:f"[synthetic] {c['name']}: 0 total seats remaining.\n" for crn,c in monitor.active_courses().items()}
        complete = "".join(lines.values())
        success,courses = health_runner.summarize_output(complete,0)
        self.assertTrue(success)
        self.assertEqual([c['status'] for c in courses],["retired"]+["success"]*4)
        for crn in lines:
            with self.subTest(crn=crn):
                self.assertFalse(health_runner.summarize_output(complete.replace(lines[crn],""),0)[0])
        with tempfile.TemporaryDirectory() as tmp,contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(health_runner.run_once(runner=Mock(return_value=Mock(returncode=0,stdout=complete,stderr="")),report_path=Path(tmp)/"health.json"),0)
            self.assertIn("4/4 active course checks",output.getvalue())
            state_path=Path(tmp)/"state.json"
            state_path.write_text(json.dumps({"17582":{"seatsAvailable":18},"17593":{"seatsAvailable":0},
                                             **{crn:monitor.snapshot(section_row(crn)) for crn in SECTIONS}}))
            self.assertTrue(health_runner.state_is_valid(state_path))

    def test_payload_validation_keeps_three_same_number_sections_and_retry_bytes(self):
        observations = [CourseObservation("202710","IT","343",section,crn,16,{"unreservedAvailable":0}) for crn,section in SECTIONS.items()]
        prepared = prepare_check(str(uuid.uuid4()),datetime(2026,11,26,tzinfo=timezone.utc),.25,observations)
        before=prepared.body;validate_prepared(prepared);self.assertEqual(prepared.body,before)
        self.assertEqual([(o["crn"],o["section"]) for o in json.loads(before)["observations"]],list(SECTIONS.items()))
        with self.assertRaises(ValueError):
            prepare_check(str(uuid.uuid4()),datetime(2026,11,26,tzinfo=timezone.utc),.25,observations+[observations[0]])

    def test_sqlite_same_number_histories_and_openings_remain_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=SeatHistory(Path(tmp)/"history.sqlite3");store.initialize();at=datetime(2026,11,26,tzinfo=timezone.utc)
            store.record_check("old",at,.25,[CourseObservation("202710","IT","304","DL1","17582",0),CourseObservation("202710","IT","341","DL1","17593",0)])
            before={crn:store.history("202710",crn) for crn in ("17582","17593")}
            for minute,counts in [(5,(0,0,0)),(10,(1,0,0)),(15,(1,2,0))]:
                store.record_check("three-"+str(minute),at+timedelta(minutes=minute),.25,
                    [CourseObservation("202710","IT","343",section,crn,16,{"unreservedAvailable":count}) for (crn,section),count in zip(SECTIONS.items(),counts)])
            for crn,section in SECTIONS.items():
                self.assertEqual([o["section"] for o in store.history("202710",crn)],[section]*3)
                self.assertEqual(len(store.opening_events("202710",crn)),0 if crn=="21051" else 1)
            for crn in before:self.assertEqual(store.history("202710",crn),before[crn])

"""End-to-end synthetic course-to-Bark contract tests.

No university request, phone push, personal account, or enrollment action occurs.
Run: python -m unittest discover -s tests -v
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import monitor


def public_row(crn, number, unreserved, reserved, section="DL1"):
    return {
        "courseReferenceNumber": crn,
        "term": "202710",
        "sequenceNumber": section,
        "courseTitle": f"IT {number}",
        "seatsAvailable": unreserved + reserved,
        "reservedSeatSummary": (
            f"{unreserved} of 10 unreserved; {reserved} of 30 reserved"
        ),
    }


class SyntheticNotificationPipelineTests(unittest.TestCase):
    def setUp(self):
        both_active = {crn: dict(course, status="active") for crn, course in monitor.COURSES.items() if crn in ("17582", "17593")}
        self.enterContext(patch.object(monitor, "COURSES", both_active))

    def test_one_candidate_opening_reaches_mock_bark_and_reserved_only_change_is_silent(self):
        """Run real parsing, decisions, state persistence and notify() with mocked I/O."""
        with tempfile.TemporaryDirectory() as folder:
            state_path = Path(folder) / "seat_state.json"
            previous = {
                "17582": monitor.snapshot(public_row("17582", "304", 0, 0)),
                "17593": monitor.snapshot(public_row("17593", "341", 0, 2)),
            }
            state_path.write_text(json.dumps(previous), encoding="utf-8")

            current = {
                "17582": public_row("17582", "304", 1, 0),
                "17593": public_row("17593", "341", 0, 3),
            }
            response = Mock()
            response.json.return_value = {"code": 200}
            response.raise_for_status.return_value = None

            def mocked_lookup(term_code, crn, course_number):
                self.assertEqual(term_code, "202710")
                self.assertEqual(course_number, "304" if crn == "17582" else "341")
                return current[crn]

            with (
                patch.object(monitor, "STATE_PATH", state_path),
                patch.object(monitor, "get_section", side_effect=mocked_lookup) as lookup,
                patch.object(monitor, "read_bark_key", return_value="SYNTHETIC_DEMO_ONLY_KEY"),
                patch.object(monitor.requests, "post", return_value=response) as bark_request,
                patch.object(monitor.time, "sleep"),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                state = monitor.load_state()
                self.assertEqual(monitor.check_once("202710", state), 0)

                # A single real decision-to-notify path; no reserved-only push.
                self.assertEqual(lookup.call_count, 2)
                bark_request.assert_called_once()
                args, kwargs = bark_request.call_args
                self.assertEqual(args[0], "https://api.day.app/push")
                self.assertEqual(kwargs["timeout"], 15)
                push = kwargs["json"]
                self.assertEqual(push["device_key"], "SYNTHETIC_DEMO_ONLY_KEY")
                self.assertEqual({k: push[k] for k in ("sound", "call", "level", "volume")},
                                 {"sound": "alarm", "call": "1", "level": "critical", "volume": "10"})
                self.assertIn("IT 304 DL1", push["title"])
                self.assertIn("CRN 17582", push["title"])
                self.assertIn("0 -> 1", push["body"])
                self.assertNotIn("IT 341 DL1", push["title"])
                self.assertEqual(json.loads(state_path.read_text()), state)
                self.assertEqual(state["17582"]["seatsAvailable"], 1)
                self.assertEqual(state["17593"]["seatsAvailable"], 3)

                # The same two public snapshots must not cause a duplicate alert.
                self.assertEqual(monitor.check_once("202710", state), 0)
                self.assertEqual(bark_request.call_count, 1)
                self.assertEqual(json.loads(state_path.read_text()), state)

    def test_unknown_reservation_details_do_not_create_false_opening(self):
        with tempfile.TemporaryDirectory() as folder:
            state_path = Path(folder) / "seat_state.json"
            state_path.write_text(json.dumps({
                "17593": monitor.snapshot(public_row("17593", "341", 0, 2)),
            }), encoding="utf-8")
            current = public_row("17593", "341", 0, 3)
            current["reservedSeatSummary"] = "restricted eligibility (not parsed)"

            with (
                patch.object(monitor, "COURSES", {
                    "17593": {"name": "IT 341 DL1", "number": "341", "section": "DL1", "status": "active"},
                }),
                patch.object(monitor, "STATE_PATH", state_path),
                patch.object(monitor, "get_section", return_value=current),
                patch.object(monitor.requests, "post") as bark_request,
                patch.object(monitor.time, "sleep"),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                state = monitor.load_state()
                self.assertEqual(monitor.check_once("202710", state), 0)
                bark_request.assert_not_called()
                self.assertEqual(state["17593"]["reservedSeatSummary"], current["reservedSeatSummary"])


class ActionableBarkParametersTests(unittest.TestCase):
    def setUp(self):
        # Mock the local beep as well as HTTP; no device receives a notification.
        self.enterContext(patch.dict(sys.modules, {"winsound": Mock()}))

    def test_all_five_active_courses_send_exact_critical_parameters_and_unchanged_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path=Path(tmp)/"seat_state.json"
            state={"17582":{"seatsAvailable":18}}
            for crn,c in monitor.active_courses().items():
                state[crn]=monitor.snapshot(public_row(crn,c["number"],0,16,c["section"]))
            current={crn:public_row(crn,c["number"],1,15,c["section"]) for crn,c in monitor.active_courses().items()}
            reply=Mock();reply.json.return_value={"code":200}
            with (patch.object(monitor,"STATE_PATH",state_path),
                  patch.object(monitor,"get_section",side_effect=lambda term,crn,number:current[crn]),
                  patch.object(monitor,"read_bark_key",return_value="SYNTHETIC_DEMO_ONLY_KEY"),
                  patch.object(monitor.requests,"post",return_value=reply) as send,
                  patch.object(monitor.time,"sleep"),contextlib.redirect_stdout(io.StringIO())):
                self.assertEqual(monitor.check_once("202710",state),0)
                self.assertEqual(send.call_count,5)
                for call,(crn,c) in zip(send.call_args_list,monitor.active_courses().items()):
                    self.assertEqual(call.args,("https://api.day.app/push",))
                    self.assertEqual(call.kwargs["timeout"],15)
                    payload=call.kwargs["json"]
                    self.assertEqual({k:payload[k] for k in ("sound","call","level","volume")},
                                     {"sound":"alarm","call":"1","level":"critical","volume":"10"})
                    self.assertEqual(payload["title"],f"Possible seat opening: {c['name']} (CRN {crn})")
                    self.assertEqual(payload["body"],
                        f"{c['name']}: 1 unreserved seats available; 16 total seats visible.\n"
                        "Potentially available seats: 0 -> 1.\n"
                        "Check Patriot Web immediately. Seats may still have restrictions. "
                        "Do not drop your current registered section without a safe swap."
                        "\n\nCheck GMU in Patriot Web before changing any registration.")
                    self.assertEqual(payload["url"],monitor.PUBLIC_COURSE_SEARCH)
                    self.assertEqual(payload["group"],"GMU Classes")
                # Unchanged snapshots and then decreasing availability remain silent.
                self.assertEqual(monitor.check_once("202710",state),0)
                for crn,c in monitor.active_courses().items():current[crn]=public_row(crn,c["number"],0,16,c["section"])
                self.assertEqual(monitor.check_once("202710",state),0)
                self.assertEqual(send.call_count,5)
            self.assertEqual(state["17582"],{"seatsAvailable":18})
            self.assertEqual(json.loads(state_path.read_text()),state)

    def test_all_active_reserved_only_increases_never_send_a_critical_alert(self):
        state={crn:monitor.snapshot(public_row(crn,c["number"],0,15,c["section"])) for crn,c in monitor.active_courses().items()}
        with (patch.object(monitor,"get_section",side_effect=lambda term,crn,number:public_row(crn,number,0,16,monitor.COURSES[crn]["section"])),
              patch.object(monitor,"save_state"),patch.object(monitor,"read_bark_key") as key,
              patch.object(monitor.requests,"post") as send,patch.object(monitor.time,"sleep"),
              contextlib.redirect_stdout(io.StringIO())):
            self.assertEqual(monitor.check_once("202710",state),0)
        key.assert_not_called();send.assert_not_called()
        self.assertEqual(monitor.COURSES["17582"]["status"],"retired")

    def test_non_actionable_test_notification_retains_existing_delivery_defaults(self):
        reply=Mock();reply.json.return_value={"code":200}
        with (patch.object(monitor,"read_bark_key",return_value="SYNTHETIC_DEMO_ONLY_KEY"),
              patch.object(monitor.requests,"post",return_value=reply) as send,
              contextlib.redirect_stdout(io.StringIO())):
            self.assertTrue(monitor.notify("Demo title","Demo body"))
        payload=send.call_args.kwargs["json"]
        self.assertEqual(payload["level"],"timeSensitive")
        for key in ("sound","call","volume"):self.assertNotIn(key,payload)

    def test_actionable_delivery_errors_still_return_false_without_retry(self):
        for mode in ("timeout","http","json","rejected","unexpected"):
            with self.subTest(mode=mode):
                reply=Mock();reply.json.return_value={"code":200}
                error=None
                if mode=="timeout":error=monitor.requests.Timeout("synthetic timeout")
                if mode=="http":reply.raise_for_status.side_effect=monitor.requests.HTTPError("synthetic HTTP error")
                if mode=="json":reply.json.side_effect=ValueError("synthetic JSON error")
                if mode=="rejected":reply.json.return_value={"code":400}
                if mode=="unexpected":reply.json.return_value=None
                with (patch.object(monitor,"read_bark_key",return_value="SYNTHETIC_DEMO_ONLY_KEY"),
                      patch.object(monitor.requests,"post",return_value=reply,side_effect=error) as send,
                      contextlib.redirect_stdout(io.StringIO())):
                    self.assertFalse(monitor.notify("Demo title","Demo body",actionable=True))
                send.assert_called_once()
                self.assertEqual(send.call_args.kwargs["json"]["level"],"critical")

    def test_missing_key_still_does_not_attempt_delivery(self):
        with (patch.object(monitor,"read_bark_key",return_value=None),
              patch.object(monitor.requests,"post") as send,contextlib.redirect_stdout(io.StringIO())):
            self.assertFalse(monitor.notify("Demo title","Demo body",actionable=True))
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()

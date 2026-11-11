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


def public_row(crn, number, unreserved, reserved):
    return {
        "courseReferenceNumber": crn,
        "term": "202710",
        "sequenceNumber": "DL1",
        "courseTitle": f"IT {number}",
        "seatsAvailable": unreserved + reserved,
        "reservedSeatSummary": (
            f"{unreserved} of 10 unreserved; {reserved} of 30 reserved"
        ),
    }


class SyntheticNotificationPipelineTests(unittest.TestCase):
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
                self.assertEqual(push["level"], "timeSensitive")
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
                    "17593": {"name": "IT 341 DL1", "number": "341"},
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


if __name__ == "__main__":
    unittest.main()

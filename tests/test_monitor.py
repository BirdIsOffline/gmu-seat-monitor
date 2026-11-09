"""Offline tests for the GMU course seat monitor.

Run: python -m unittest discover -s tests -v
All network/notification integrations are mocked: no GMU polling, no Bark push.
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


def seat_row(unreserved=0, reserved=0, total=None, **changes):
    if total is None:
        total = unreserved + reserved
    row = {
        "courseReferenceNumber": "17582",
        "term": "202710",
        "sequenceNumber": "DL1",
        "courseTitle": "IT in the Global Economy",
        "seatsAvailable": total,
        "reservedSeatSummary": (
            f"{unreserved} of 10 unreserved; {reserved} of 30 reserved"
        ),
    }
    row.update(changes)
    return row


class SeatParsingTests(unittest.TestCase):
    def test_all_seats_reserved_are_not_alertable(self):
        self.assertEqual(monitor.alertable_open_seats(seat_row(0, 9)), 0)

    def test_one_unreserved_seat_is_alertable(self):
        self.assertEqual(monitor.alertable_open_seats(seat_row(1, 9)), 1)

    def test_multiple_unreserved_seats(self):
        self.assertEqual(monitor.alertable_open_seats(seat_row(3, 4)), 3)

    def test_no_total_availability(self):
        self.assertEqual(monitor.alertable_open_seats(seat_row(0, 0)), 0)

    def test_unreserved_count_is_capped_by_total(self):
        self.assertEqual(monitor.alertable_open_seats(seat_row(7, 0, total=2)), 2)

    def test_missing_reservation_summary_is_possible_opening(self):
        self.assertEqual(monitor.alertable_open_seats({"seatsAvailable": 2, "reservedSeatSummary": None}), 2)

    def test_unknown_reservation_format_is_not_counted(self):
        self.assertEqual(monitor.alertable_open_seats({"seatsAvailable": 9, "reservedSeatSummary": "restricted"}), 0)

    def test_invalid_total_is_ignored(self):
        for value in (None, -1, "2", True):
            with self.subTest(value=value):
                self.assertEqual(monitor.alertable_open_seats({"seatsAvailable": value}), 0)

    def test_alternate_unreserved_text_formats(self):
        for summary in ("unreserved seats remaining: 2", "unreserved: 2 of 10"):
            with self.subTest(summary=summary):
                self.assertEqual(monitor.parse_unreserved(summary), 2)

    def test_dictionary_unreserved_details(self):
        summary = {"unreservedSeatsRemaining": 3, "reservedSeatsRemaining": 6}
        self.assertEqual(monitor.parse_unreserved(summary), 3)

    def test_reserved_only_dictionary_not_mistaken_for_open(self):
        self.assertIsNone(monitor.parse_unreserved({"reservedAvailable": 9}))

    def test_snapshot_omits_student_information(self):
        row = seat_row(1, 2, faculty=[{"emailAddress": "private@example.com"}], studentId="secret")
        result = monitor.snapshot(row)
        self.assertNotIn("faculty", result)
        self.assertNotIn("studentId", result)
        self.assertEqual(result["seatsAvailable"], 3)


class NotificationDecisionTests(unittest.TestCase):
    """Exercise the real check_once function while mocking network + messaging."""

    def check_change(self, previous, current):
        state = {} if previous is None else {"17582": previous}
        with (patch.object(monitor, "COURSES", {"17582": {"name": "IT 304 DL1", "number": "304"}}),
              patch.object(monitor, "get_section", return_value=current),
              patch.object(monitor, "notify", return_value=True) as notifier,
              patch.object(monitor, "save_state"),
              patch.object(monitor.time, "sleep"),
              contextlib.redirect_stdout(io.StringIO())):
            failures = monitor.check_once("202710", state)
        self.assertEqual(failures, 0)
        self.assertEqual(state["17582"]["seatsAvailable"], current["seatsAvailable"])
        return notifier.call_args_list

    def test_zero_to_one_unreserved_sends_alert(self):
        calls = self.check_change(seat_row(0, 0), seat_row(1, 0))
        self.assertEqual(len(calls), 1)
        self.assertIn("0 -> 1", calls[0].args[1])

    def test_one_to_two_unreserved_sends_alert(self):
        self.assertEqual(len(self.check_change(seat_row(1, 0), seat_row(2, 0))), 1)

    def test_reserved_increase_does_not_alert(self):
        self.assertEqual(len(self.check_change(seat_row(0, 9), seat_row(0, 10))), 0)

    def test_reserved_decrease_does_not_alert(self):
        self.assertEqual(len(self.check_change(seat_row(0, 9), seat_row(0, 8))), 0)

    def test_unreserved_decrease_does_not_alert(self):
        self.assertEqual(len(self.check_change(seat_row(2, 0), seat_row(1, 0))), 0)

    def test_seat_closes_does_not_alert(self):
        self.assertEqual(len(self.check_change(seat_row(1, 0), seat_row(0, 0))), 0)

    def test_no_change_does_not_alert(self):
        self.assertEqual(len(self.check_change(seat_row(0, 0), seat_row(0, 0))), 0)

    def test_professor_only_change_does_not_alert(self):
        before = seat_row(0, 9, instructor="A")
        after = seat_row(0, 9, instructor="B")
        self.assertEqual(len(self.check_change(before, after)), 0)

    def test_reserved_seats_become_unreserved_alerts(self):
        self.assertEqual(len(self.check_change(seat_row(0, 9), seat_row(2, 7))), 1)

    def test_first_observation_with_open_seat_alerts(self):
        self.assertEqual(len(self.check_change(None, seat_row(1, 0))), 1)

    def test_first_observation_with_only_reserved_seats_silent(self):
        self.assertEqual(len(self.check_change(None, seat_row(0, 9))), 0)

    def test_request_error_does_not_send_alert(self):
        state = {"17582": seat_row(0, 9)}
        with (patch.object(monitor, "COURSES", {"17582": {"name": "IT 304 DL1", "number": "304"}}),
              patch.object(monitor, "get_section", side_effect=monitor.SearchError("sample failure")),
              patch.object(monitor, "notify") as notify,
              contextlib.redirect_stdout(io.StringIO())):
            failures = monitor.check_once("202710", state)
        self.assertEqual(failures, 1)
        notify.assert_not_called()
        self.assertEqual(state["17582"]["seatsAvailable"], 9)


class IntegrationBoundaryTests(unittest.TestCase):
    def test_saved_state_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "seat_state.json"
            with patch.object(monitor, "STATE_PATH", state_file):
                state = {"17582": seat_row(1, 0)}
                monitor.save_state(state)
                self.assertEqual(monitor.load_state(), state)
                self.assertFalse(state_file.with_suffix(".tmp").exists())

    def test_corrupted_state_gracefully_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "seat_state.json"
            state_file.write_text("not-json", encoding="utf-8")
            with patch.object(monitor, "STATE_PATH", state_file), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(monitor.load_state(), {})

    def test_gmu_search_is_exact_crn_and_section(self):
        class FakeResponse:
            def raise_for_status(self):
                return None
        session = Mock()
        session.get.return_value = FakeResponse()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        wrong = dict(seat_row(0, 0), courseNumber="304", subject="IT", sequenceNumber="004")
        correct = dict(seat_row(1, 0), courseNumber="304", subject="IT")
        payload = {"success": True, "data": [wrong, correct]}
        with patch.object(monitor, "new_session", return_value=session), patch.object(
            monitor, "_json_request", side_effect=[{"fwdURL": "ok"}, payload]
        ):
            found = monitor.get_section("202710", "17582", "304")
        self.assertEqual(found["sequenceNumber"], "DL1")
        self.assertEqual(found["seatsAvailable"], 1)

    def test_bark_key_from_environment(self):
        with patch.dict(monitor.os.environ, {"BARK_DEVICE_KEY": "EXAMPLE_ONLY_123456789"}):
            self.assertEqual(monitor.read_bark_key(), "EXAMPLE_ONLY_123456789")

    def test_bark_network_request_is_mocked(self):
        response = Mock()
        response.json.return_value = {"code": 200}
        with (patch.object(monitor, "read_bark_key", return_value="EXAMPLE_ONLY_123456789"),
              patch.object(monitor.requests, "post", return_value=response) as send,
              contextlib.redirect_stdout(io.StringIO())):
            delivered = monitor.notify("Demo title", "Demo body")
        self.assertTrue(delivered)
        data = send.call_args.kwargs["json"]
        self.assertEqual(data["device_key"], "EXAMPLE_ONLY_123456789")
        self.assertEqual(data["level"], "timeSensitive")
        self.assertIn("Demo body", data["body"])


if __name__ == "__main__":
    unittest.main()

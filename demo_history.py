"""Synthetic, temporary SQLite demonstration; no GMU or Bark calls."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import json

from seat_history import CourseObservation, SeatHistory


def demo():
    with TemporaryDirectory(prefix="seat-history-demo-") as directory:
        history = SeatHistory(Path(directory) / "synthetic.sqlite3")
        history.initialize()
        print("OFFLINE SYNTHETIC DEMO: temporary database created; no network calls")
        start = datetime(2026, 11, 9, tzinfo=timezone.utc)
        # IT 304: unreserved 0 -> 1; IT 341: reserved-only 9 -> 10.
        for index, counts in enumerate(((0, 0, 9), (1, 1, 10), (1, 1, 10))):
            total, unreserved, reserved = counts
            observations = [
                CourseObservation("202710", "IT", "304", "DL1", "17582", total,
                                  f"{unreserved} of 10 unreserved; 0 of 30 reserved"),
                CourseObservation("202710", "IT", "341", "DL1", "17593", reserved,
                                  f"0 of 10 unreserved; {reserved} of 30 reserved"),
            ]
            history.record_check(f"synthetic-{index}", start + timedelta(minutes=5 * index),
                                 0.25, observations)
        history.record_failure("synthetic-failure", start + timedelta(minutes=15),
                               20, "LOOKUP_TIMEOUT")
        print("Statistics:", json.dumps(history.statistics(), indent=2))
        for crn in ("17582", "17593"):
            print(f"CRN {crn} historical potential counts:",
                  [row["potential_available"] for row in history.history("202710", crn)])
            print("Latest availability:", json.dumps(history.latest("202710", crn)))
            print("Observed opening events:", json.dumps(history.opening_events("202710", crn)))
            print("Frequency by UTC detection day:", json.dumps(history.openings_by_day("202710", crn)))
        print("IT 304: unreserved 0 -> 1 detected. IT 341: reserved-only 9 -> 10 excluded.")
        print("Detection timestamps are sampled observations, not exact seat-opening times.")
    print("Temporary synthetic database removed.")


if __name__ == "__main__":
    demo()

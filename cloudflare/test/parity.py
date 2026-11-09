"""Synthetic Python SQLite reference + real localhost Worker client roundtrip."""

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cloud_history import CloudHistoryClient, prepare_check
from seat_history import CourseObservation, SeatHistory


def main(endpoint):
    assert urlsplit(endpoint).hostname == "127.0.0.1"
    client = CloudHistoryClient(endpoint, "synthetic_local_only_token_00000000000000000000000",
                                allow_local_http=True, max_attempts=1)
    try:
        with TemporaryDirectory(prefix="d1-parity-") as directory:
            history = SeatHistory(Path(directory) / "reference.sqlite3")
            history.initialize()
            start = datetime(2026, 11, 9, tzinfo=timezone.utc)
            cases = [(None, None), (1, None), (0, "0 of 10 unreserved"),
                     (1, "1 of 10 unreserved"), (1, "1 of 10 unreserved"),
                     (9, "0 of 10 unreserved; 9 of 30 reserved"),
                     (10, "0 of 10 unreserved; 10 of 30 reserved"),
                     (10, {"unreservedAvailable": 2, "reservedAvailable": 8}),
                     (None, {"unreservedAvailable": 0}), (5, None),
                     (5, "eligibility unknown"), (5, "unreserved seats remaining: 2"),
                     (0, None), (2, {}), (0, None), (1, ""),
                     (9, {"reservedAvailable": 9}), (2, {"unreservedRemaining": 20}),
                     (0, {"unreservedAvailable": 1, "unreservedRemaining": 1})]
            for i, (total, summary) in enumerate(cases):
                check_id = str(uuid.uuid4())
                timestamp = start + timedelta(minutes=i)
                observations = [CourseObservation("202710", "IT", "304", "DL1", "17582", total, summary)]
                kwargs = {"status": "failed", "error_code": "COURSE_LOOKUP_FAILED"} if i == 10 else {}
                prepared = prepare_check(check_id, timestamp, 0.25, observations, **kwargs)
                history.record_check(check_id, timestamp, 0.25, observations, **kwargs)
                result = client.submit(prepared)
                assert result.success, result
                assert client.submit(prepared).success  # Same immutable body, no new GMU work.
            check_id = str(uuid.uuid4())
            timestamp = start + timedelta(hours=1)
            history.record_failure(check_id, timestamp, 20, "TIMEOUT")
            assert client.submit(prepare_check(check_id, timestamp, 20, status="failed", error_code="TIMEOUT")).success
            fields = ("total_available", "unreserved_available", "classification", "potential_available")
            event_fields = ("previous_available", "new_available", "classification")
            print(json.dumps({"observations": [{k: r[k] for k in fields} for r in history.history("202710", "17582")],
                              "events": [{k: r[k] for k in event_fields} for r in history.opening_events("202710", "17582")],
                              "checks": history.statistics()["completed_checks"]}))
    finally:
        client.close()


if __name__ == "__main__":
    main(sys.argv[1])

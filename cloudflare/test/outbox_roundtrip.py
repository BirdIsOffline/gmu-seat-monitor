"""Local D1 outbox crash/restart probe. Node supplies an ephemeral loopback URL."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cloud_history import CloudHistoryClient, PreparedCheck, prepare_check
from history_outbox import HistoryOutbox
from seat_history import CourseObservation


def main(endpoint, path, mode):
    assert urlsplit(endpoint).hostname == "127.0.0.1"
    box = HistoryOutbox(path)
    prepared = [prepare_check(
        f"00000000-0000-4000-8000-{i + 1:012d}",
        datetime(2026, 11, 9, tzinfo=timezone.utc) + timedelta(minutes=i * 5), .25,
        [CourseObservation("202710", "IT", "304", "DL1", "17582", i, {"unreservedAvailable": i})],
    ) for i in range(2)]
    client = CloudHistoryClient(endpoint, "synthetic_local_only_token_00000000000000000000000",
                                allow_local_http=True, max_attempts=1)
    try:
        if mode == "interrupt":
            box.initialize()
            for p in reversed(prepared):
                box.enqueue(p)
            class InterruptAfterCommit:
                def submit(self, snapshot):
                    assert client.submit(snapshot).success
                    os._exit(23)  # D1 committed; local delivery receipt never saved.
            box.drain(InterruptAfterCommit())
            raise AssertionError("Expected abrupt process exit")
        if mode == "replay":
            # A restored D1 may be missing rows whose local receipts say delivered.
            # Copy the retained originals into a NEW queue, without changing them.
            original_records = box.records(include_delivered=True)
            assert all(r["delivered"] for r in original_records)
            replay = HistoryOutbox(str(path) + ".replay")
            replay.initialize()
            with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True) as db:
                for check_id, body in db.execute("SELECT check_id, body FROM submissions ORDER BY stamp"):
                    replay.enqueue(PreparedCheck(check_id, body))
            assert replay.drain(client) == {"attempted": 2, "delivered": 2}
            assert box.records(include_delivered=True) == original_records
            print(json.dumps({"replayed": 2}))
            return
        assert mode == "recover"
        before = box.records(include_delivered=True)
        assert [r["attempts"] for r in before] == [1, 0]
        assert before[0]["last_code"] == "ATTEMPT_STARTED"
        assert box.drain(client, clock=lambda: time.time() + 7200) == {"attempted": 2, "delivered": 2}
        assert not box.records()
        records = box.records(include_delivered=True)
        assert [r["attempts"] for r in records] == [2, 1]
        with sqlite3.connect(path) as db:
            bodies = db.execute("SELECT body, body_hash FROM submissions ORDER BY stamp").fetchall()
        for p, (body, digest) in zip(prepared, bodies, strict=True):
            assert body == p.body
            assert digest == hashlib.sha256(p.body).hexdigest()
        print(json.dumps({"delivered": len(records), "attempts": [r["attempts"] for r in records]}))
    finally:
        client.close()


if __name__ == "__main__":
    main(*sys.argv[1:])

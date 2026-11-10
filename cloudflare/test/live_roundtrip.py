"""Original mocked lookups -> structured handoff -> subprocess -> local D1 only."""

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from history_capture import CheckCapture, read_prepared
from history_submit import submit_with_deadline
import monitor


def main(endpoint):
    assert urlsplit(endpoint).hostname == "127.0.0.1"
    with tempfile.TemporaryDirectory() as tmp:
        capture = CheckCapture(tmp)
        with (patch.object(monitor, "get_section", side_effect=[
                {"seatsAvailable": 1, "reservedSeatSummary": "1 of 10 unreserved; PRIVATE_TEXT"},
                monitor.SearchError("synthetic failure")]) as fetch,
              patch.object(monitor, "notify", return_value=True) as bark,
              patch.object(monitor, "save_state"), patch.object(monitor.time, "sleep"),
              patch("requests.sessions.Session.request", side_effect=AssertionError("external HTTP forbidden")),
              contextlib.redirect_stdout(io.StringIO())):
            failures = monitor.check_once("202710", {}, capture)
            capture.finish(failures)
            original = read_prepared(capture.directory, "pending.json")
            with patch.dict(os.environ, {"GMU_HISTORY_ENABLED": "true",
                  "GMU_HISTORY_PRODUCTION_ENDPOINT": endpoint,
                  "GMU_HISTORY_PRODUCTION_TOKEN": "synthetic_local_only_token_00000000000000000000000"}, clear=True):
                assert submit_with_deadline(tmp, 5, allow_local_http=True)["outcome"] == "success"
                # Simulate losing the local receipt after a committed D1 ACK.
                (capture.directory / "receipt.sha256").unlink()
                assert submit_with_deadline(tmp, 5, allow_local_http=True)["outcome"] == "success"
            assert fetch.call_count == 2 and bark.call_count == 1
            assert read_prepared(capture.directory) == original
            assert b"PRIVATE_TEXT" not in original.body
        print(json.dumps({"check_id": capture.check_id, "lookups": 2, "bark_calls": 1}))


if __name__ == "__main__":
    main(*sys.argv[1:])

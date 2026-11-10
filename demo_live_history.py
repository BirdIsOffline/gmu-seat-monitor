"""Synthetic structured handoff demo. No external network, credentials or Bark."""

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from cloud_history import CloudHistoryClient, IngestionResult
from history_capture import CheckCapture, read_prepared
from history_submit import _worker
import monitor


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "history-handoff"
        capture = CheckCapture(root)
        with (patch.object(monitor, "get_section", side_effect=[
                {"seatsAvailable": 0, "reservedSeatSummary": "0 of 10 unreserved"},
                monitor.SearchError("synthetic lookup failure")]) as fetch,
              patch.object(monitor, "notify") as bark,
              patch.object(monitor, "save_state"), patch.object(monitor.time, "sleep"),
              patch("requests.sessions.Session.request", side_effect=AssertionError("network forbidden")),
              contextlib.redirect_stdout(io.StringIO())):
            failures = monitor.check_once("202710", {}, capture)
            capture.finish(failures)
            original = read_prepared(capture.directory, "pending.json")
            retained = []
            def synthetic_submit(client, prepared):
                retained.append(prepared)
                return IngestionResult(len(retained) > 1, "TIMEOUT" if len(retained) == 1 else "INGESTED", 1)
            with (patch.dict(os.environ, {
                    "GMU_HISTORY_PRODUCTION_ENDPOINT": "https://history.invalid/v1/checks",
                    "GMU_HISTORY_PRODUCTION_TOKEN": "synthetic_local_only_token_00000000000000000000000",
                  }, clear=True), patch.object(CloudHistoryClient, "submit", synthetic_submit)):
                first = _worker(root)
                recovered = _worker(root)
            assert first["outcome"] == "unconfirmed" and recovered["outcome"] == "success"
            assert retained == [original, original]
            assert fetch.call_count == 2 and bark.call_count == 0
        body = json.loads(original.body)
        print("OFFLINE STRUCTURED HANDOFF DEMO: synthetic facts + mocked HTTP only")
        print(json.dumps({"original_lookups": 2, "completed_observations": len(body["observations"]),
                          "check_status": body["status"], "first_delivery": first["outcome"],
                          "retry_delivery": recovered["outcome"], "identical_retry_body": True,
                          "bark_calls": 0}, indent=2))
        print("Temporary handoff removed. Ephemeral-runner loss is still possible before D1 commit.")


if __name__ == "__main__":
    main()

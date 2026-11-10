"""Opt-in, local structured handoff from completed monitor lookups.

No HTTP, notification, state-cache reads, credentials, or raw-response storage.
Requires a local POSIX filesystem. This is NOT durable storage on an Actions runner.
"""

from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import time
import uuid

from cloud_history import MAX_BYTES, PreparedCheck, prepare_check, validate_prepared
from monitor import parse_unreserved
from seat_history import CourseObservation


def atomic_write(path, body):
    """Publish complete bytes only; callers hold the check's exclusive lock."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("wb") as stream:
            os.chmod(temporary, 0o600)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_prepared(directory, name="check.json"):
    with (directory / name).open("rb") as stream:
        body = stream.read(MAX_BYTES + 1)
    prepared = PreparedCheck(directory.name, body)
    validate_prepared(prepared)
    return prepared


def public_observation(term, crn, number, current):
    """Reduce free text/dicts immediately to bounded public counts or unknown.

    Course identity comes from the lookup arguments already checked by get_section.
    Missing/invalid totals stay unknown, never become invented zero-seat samples.
    """
    total = current.get("seatsAvailable")
    if type(total) is not int or not 0 <= total <= 1_000_000:
        total = None
    summary = current.get("reservedSeatSummary")
    if summary in (None, "", [], {}):
        reserved = None
    else:
        # Only strings or recognized count keys can contribute a public count.
        if isinstance(summary, dict):
            allowed = {"unreservedAvailable", "unreservedRemaining",
                       "unreservedSeatsAvailable", "unreservedSeatsRemaining"}
            counts = [v for k, v in summary.items() if k in allowed
                      and type(v) is int and 0 <= v <= 1_000_000]
            count = counts[0] if counts and len(set(counts)) == 1 else None
        elif isinstance(summary, str) and len(summary) <= 2000:
            count = parse_unreserved(summary)
        else:
            count = None
        reserved = ({"unreservedAvailable": count}
                    if type(count) is int and 0 <= count <= 1_000_000 else "unknown")
    return CourseObservation(term, "IT", number, "DL1", crn, total, reserved)


class CheckCapture:
    """One UUID/time per original cycle; pending bytes are NEVER submitted.

    A nonblocking lock prevents consumers from sealing an active producer.
    Every checkpoint is a valid failed check, safe to seal after interruption.
    Once sealed, its status, duration, observations and identity never change.
    """

    def __init__(self, root):
        self.check_id = str(uuid.uuid4())
        self.sampled_at = datetime.now(timezone.utc)
        self.started = time.monotonic()
        self.observations = []
        self.preparation_failed = False
        self.directory = Path(root) / self.check_id
        self.lock = None
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        try:
            self.lock = (self.directory / "owner.lock").open("a+b")
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._checkpoint("failed", "CHECK_FAILED")
        except BaseException:
            self.close()
            raise

    def _checkpoint(self, status, code):
        if self.lock is None or (self.directory / "check.json").exists():
            raise ValueError("Capture is no longer active")
        prepared = prepare_check(self.check_id, self.sampled_at,
                                 round(time.monotonic() - self.started, 6),
                                 self.observations, status=status, error_code=code)
        validate_prepared(prepared)
        atomic_write(self.directory / "pending.json", prepared.body)

    def observe(self, term, crn, number, current):
        try:
            if self.lock is None:
                raise ValueError("Capture is no longer active")
            observation = public_observation(term, crn, number, current)
            # Validate independently so one preparation failure cannot poison
            # later successful courses. No unvalidated facts are retained.
            prepare_check(self.check_id, self.sampled_at, 0, [observation])
            self.observations.append(observation)
            self._checkpoint("failed", "CHECK_FAILED")
        except Exception:
            self.preparation_failed = True
            raise

    def finish(self, failures):
        try:
            code = ("CHECK_FAILED" if self.preparation_failed else
                    "COURSE_LOOKUP_FAILED" if failures else None)
            self._checkpoint("failed" if code else "success", code)
        finally:
            self.close()

    def close(self):
        if self.lock is not None:
            self.lock.close()
            self.lock = None

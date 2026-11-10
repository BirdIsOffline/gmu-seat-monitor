"""Opt-in ordered historical delivery on a surviving local Linux filesystem.

No production caller, GMU requests, Bark calls, or cloud resource provisioning.
SQLite commits precede HTTP; an OS lock serializes producers and consumers.
This file is NOT durable across disposable Actions runners without durable disk.
"""

import argparse
from contextlib import contextmanager
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import time

from cloud_history import (
    CloudHistoryClient, IngestionResult, MAX_BYTES, PreparedCheck,
    prepare_check, validate_prepared,
)

LOGGER = logging.getLogger(__name__)
RETRYABLE = frozenset({
    "TIMEOUT", "NETWORK_FAILURE", "INVALID_ACKNOWLEDGEMENT", "RATE_LIMITED",
    "RETRYABLE_HTTP_FAILURE", "CLIENT_ERROR",
})
SCHEMA = """
CREATE TABLE submissions (
    check_id TEXT PRIMARY KEY,
    stamp TEXT NOT NULL,
    body BLOB NOT NULL,
    body_hash TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    delivered INTEGER NOT NULL DEFAULT 0,
    blocked INTEGER NOT NULL DEFAULT 0,
    next_attempt REAL NOT NULL DEFAULT 0,
    last_code TEXT
);
CREATE TABLE submission_courses (
    check_id TEXT NOT NULL REFERENCES submissions(check_id),
    term TEXT NOT NULL,
    crn TEXT NOT NULL,
    stamp TEXT NOT NULL,
    PRIMARY KEY(check_id, term, crn),
    UNIQUE(term, crn, stamp)
);
CREATE INDEX course_order ON submission_courses(term, crn, stamp);
CREATE INDEX pending_order ON submissions(delivered, stamp, check_id);
PRAGMA user_version = 1;
"""


class OutboxError(Exception):
    """Fixed public error category; never carries a payload or raw exception."""


class HistoryOutbox:
    def __init__(self, path):
        self.path = Path(path).resolve()

    @contextmanager
    def _locked(self, *, create=False):
        # Keep the lock file forever: unlinking a held lock allows a second inode
        # and a second consumer. Requires local POSIX locking, not NFS/object sync.
        db = None
        try:
            import fcntl
            with self.path.with_suffix(self.path.suffix + ".lock").open("a+b") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise OutboxError("OUTBOX_BUSY") from None
                # A missing queue must not silently become an empty queue.
                db = sqlite3.connect(self.path.as_uri() + ("?mode=rwc" if create else "?mode=rw"),
                                     uri=True, timeout=1)
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA foreign_keys = ON")
                db.execute("PRAGMA synchronous = FULL")
                if not create and db.execute("PRAGMA user_version").fetchone()[0] != 1:
                    raise OutboxError("OUTBOX_SCHEMA")
                try:
                    yield db
                finally:
                    db.close()
                    db = None
        except ImportError:
            raise OutboxError("OUTBOX_REQUIRES_POSIX") from None
        except (OSError, sqlite3.Error):
            raise OutboxError("OUTBOX_STORAGE_ERROR") from None
        finally:
            if db is not None:
                db.close()

    def initialize(self):
        """Explicitly create once on an existing directory; never resets data."""
        with self._locked(create=True) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0 and not db.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
                db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + "\nCOMMIT;")
            elif version != 1:
                raise OutboxError("OUTBOX_SCHEMA")

    def enqueue(self, prepared):
        """Commit immutable bytes before any delivery; exact re-enqueue is a no-op.

        Pending records may arrive out of order, but once a course has been
        attempted its time frontier is sealed, even if the acknowledgement was
        lost. Older imports then fail closed instead of corrupting event order.
        """
        validate_prepared(prepared)
        payload = json.loads(prepared.body)
        stamp = payload["executed_at_utc"]
        with self._locked() as db, db:
            existing = db.execute("SELECT body FROM submissions WHERE check_id=?",
                                  (prepared.check_id,)).fetchone()
            if existing:
                if existing[0] != prepared.body:
                    raise OutboxError("OUTBOX_IDENTITY_CONFLICT")
                return False
            for row in payload["observations"]:
                frontier = db.execute("""
                    SELECT MAX(c.stamp) FROM submission_courses c JOIN submissions s USING(check_id)
                    WHERE c.term=? AND c.crn=? AND s.attempts > 0
                """, (row["term"], row["crn"])).fetchone()[0]
                if frontier is not None and stamp <= frontier:
                    raise OutboxError("OUTBOX_LATE_RECORD")
                if db.execute("SELECT 1 FROM submission_courses WHERE term=? AND crn=? AND stamp=?",
                              (row["term"], row["crn"], stamp)).fetchone():
                    raise OutboxError("OUTBOX_AMBIGUOUS_TIME")
            db.execute("INSERT INTO submissions(check_id, stamp, body, body_hash) VALUES (?, ?, ?, ?)",
                       (prepared.check_id, stamp, prepared.body, hashlib.sha256(prepared.body).hexdigest()))
            db.executemany("INSERT INTO submission_courses VALUES (?, ?, ?, ?)",
                           [(prepared.check_id, r["term"], r["crn"], stamp) for r in payload["observations"]])
        return True

    def records(self, *, include_delivered=False):
        """Operational metadata only; bodies and credentials are never printed."""
        with self._locked() as db:
            return [dict(r) for r in db.execute("""
                SELECT check_id, stamp, attempts, delivered, blocked, next_attempt, last_code
                FROM submissions WHERE delivered=0 OR ? ORDER BY stamp, check_id
            """, (include_delivered,))]

    def retry(self, check_id):
        """Explicit operator retry after diagnosis; never bypasses course ordering."""
        with self._locked() as db, db:
            updated = db.execute("UPDATE submissions SET blocked=0, next_attempt=0 WHERE check_id=? AND delivered=0",
                                 (check_id,))
            if updated.rowcount != 1:
                raise OutboxError("OUTBOX_NOT_PENDING")

    def drain(self, client, *, limit=20, clock=time.time):
        """Try due course heads once per invocation, retaining failures for restart.

        The client should use max_attempts=1. A separate process supervisor MUST
        impose a wall-clock deadline; requests timeouts only bound inactivity.
        No lock lease expires while a slow HTTP request could still commit.
        """
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Invalid drain limit")
        attempted = delivered = 0
        with self._locked() as db:
            waiting = set()
            candidates = db.execute("SELECT * FROM submissions WHERE delivered=0 ORDER BY stamp, check_id").fetchall()
            for row in candidates:
                if attempted >= limit:
                    break
                courses = {(r[0], r[1]) for r in db.execute(
                    "SELECT term, crn FROM submission_courses WHERE check_id=?", (row["check_id"],))}
                if row["blocked"] or row["next_attempt"] > clock() or courses & waiting:
                    waiting.update(courses)
                    continue
                prepared = PreparedCheck(row["check_id"], row["body"])
                try:
                    validate_prepared(prepared)
                    payload = json.loads(prepared.body)
                    if (hashlib.sha256(prepared.body).hexdigest() != row["body_hash"]
                            or payload["executed_at_utc"] != row["stamp"]
                            or {(r["term"], r["crn"]) for r in payload["observations"]} != courses):
                        raise ValueError()
                except ValueError:
                    raise OutboxError("OUTBOX_CORRUPT_RECORD") from None
                # Commit attempt/frontier BEFORE HTTP. Death during submit leaves
                # the original bytes pending and a persisted retry delay.
                delay = min(60 * 2 ** min(row["attempts"], 6), 3600)
                with db:
                    db.execute("""UPDATE submissions SET attempts=attempts+1,
                        next_attempt=?, last_code='ATTEMPT_STARTED' WHERE check_id=?""",
                               (clock() + delay, row["check_id"]))
                attempted += 1
                try:
                    result = client.submit(prepared)
                    if not isinstance(result, IngestionResult):
                        raise ValueError()
                except Exception:
                    result = IngestionResult(False, "CLIENT_ERROR", 0)
                # Unknown client codes are normalized, never echoed to disk/logs.
                code = result.code if isinstance(result.code, str) and result.code in RETRYABLE | {
                    "INGESTED", "CONFLICT", "UNAUTHORIZED", "INVALID_REQUEST", "TOO_LARGE", "HTTP_FAILURE",
                } else "CLIENT_ERROR"
                success = result.success is True and code == "INGESTED"
                with db:
                    db.execute("UPDATE submissions SET delivered=?, blocked=?, last_code=? WHERE check_id=?",
                               (success, not success and code not in RETRYABLE, code, row["check_id"]))
                if success:
                    delivered += 1
                else:
                    waiting.update(courses)
                    LOGGER.warning("Historical outbox delivery failed: %s", code)
        return {"attempted": attempted, "delivered": delivered}


def enqueue_completed_best_effort(outbox, *args, **kwargs):
    """Future isolated adapter boundary, including preparation/storage failures.

    The caller owns the UUID and completed public facts. No monitoring result is
    accepted or changed here. Successful enqueue means local commit, NOT delivery.
    """
    if outbox is None:
        return IngestionResult(False, "DISABLED", 0)
    try:
        inserted = outbox.enqueue(prepare_check(*args, **kwargs))
        return IngestionResult(True, "ENQUEUED" if inserted else "ALREADY_ENQUEUED", 0)
    except Exception:
        LOGGER.warning("Historical capture failed: OUTBOX_CAPTURE_FAILED")
        return IngestionResult(False, "OUTBOX_CAPTURE_FAILED", 0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("status")
    commands.add_parser("enqueue").add_argument("payload", type=Path)
    commands.add_parser("retry").add_argument("check_id")
    commands.add_parser("drain").add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    outbox = HistoryOutbox(args.db)
    try:
        if args.command == "init":
            outbox.initialize()
        elif args.command == "status":
            print(json.dumps(outbox.records()))
        elif args.command == "enqueue":
            with args.payload.open("rb") as stream:
                body = stream.read(MAX_BYTES + 1)
            payload = json.loads(body)
            prepared = PreparedCheck(payload["check_id"], body)
            print("ENQUEUED" if outbox.enqueue(prepared) else "ALREADY_ENQUEUED")
        elif args.command == "retry":
            outbox.retry(args.check_id)
        else:
            client = CloudHistoryClient.from_env(max_attempts=1)
            if client is None:
                raise OutboxError("DISABLED")
            try:
                print(json.dumps(outbox.drain(client, limit=args.limit)))
                return 1 if outbox.records() else 0
            finally:
                client.close()
        return 0
    except OutboxError as exc:
        LOGGER.error("Historical outbox command failed: %s", exc)
        return 1
    except (ValueError, OSError, KeyError, TypeError, RecursionError):
        LOGGER.error("Historical outbox command failed; inspect local queue and configuration")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

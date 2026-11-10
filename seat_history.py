"""Offline SQLite history. Importing this module never performs network I/O.

Callers supply public course facts, aware timestamps, and a stable check ID for
retries. SQLite recording stays offline; opt-in capture reuses CourseObservation.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

from monitor import alertable_open_seats, parse_unreserved


class HistoryError(Exception):
    """A database operation failed; the transaction was rolled back."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
    check_id TEXT PRIMARY KEY NOT NULL,
    executed_at_utc TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('success', 'failed')),
    duration_seconds REAL NOT NULL CHECK(duration_seconds >= 0),
    error_code TEXT,
    request_hash TEXT NOT NULL,
    CHECK((status = 'success' AND error_code IS NULL)
       OR (status = 'failed' AND error_code IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS observations (
    observation_id INTEGER PRIMARY KEY,
    check_id TEXT NOT NULL REFERENCES checks(check_id),
    term TEXT NOT NULL,
    subject TEXT NOT NULL,
    course_number TEXT NOT NULL,
    section TEXT NOT NULL,
    crn TEXT NOT NULL,
    total_available INTEGER CHECK(total_available >= 0),
    unreserved_available INTEGER CHECK(unreserved_available >= 0),
    reserved_summary_json TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    classification TEXT NOT NULL CHECK(classification IN
        ('full', 'unreserved', 'possible', 'reserved_only', 'unknown')),
    potential_available INTEGER NOT NULL CHECK(potential_available >= 0),
    UNIQUE(check_id, term, crn),
    CHECK(total_available IS NULL OR potential_available <= total_available)
);
CREATE TABLE IF NOT EXISTS opening_events (
    event_id INTEGER PRIMARY KEY,
    observation_id INTEGER NOT NULL UNIQUE REFERENCES observations(observation_id),
    previous_observation_id INTEGER NOT NULL REFERENCES observations(observation_id),
    previous_available INTEGER NOT NULL CHECK(previous_available >= 0),
    new_available INTEGER NOT NULL CHECK(new_available > previous_available),
    detected_at_utc TEXT NOT NULL,
    classification TEXT NOT NULL CHECK(classification IN
        ('unreserved_increase', 'possible_increase'))
);
CREATE INDEX IF NOT EXISTS idx_checks_time ON checks(executed_at_utc);
CREATE INDEX IF NOT EXISTS idx_observations_course_time
    ON observations(term, crn, observed_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_events_time ON opening_events(detected_at_utc);
"""


def _stamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must be a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _text(value, field, pattern):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError(f"Invalid {field}")
    return value


@dataclass(frozen=True)
class CourseObservation:
    term: str
    subject: str
    course_number: str
    section: str
    crn: str
    total_available: int | None
    reserved_summary: str | dict | None = None

    def values(self):
        """Validate and whitelist facts; never persist raw API responses."""
        identity = [
            _text(self.term, "term", r"[0-9]{6}"),
            _text(self.subject, "subject", r"[A-Z]{1,10}"),
            _text(self.course_number, "course number", r"[0-9A-Z]{1,10}"),
            _text(self.section, "section", r"[A-Z0-9-]{1,10}"),
            _text(self.crn, "CRN", r"[0-9]{1,10}"),
        ]
        total = self.total_available
        if total is not None and (type(total) is not int or not 0 <= total <= 1_000_000):
            raise ValueError("Seat count must be a nonnegative integer or None")
        reserved = self.reserved_summary
        if isinstance(reserved, dict):
            allowed = {"unreservedAvailable", "unreservedRemaining", "reservedAvailable", "reservedRemaining"}
            if any(k not in allowed or type(v) is not int or not 0 <= v <= 1_000_000
                   for k, v in reserved.items()):
                raise ValueError("Reserved summary must contain only public seat counts")
        elif reserved is not None and (not isinstance(reserved, str) or len(reserved) > 2000):
            raise ValueError("Reserved summary must be public text, seat-count dict, or None")
        unreserved = parse_unreserved(reserved)
        potential = alertable_open_seats({
            "seatsAvailable": total, "reservedSeatSummary": reserved,
        })
        if total is None:
            classification = "unknown"
        elif total == 0:
            classification = "full"
        elif reserved in (None, "", {}):
            classification = "possible"
        elif unreserved is None:
            classification = "unknown"
        elif unreserved == 0:
            classification = "reserved_only"
        else:
            classification = "unreserved"
        return (*identity, total, unreserved,
                json.dumps(reserved, sort_keys=True), classification, potential)


class SeatHistory:
    """File-backed history with a fresh FK-enabled connection per operation."""

    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def _connection(self):
        connection = None
        try:
            connection = sqlite3.connect(self.path, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection
        except sqlite3.Error as exc:
            raise HistoryError(f"SQLite operation failed ({type(exc).__name__})") from exc
        finally:
            if connection is not None:
                connection.close()

    def initialize(self):
        """Create schema idempotently. Parent directory must already exist."""
        with self._connection() as db:
            db.executescript(SCHEMA)

    def record_check(self, check_id, executed_at, duration_seconds, observations=(), *,
                     status="success", error_code=None):
        """Atomically record a completed check, observations, and sampled increases.

        A failed check may contain successful partial observations. Unknown total
        counts are stored, but never used as a zero-seat baseline. A first known
        observation establishes the baseline and cannot prove an increase.
        """
        _text(check_id, "check ID", r"[A-Za-z0-9_.:-]{1,128}")
        stamp = _stamp(executed_at)
        if (type(duration_seconds) not in (int, float)
                or not math.isfinite(duration_seconds) or duration_seconds < 0):
            raise ValueError("Duration must be finite and nonnegative")
        if status not in ("success", "failed"):
            raise ValueError("Invalid check status")
        if status == "failed":
            _text(error_code, "public error code", r"[A-Z][A-Z0-9_]{0,63}")
        elif error_code is not None:
            raise ValueError("Successful check cannot have an error")
        rows = []
        for item in observations:
            if not isinstance(item, CourseObservation):
                raise ValueError("Expected CourseObservation")
            rows.append(item.values())
        rows.sort(key=lambda row: (row[0], row[4]))
        if status == "success" and not rows:
            raise ValueError("Successful check needs at least one observation")
        if len({(r[0], r[4]) for r in rows}) != len(rows):
            raise ValueError("Duplicate course in check")
        payload = json.dumps([stamp, duration_seconds, status, error_code, rows])
        digest = hashlib.sha256(payload.encode()).hexdigest()
        with self._connection() as db:
            # Serialize read/compare/write across concurrent writers.
            db.execute("BEGIN IMMEDIATE")
            previous_check = db.execute(
                "SELECT request_hash FROM checks WHERE check_id = ?", (check_id,)
            ).fetchone()
            if previous_check:
                if previous_check[0] != digest:
                    raise ValueError("Check ID already exists with different contents")
                return check_id
            db.execute("INSERT INTO checks VALUES (?, ?, ?, ?, ?, ?)",
                       (check_id, stamp, status, duration_seconds, error_code, digest))
            for row in rows:
                term, subject, number, section, crn, total, unreserved, reserved, kind, potential = row
                previous = db.execute("""
                    SELECT * FROM observations WHERE term = ? AND crn = ?
                    ORDER BY observed_at_utc DESC LIMIT 1
                """, (term, crn)).fetchone()
                if previous and stamp <= previous["observed_at_utc"]:
                    raise ValueError("Course observations must be appended in timestamp order")
                if previous and (subject, number, section) != (
                    previous["subject"], previous["course_number"], previous["section"]
                ):
                    raise ValueError("Course identity changed within the same term/CRN")
                cursor = db.execute("""
                    INSERT INTO observations
                    (check_id, term, subject, course_number, section, crn,
                     total_available, unreserved_available, reserved_summary_json,
                     observed_at_utc, classification, potential_available)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (check_id, *row[:8], stamp, kind, potential))
                # Unknown eligibility follows the monitor's conservative zero;
                # missing total data is not evidence of zero availability.
                if (previous and total is not None and previous["total_available"] is not None
                        and potential > previous["potential_available"]):
                    event_kind = "unreserved_increase" if kind == "unreserved" else "possible_increase"
                    db.execute("""
                        INSERT INTO opening_events
                        (observation_id, previous_observation_id, previous_available,
                         new_available, detected_at_utc, classification)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (cursor.lastrowid, previous["observation_id"],
                          previous["potential_available"], potential, stamp, event_kind))
        return check_id

    def record_failure(self, check_id, executed_at, duration_seconds, error_code):
        return self.record_check(check_id, executed_at, duration_seconds,
                                 status="failed", error_code=error_code)

    def _query(self, sql, params=()):
        with self._connection() as db:
            return [dict(row) for row in db.execute(sql, params)]

    def history(self, term, crn):
        return self._query("""SELECT * FROM observations WHERE term = ? AND crn = ?
                              ORDER BY observed_at_utc""", (term, crn))

    def latest(self, term, crn):
        rows = self._query("""SELECT * FROM observations WHERE term = ? AND crn = ?
                               ORDER BY observed_at_utc DESC LIMIT 1""", (term, crn))
        return rows[0] if rows else None

    def opening_events(self, term, crn):
        return self._query("""
            SELECT e.*, o.term, o.subject, o.course_number, o.section, o.crn
            FROM opening_events e JOIN observations o USING(observation_id)
            WHERE o.term = ? AND o.crn = ? ORDER BY e.detected_at_utc
        """, (term, crn))

    def statistics(self):
        return self._query("""
            SELECT COUNT(*) AS completed_checks,
                   COALESCE(SUM(status = 'success'), 0) AS successful_checks,
                   COALESCE(SUM(status = 'failed'), 0) AS failed_checks,
                   CASE WHEN COUNT(*) = 0 THEN NULL
                        ELSE 100.0 * SUM(status = 'success') / COUNT(*) END AS success_rate_percent,
                   (SELECT COUNT(*) FROM observations) AS observations,
                   (SELECT COUNT(*) FROM opening_events) AS opening_events
            FROM checks
        """)[0]

    def openings_by_day(self, term, crn):
        """UTC detection dates, with exposure counts; never exact opening times."""
        return self._query("""
            SELECT substr(o.observed_at_utc, 1, 10) AS day_utc,
                   COUNT(*) AS sampled_observations, COUNT(e.event_id) AS opening_events
            FROM observations o LEFT JOIN opening_events e USING(observation_id)
            WHERE o.term = ? AND o.crn = ? GROUP BY day_utc ORDER BY day_utc
        """, (term, crn))

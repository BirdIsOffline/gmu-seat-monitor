"""Explicit opt-in HTTP history prototype; production entry points do not import it.

Prepare once from already completed observations, then retry the same immutable
body. This module never fetches GMU, reads monitoring state, or notifies Bark.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import math
import os
import re
import time
from urllib.parse import urlsplit
import uuid

import requests

from seat_history import CourseObservation

LOGGER = logging.getLogger(__name__)
MAX_BYTES = 64 * 1024
MAX_OBSERVATIONS = 50
ERROR_CODES = frozenset({"TIMEOUT", "LOOKUP_TIMEOUT", "COURSE_LOOKUP_FAILED", "CHECK_FAILED"})


@dataclass(frozen=True, repr=False)
class PreparedCheck:
    """Public, sanitized JSON snapshot; retain this object for retries."""

    check_id: str
    body: bytes


@dataclass(frozen=True)
class IngestionResult:
    success: bool
    code: str
    attempts: int


def prepare_check(check_id, executed_at, duration_seconds, observations=(), *,
                  status="success", error_code=None):
    """Whitelist completed course facts. check_id must be a caller-owned UUID4.

    Generate it once with str(uuid.uuid4()), never from a student identifier.
    Raw reservation text is reduced to counts or a fixed 'unknown' marker.
    """
    if not isinstance(check_id, str):
        raise ValueError("Expected canonical UUID4 check ID")
    try:
        parsed_id = uuid.UUID(check_id)
    except ValueError:
        raise ValueError("Expected canonical UUID4 check ID") from None
    if str(parsed_id) != check_id or parsed_id.version != 4:
        raise ValueError("Expected canonical UUID4 check ID")
    if not isinstance(executed_at, datetime) or executed_at.tzinfo is None or executed_at.utcoffset() is None:
        raise ValueError("Expected aware timestamp")
    if (type(duration_seconds) not in (int, float) or not math.isfinite(duration_seconds)
            or not 0 <= duration_seconds <= 86400):
        raise ValueError("Invalid duration")
    if status not in ("success", "failed") or (
            error_code is not None if status == "success" else error_code not in ERROR_CODES):
        raise ValueError("Invalid check status or public error code")
    rows = []
    seen = set()
    for item in observations:
        if len(rows) >= MAX_OBSERVATIONS or not isinstance(item, CourseObservation):
            raise ValueError("Invalid observation batch")
        values = item.values()
        if (item.term, item.crn) in seen:
            raise ValueError("Duplicate course in check")
        seen.add((item.term, item.crn))
        summary = item.reserved_summary
        if isinstance(summary, dict):
            if ("unreservedAvailable" in summary and "unreservedRemaining" in summary
                    and summary["unreservedAvailable"] != summary["unreservedRemaining"]):
                raise ValueError("Ambiguous unreserved counts")
            summary = dict(summary)
        elif summary in (None, ""):
            summary = None
        else:
            summary = {"unreservedAvailable": values[6]} if values[6] is not None else "unknown"
        if values[6] is not None and not 0 <= values[6] <= 1_000_000:
            raise ValueError("Invalid unreserved count")
        rows.append(dict(zip(
            ("term", "subject", "course_number", "section", "crn", "total_available"), values[:6]
        ), reserved_summary=summary))
    if status == "success" and not rows:
        raise ValueError("Successful check needs observations")
    rows.sort(key=lambda row: (row["term"], row["crn"]))
    payload = {"version": 1, "check_id": check_id,
               "executed_at_utc": executed_at.astimezone(timezone.utc).isoformat(timespec="microseconds"),
               "duration_seconds": duration_seconds, "status": status,
               "error_code": error_code, "observations": rows}
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(body) > MAX_BYTES:
        raise ValueError("Observation batch too large")
    return PreparedCheck(check_id, body)


def validate_prepared(prepared):
    """Revalidate before persistence or HTTP, including caller-made snapshots."""
    if not isinstance(prepared, PreparedCheck) or type(prepared.body) is not bytes or len(prepared.body) > MAX_BYTES:
        raise ValueError("Expected prepared public observations")
    try:
        payload = json.loads(prepared.body)
        if type(payload) is not dict or set(payload) != {
                "version", "check_id", "executed_at_utc", "duration_seconds",
                "status", "error_code", "observations"}:
            raise ValueError()
        if type(payload["observations"]) is not list:
            raise ValueError()
        observations = []
        for row in payload["observations"]:
            if type(row) is not dict or set(row) != {
                    "term", "subject", "course_number", "section", "crn", "total_available", "reserved_summary"}:
                raise ValueError()
            observations.append(CourseObservation(**row))
        rebuilt = prepare_check(payload["check_id"], datetime.fromisoformat(payload["executed_at_utc"]),
                                payload["duration_seconds"], observations,
                                status=payload["status"], error_code=payload["error_code"])
        if prepared != rebuilt:
            raise ValueError()
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise ValueError("Invalid prepared public observations") from None


class CloudHistoryClient:
    """Bounded synchronous retries, intended for a separate historical-recording step."""

    def __init__(self, endpoint, token, *, timeout=(3.05, 10), max_attempts=3,
                 allow_local_http=False, session=None, sleeper=time.sleep):
        url = urlsplit(endpoint)
        local = allow_local_http and url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1", "::1")
        if (not (url.scheme == "https" or local) or not url.hostname or url.username or url.password
                or url.path != "/v1/checks" or url.query or url.fragment):
            raise ValueError("Expected HTTPS ingestion endpoint (or explicit local HTTP)")
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", token):
            raise ValueError("Invalid ingestion secret configuration")
        if (not isinstance(timeout, tuple) or len(timeout) != 2
                or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 < v <= 30 for v in timeout)
                or type(max_attempts) is not int or not 1 <= max_attempts <= 5):
            raise ValueError("Invalid retry or timeout configuration")
        self._endpoint = endpoint
        self._token = token
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._session = session if session is not None else requests.Session()
        # No ambient .netrc credentials, proxy authentication, or cookie-dependent API.
        if session is None:
            self._session.trust_env = False
        self._sleep = sleeper

    @classmethod
    def from_env(cls, **kwargs):
        """Explicit call only. Merely setting variables cannot enable production."""
        if os.environ.get("GMU_HISTORY_ENABLED") != "true":
            return None
        return cls(os.environ.get("GMU_HISTORY_ENDPOINT", ""), os.environ.get("GMU_HISTORY_TOKEN", ""), **kwargs)

    def close(self):
        self._session.close()

    def submit(self, prepared):
        validate_prepared(prepared)
        code = "NETWORK_FAILURE"
        for attempt in range(1, self._max_attempts + 1):
            retry = True
            try:
                with self._session.post(
                    self._endpoint, data=prepared.body,
                    headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
                    timeout=self._timeout, allow_redirects=False, stream=True,
                ) as reply:
                    if reply.status_code == 200:
                        # Bound acknowledgement reads too; never log response contents.
                        data = bytearray()
                        for chunk in reply.iter_content(chunk_size=1024):
                            data.extend(chunk)
                            if len(data) > 4096:
                                break
                        try:
                            ack = json.loads(data) if len(data) <= 4096 else None
                        except (ValueError, UnicodeError):
                            ack = None
                        if ack == {"code": "INGESTED", "check_id": prepared.check_id}:
                            return IngestionResult(True, "INGESTED", attempt)
                        code = "INVALID_ACKNOWLEDGEMENT"
                    else:
                        status = reply.status_code
                        code = {401: "UNAUTHORIZED", 409: "CONFLICT", 400: "INVALID_REQUEST",
                                413: "TOO_LARGE", 429: "RATE_LIMITED"}.get(status, "HTTP_FAILURE")
                        # Include Cloudflare edge errors (e.g. 522/524), not
                        # only the statuses returned by the Worker itself.
                        retry = status in (408, 429) or 500 <= status <= 599
                        if retry and code == "HTTP_FAILURE":
                            code = "RETRYABLE_HTTP_FAILURE"
            except requests.Timeout:
                code = "TIMEOUT"
            except requests.RequestException:
                code = "NETWORK_FAILURE"
            LOGGER.warning("Historical ingestion failed: %s (attempt %d)", code, attempt)
            if not retry or attempt == self._max_attempts:
                return IngestionResult(False, code, attempt)
            self._sleep(min(0.25 * (2 ** (attempt - 1)), 2))


def persist_best_effort(client, prepared):
    """Separate boundary for future callers; never replace monitoring's result.

    A future adapter must finish normal checks/notifications before invoking this
    boundary, ideally in a separate process. No production caller is wired here.
    """
    if client is None:
        return IngestionResult(False, "DISABLED", 0)
    try:
        return client.submit(prepared)
    except Exception:
        LOGGER.warning("Historical ingestion failed: CLIENT_ERROR")
        return IngestionResult(False, "CLIENT_ERROR", 0)


def capture_completed_best_effort(client, *args, **kwargs):
    """Future Cloudflare-first boundary from already completed public facts.

    Caller owns the UUID and must retain it/its facts for any later retry. Success
    means a matching D1 commit acknowledgement, never merely local preparation.
    A failed result is unconfirmed: the server may have committed before a lost
    reply. This helper cannot recover a body lost with an ephemeral runner.
    Invoke after monitoring/notifications, within a separate process deadline.
    """
    if client is None:
        return IngestionResult(False, "DISABLED", 0)
    try:
        prepared = prepare_check(*args, **kwargs)
    except Exception:
        LOGGER.warning("Historical capture failed: CAPTURE_PREPARATION_FAILED")
        return IngestionResult(False, "CAPTURE_PREPARATION_FAILED", 0)
    result = persist_best_effort(client, prepared)
    if not result.success:
        LOGGER.warning("Historical capture unconfirmed: %s; retain original UUID and facts for retry", result.code)
    return result

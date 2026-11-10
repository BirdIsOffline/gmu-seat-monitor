"""Separate opt-in history submission. Never runs GMU lookups or Bark decisions.

A supervisor enforces a total wall-clock budget by killing its HTTP worker.
POSIX timers also bound the worker if the supervisor is abruptly killed.
Only fixed outcomes/codes/counts cross back to the supervisor or Actions summary.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

DEFAULT_DEADLINE = 20
MAX_DEADLINE = 45
MAX_CHECKS = 50
CODES = frozenset({
    "DISABLED", "INGESTED", "NO_CAPTURES", "CAPTURE_BUSY", "CAPTURE_INVALID",
    "CONFIGURATION_ERROR", "CLIENT_ERROR", "DEADLINE", "INTERRUPTED", "WORKER_FAILED",
    "TIMEOUT", "NETWORK_FAILURE", "INVALID_ACKNOWLEDGEMENT", "UNAUTHORIZED",
    "CONFLICT", "INVALID_REQUEST", "TOO_LARGE", "RATE_LIMITED", "HTTP_FAILURE",
    "RETRYABLE_HTTP_FAILURE",
})


class Interrupted(BaseException):
    pass


class Deadline(BaseException):
    pass


def outcome(status, code, checks=0, ingested=0):
    return {"outcome": status, "code": code, "checks": checks, "ingested": ingested}


def valid_outcome(value):
    return (type(value) is dict and set(value) == {"outcome", "code", "checks", "ingested"}
            and type(value["outcome"]) is str and type(value["code"]) is str
            and value["outcome"] in {"disabled", "success", "failed", "unconfirmed"}
            and value["code"] in CODES
            and all(type(value[k]) is int and 0 <= value[k] <= MAX_CHECKS for k in ("checks", "ingested"))
            and value["ingested"] <= value["checks"])


def _worker(root, allow_local_http=False):
    """Only the deadline-controlled child imports the HTTP client."""
    import fcntl
    from cloud_history import CloudHistoryClient
    from history_capture import atomic_write, read_prepared

    endpoint = os.environ.get("GMU_HISTORY_PRODUCTION_ENDPOINT", "")
    try:
        client = CloudHistoryClient(
            endpoint,
            os.environ.get("GMU_HISTORY_PRODUCTION_TOKEN", ""),
            timeout=(2, 4), max_attempts=3, allow_local_http=allow_local_http,
        )
    except Exception:
        return outcome("failed", "CONFIGURATION_ERROR")
    checks = ingested = 0
    result = outcome("failed", "NO_CAPTURES")
    try:
        # Bound both scan and submission work; this is a per-run handoff, not an
        # unbounded persistent queue. Old sealed checks can be retried explicitly.
        from itertools import islice
        try:
            directories = list(islice(Path(root).iterdir(), MAX_CHECKS + 1))
        except OSError:
            return outcome("failed", "CAPTURE_INVALID")
        if len(directories) > MAX_CHECKS:
            return outcome("failed", "CAPTURE_INVALID")
        for directory in sorted(directories):
            if not directory.is_dir() or directory.is_symlink():
                return outcome("failed", "CAPTURE_INVALID", checks, ingested)
            checks += 1
            try:
                with (directory / "owner.lock").open("a+b") as lock:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        return outcome("failed", "CAPTURE_BUSY", checks, ingested)
                    sealed = directory / "check.json"
                    if not sealed.exists():
                        # Validate before publishing, including interrupted drafts.
                        read_prepared(directory, "pending.json")
                        (directory / "pending.json").replace(sealed)
                    prepared = read_prepared(directory)
                    # Receipts are scoped to endpoint AND body, not a different
                    # staging/production target. They contain no endpoint or token.
                    digest = hashlib.sha256(endpoint.encode() + b"\0" + prepared.body).hexdigest().encode()
                    receipt = directory / "receipt.sha256"
                    if receipt.exists():
                        with receipt.open("rb") as stream:
                            acknowledged = stream.read(65) == digest
                        if acknowledged:
                            ingested += 1
                            continue
                    try:
                        delivery = client.submit(prepared)
                    except Exception:
                        # Once submission starts, even a local client/decoder
                        # error may follow a committed request. Keep uncertainty
                        # separate from validation failures before any send.
                        return outcome("unconfirmed", "CLIENT_ERROR", checks, ingested)
                    if not delivery.success:
                        # Even an HTTP error after an earlier timeout cannot prove
                        # no commit occurred. Never report uncertainty as absence.
                        return outcome("unconfirmed", delivery.code, checks, ingested)
                    try:
                        atomic_write(receipt, digest)
                    except Exception:
                        # Matching commit ACK is success even if local receipt
                        # persistence fails. Next invocation will safely retry.
                        pass
                    ingested += 1
            except (OSError, ValueError, TypeError):
                return outcome("failed", "CAPTURE_INVALID", checks, ingested)
        if checks:
            result = outcome("success", "INGESTED", checks, ingested)
        return result
    except Exception:
        return outcome("unconfirmed", "CLIENT_ERROR", checks, ingested)
    finally:
        client.close()


def submit_with_deadline(root, deadline=DEFAULT_DEADLINE, *, allow_local_http=False):
    """All file/HTTP work runs in a killable child, including DNS and ACK reads.

    Request read timeouts alone are NOT a total deadline (e.g. trickled replies).
    TERM/INT terminate the child. SIGKILL cannot run cleanup; its independent
    timer still bounds the worker, but no report/receipt is guaranteed.
    """
    if os.environ.get("GMU_HISTORY_ENABLED") != "true":
        return outcome("disabled", "DISABLED")
    if type(deadline) not in (int, float) or not math.isfinite(deadline) or not 0 < deadline <= MAX_DEADLINE:
        return outcome("failed", "CONFIGURATION_ERROR")
    started = time.monotonic()
    child = None
    previous = {}
    def interrupt(signum, frame):
        raise Interrupted()
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, interrupt)
        command = [sys.executable, str(Path(__file__).resolve()), str(root),
                   "--worker", "--deadline-seconds", str(deadline)]
        if allow_local_http:
            command.append("--allow-local-http")
        child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
        output, _ = child.communicate(timeout=max(0.001, deadline - (time.monotonic() - started)))
        if child.returncode != 0 or len(output) > 1024:
            return outcome("unconfirmed", "WORKER_FAILED")
        try:
            result = json.loads(output)
        except (ValueError, UnicodeError):
            result = None
        return result if valid_outcome(result) else outcome("unconfirmed", "WORKER_FAILED")
    except subprocess.TimeoutExpired:
        return outcome("unconfirmed", "DEADLINE")
    except Interrupted:
        return outcome("unconfirmed", "INTERRUPTED")
    except Exception:
        return outcome("unconfirmed" if child else "failed", "WORKER_FAILED")
    finally:
        # Do not leave a child retrying after cancellation or the total deadline.
        # Its process group is separate, so this never signals monitoring/Bark.
        if child is not None:
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            child.communicate()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def report(result, report_path=None, summary_path=None):
    """Fixed schema, independent of run_health.json and monitor exit status."""
    if not valid_outcome(result):
        result = outcome("failed", "WORKER_FAILED")
    text = json.dumps(result, sort_keys=True)
    print("HISTORY: " + text)
    if report_path:
        try:
            from history_capture import atomic_write
            atomic_write(Path(report_path), (text + "\n").encode())
        except Exception:
            print("HISTORY REPORT: WRITE_FAILED")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as stream:
                stream.write("\n## Historical submission (separate from monitoring health)\n\n"
                             f"{result['outcome']}: {result['code']}; "
                             f"acknowledged {result['ingested']}/{result['checks']} checks.\n\n"
                             "Unconfirmed receipt may include committed checks. Runner loss may lose unsent facts.\n")
        except Exception:
            print("HISTORY REPORT: WRITE_FAILED")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture_dir")
    parser.add_argument("--deadline-seconds", type=float, default=DEFAULT_DEADLINE)
    parser.add_argument("--allow-local-http", action="store_true", help="Loopback-only offline tests")
    parser.add_argument("--report", default="history_report.json")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if os.environ.get("GMU_HISTORY_ENABLED") != "true":
        result = outcome("disabled", "DISABLED")
    elif args.worker:
        # A second, independent hard deadline survives abrupt supervisor loss.
        def expired(signum, frame):
            raise Deadline()
        signal.signal(signal.SIGALRM, expired)
        if not math.isfinite(args.deadline_seconds) or not 0 < args.deadline_seconds <= MAX_DEADLINE:
            result = outcome("failed", "CONFIGURATION_ERROR")
        else:
            signal.setitimer(signal.ITIMER_REAL, args.deadline_seconds)
            try:
                result = _worker(args.capture_dir, args.allow_local_http)
            except Deadline:
                result = outcome("unconfirmed", "DEADLINE")
            except BaseException:
                result = outcome("unconfirmed", "WORKER_FAILED")
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
        print(json.dumps(result))
        return 0
    else:
        result = submit_with_deadline(args.capture_dir, args.deadline_seconds,
                                      allow_local_http=args.allow_local_http)
    report(result, args.report, os.getenv("GITHUB_STEP_SUMMARY"))
    return 0 if result["outcome"] in {"disabled", "success"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

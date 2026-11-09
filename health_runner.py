"""Run one public-seat check and report its outcome to GitHub Actions.

The existing monitor.py remains the source of seat and Bark notification logic.
This wrapper adds observability, not additional GMU requests or phone alerts.
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from monitor import COURSES

HERE = Path(__file__).resolve().parent
REPORT_FILE = HERE / "run_health.json"
CHECK_TIMEOUT_SECONDS = 240


def summarize_output(output, returncode):
    """Record only public course status, never account or notification secrets."""
    courses = []
    for crn, config in COURSES.items():
        name = config["name"]
        success_line = re.search(
            rf"^\[[^\n]+\]\s+{re.escape(name)}:\s*(.+)$", output, re.MULTILINE
        )
        error_line = re.search(
            rf"^ERROR\s+{re.escape(name)}\s+\(CRN\s+{re.escape(crn)}\):",
            output, re.MULTILINE,
        )
        if error_line:
            status = "failed"
            detail = "Course lookup failed (see job logs)"
        elif success_line:
            status = "success"
            detail = success_line.group(1).strip()
        else:
            status = "not_reported"
            detail = "No course result found in output (see job logs)"
        courses.append({"crn": crn, "course": name, "status": status, "detail": detail})
    success = returncode == 0 and all(row["status"] == "success" for row in courses)
    return success, courses


def write_report(report, report_path=REPORT_FILE, summary_path=None):
    try:
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"WARNING: Could not write JSON health report: {type(exc).__name__}", file=sys.stderr)
    if summary_path:
        lines = [
            "## GMU seat-check health",
            "",
            f"**Outcome:** {'SUCCESS' if report['success'] else 'FAILURE'}  ",
            f"**UTC timestamp:** {report['checked_at_utc']}  ",
            f"**Duration:** {report['duration_seconds']:.2f} seconds  ",
            "",
            f"**Execution reason:** {report.get('failure_reason') or 'Completed'}  ",
            "**Bark delivery:** Not verified by this health report.  ",
            "",
            "| Course | Lookup | Public result |",
            "| --- | --- | --- |",
        ]
        for row in report["courses"]:
            safe_detail = row["detail"].replace("|", "/").replace("\n", " ")
            lines.append(f"| {row['course']} ({row['crn']}) | {row['status']} | {safe_detail} |")
        lines += [
            "",
            "This job is read-only. Bark alerts are sent only by monitor.py for possible seat openings.",
            "The API response does not guarantee registration eligibility.",
            "",
        ]
        try:
            with open(summary_path, "a", encoding="utf-8") as stream:
                stream.write("\n".join(lines))
        except OSError as exc:
            print(f"WARNING: Could not write Actions health summary: {type(exc).__name__}", file=sys.stderr)


def state_is_valid(path):
    """Accept only nonempty public course snapshots suitable for cache restore."""
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(state, dict) and bool(state) and all(
        crn in COURSES and isinstance(row, dict)
        and type(row.get("seatsAvailable")) is int and row["seatsAvailable"] >= 0
        for crn, row in state.items()
    )


def decode_output(value):
    """TimeoutExpired may contain bytes even when text=True."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def run_once(runner=subprocess.run, report_path=REPORT_FILE, summary_path=None):
    started = time.monotonic()
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    returncode = 1
    output = ""
    failure_reason = None
    try:
        proc = runner(
            [sys.executable, "-u", str(HERE / "monitor.py"), "--once"],
            cwd=str(HERE),
            capture_output=True,
            text=True,
            timeout=CHECK_TIMEOUT_SECONDS,
            check=False,
        )
        returncode = proc.returncode
        output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    except subprocess.TimeoutExpired as exc:
        failure_reason = f"Seat check timed out after {CHECK_TIMEOUT_SECONDS} seconds."
        output = decode_output(exc.stdout) + "\n" + decode_output(exc.stderr)
        output += f"\nERROR: {failure_reason}"
    except OSError as exc:
        failure_reason = f"Could not start monitor.py: {type(exc).__name__}"
        output = f"ERROR: {failure_reason}"

    # Preserve monitor's existing logs for troubleshooting in the job output.
    if output:
        print(output, end="" if output.endswith("\n") else "\n")
    success, courses = summarize_output(output, returncode)
    if not success and failure_reason is None:
        failure_reason = "Monitor exited unsuccessfully or course results were incomplete."
    report = {
        "checked_at_utc": stamp,
        "duration_seconds": round(time.monotonic() - started, 2),
        "success": success,
        "return_code": returncode,
        "courses": courses,
        "failure_reason": failure_reason,
        "bark_delivery": "not_verified",
    }
    write_report(report, report_path, summary_path)
    print(f"HEALTH: {'PASS' if success else 'FAIL'} | "
          f"{sum(row['status'] == 'success' for row in courses)}/{len(courses)} course checks | "
          f"{report['duration_seconds']:.2f}s")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(run_once(summary_path=os.getenv("GITHUB_STEP_SUMMARY")))

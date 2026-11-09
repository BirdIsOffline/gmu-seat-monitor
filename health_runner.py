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
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if summary_path:
        lines = [
            "## GMU seat-check health",
            "",
            f"**Outcome:** {'SUCCESS' if report['success'] else 'FAILURE'}  ",
            f"**UTC timestamp:** {report['checked_at_utc']}  ",
            f"**Duration:** {report['duration_seconds']:.2f} seconds  ",
            "",
            "| Course | Check | Public result |",
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
        with open(summary_path, "a", encoding="utf-8") as stream:
            stream.write("\n".join(lines))


def run_once(runner=subprocess.run, report_path=REPORT_FILE, summary_path=None):
    started = time.monotonic()
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    returncode = 1
    output = ""
    try:
        proc = runner(
            [sys.executable, str(HERE / "monitor.py"), "--once"],
            cwd=str(HERE),
            capture_output=True,
            text=True,
            timeout=CHECK_TIMEOUT_SECONDS,
            check=False,
        )
        returncode = proc.returncode
        output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    except subprocess.TimeoutExpired:
        output = f"ERROR: Seat check exceeded {CHECK_TIMEOUT_SECONDS} seconds."
    except OSError as exc:
        output = f"ERROR: Could not start monitor.py: {type(exc).__name__}"

    # Preserve monitor's existing logs for troubleshooting in the job output.
    if output:
        print(output, end="" if output.endswith("\n") else "\n")
    success, courses = summarize_output(output, returncode)
    report = {
        "checked_at_utc": stamp,
        "duration_seconds": round(time.monotonic() - started, 2),
        "success": success,
        "return_code": returncode,
        "courses": courses,
    }
    try:
        write_report(report, report_path, summary_path)
    except OSError as exc:
        print(f"Could not write check health report: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(f"HEALTH: {'PASS' if success else 'FAIL'} | "
          f"{sum(row['status'] == 'success' for row in courses)}/{len(courses)} course checks | "
          f"{report['duration_seconds']:.2f}s")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(run_once(summary_path=os.getenv("GITHUB_STEP_SUMMARY")))

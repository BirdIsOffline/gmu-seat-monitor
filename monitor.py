"""GMU Spring 2027 read-only seat-change monitor.

Only requests publicly listed course information. No account logins,
registration actions, drops, CAPTCHA handling, or private endpoints.

GMU's public Banner search API compatibility must be verified locally.
"""

import argparse
import os
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

BASE_URL = "https://ssbstureg.gmu.edu/StudentRegistrationSsb/ssb"
PUBLIC_COURSE_SEARCH = "https://ssbstureg.gmu.edu/StudentRegistrationSsb/ssb/term/termSelection?mode=search"
TERM_NAME = "Spring 2027"

COURSES = {
    "17582": {"name": "IT 304 DL1", "number": "304"},
    "17593": {"name": "IT 341 DL1", "number": "341"},
}

STATE_PATH = Path(__file__).with_name("seat_state.json")
BARK_KEY_PATH = Path(__file__).with_name("bark_key.txt")
USER_AGENT = "GMUSeatMonitor/0.1 (student, read-only, low-frequency)"


class SearchError(Exception):
    pass


def _json_request(session, method, path, **kwargs):
    try:
        response = session.request(
            method,
            f"{BASE_URL}{path}",
            timeout=20,
            **kwargs
        )
        response.raise_for_status()
        result = response.json()
        return result

    except requests.RequestException as exc:
        raise SearchError(
            f"GMU public class search request failed: {exc}"
        ) from exc

    except ValueError as exc:
        raise SearchError(
            "GMU did not return course data in JSON format."
        ) from exc


def new_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/html, */*"
    })
    return session


def resolve_term_code():
    with new_session() as session:
        terms = _json_request(
            session,
            "GET",
            "/classSearch/getTerms",
            params={
                "offset": 1,
                "max": 100,
                "searchTerm": TERM_NAME
            }
        )

    if isinstance(terms, dict):
        terms = terms.get("data", [])

    if not isinstance(terms, list):
        raise SearchError("Unexpected term list response from GMU.")

    matches = [
        item for item in terms
        if isinstance(item, dict)
        and "spring 2027" in str(
            item.get("description", "")
        ).casefold()
    ]

    if not matches:
        raise SearchError(
            "Spring 2027 was not returned by GMU."
        )

    matches.sort(
        key=lambda x: (
            str(x.get("description", "")).strip().casefold()
            != "spring 2027",
            len(str(x.get("description", "")))
        )
    )

    code = str(matches[0].get("code", ""))

    if not code.isdigit():
        raise SearchError("GMU returned an invalid term code.")

    return code


def get_section(term_code, crn, course_number):
    with new_session() as session:
        try:
            session.get(
                PUBLIC_COURSE_SEARCH,
                timeout=20
            ).raise_for_status()

        except requests.RequestException as exc:
            raise SearchError(
                f"Could not initialize GMU search: {exc}"
            ) from exc

        setup = _json_request(
            session,
            "POST",
            "/term/search",
            data={
                "term": term_code,
                "studyPath": "",
                "studyPathText": "",
                "startDatepicker": "",
                "endDatepicker": ""
            }
        )

        if not isinstance(setup, dict) or not setup.get("fwdURL"):
            raise SearchError(
                "Could not initialize the public term search."
            )

        try:
            session.get(
                f"{BASE_URL}/classSearch/classSearch",
                timeout=20
            ).raise_for_status()

        except requests.RequestException as exc:
            raise SearchError(
                f"GMU class search page did not load: {exc}"
            ) from exc

        payload = _json_request(
            session,
            "GET",
            "/searchResults/searchResults",
            params={
                "txt_subject": "IT",
                "txt_courseNumber": course_number,
                "txt_term": term_code,
                "startDatepicker": "",
                "endDatepicker": "",
                "pageOffset": 0,
                "pageMaxSize": 100,
                "sortColumn": "subjectDescription",
                "sortDirection": "asc"
            }
        )

    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise SearchError(
            "Public course search returned unsuccessful data."
        )

    items = payload.get("data")

    if not isinstance(items, list):
        raise SearchError(
            "GMU returned an unexpected course result format."
        )

    for row in items:
        if not isinstance(row, dict):
            continue

        if (
            str(row.get("courseReferenceNumber")) == crn
            and str(row.get("courseNumber")) == course_number
            and str(row.get("subject", "")).upper() == "IT"
            and str(row.get("term")) == term_code
            and str(row.get("sequenceNumber", "")).upper() == "DL1"
        ):
            return row

    raise SearchError(
        f"IT {course_number} DL1 CRN {crn} not found."
    )


def parse_unreserved(summary):
    if isinstance(summary, dict):
        for key, value in summary.items():
            normalized = re.sub(
                r"[^a-z]",
                "",
                str(key).lower()
            )

            if (
                "unreserved" in normalized
                and any(
                    x in normalized
                    for x in ("available", "remaining")
                )
            ):
                if (
                    isinstance(value, int)
                    and not isinstance(value, bool)
                    and value >= 0
                ):
                    return value

        return None

    if not isinstance(summary, str):
        return None

    patterns = [
        r"(\d+)\s+of\s+\d+\s+unreserved",
        r"unreserved\s+seats?\s+(?:available|remaining)\s*[:=-]?\s*(\d+)",
        r"unreserved\s*[:=-]\s*(\d+)\s+of\s+\d+"
    ]

    for pattern in patterns:
        found = re.search(
            pattern,
            summary,
            flags=re.IGNORECASE
        )

        if found:
            return int(found.group(1))

    return None


def snapshot(row):
    keys = [
        "courseReferenceNumber",
        "term",
        "sequenceNumber",
        "courseTitle",
        "openSection",
        "maximumEnrollment",
        "enrollment",
        "seatsAvailable",
        "reservedSeatSummary",
        "waitCapacity",
        "waitCount",
        "waitAvailable",
        "crossListCapacity",
        "crossListAvailable"
    ]

    return {
        key: row.get(key)
        for key in keys
    }


def seat_message(course, data):
    remaining = data.get("seatsAvailable")
    reserved = data.get("reservedSeatSummary")
    unreserved = parse_unreserved(reserved)

    if reserved in (None, "", [], {}):
        if isinstance(remaining, int) and remaining > 0:
            detail = (
                f"{remaining} total seats visible; "
                "no reserved-seat summary returned. "
                "Verify in Patriot Web."
            )
        else:
            detail = (
                f"{remaining} total seats remaining."
            )

    elif unreserved is not None:
        detail = (
            f"{unreserved} unreserved seats available; "
            f"{remaining} total seats visible."
        )

    else:
        detail = (
            f"{remaining} total seats visible; "
            "reserved-seat details present. "
            "Eligibility UNKNOWN."
        )

    return f"{course}: {detail}"


def possible_eligible(data):
    remaining = data.get("seatsAvailable")

    if not isinstance(remaining, int) or remaining <= 0:
        return False

    reserved = data.get("reservedSeatSummary")

    if reserved in (None, "", [], {}):
        return True

    unreserved = parse_unreserved(reserved)

    return unreserved is not None and unreserved > 0


def alertable_open_seats(data):
    """
    Only count potentially available seats.

    Reserved seats are not counted as available.

    Missing reservation details are treated as
    possible availability, not guaranteed eligibility.
    """

    remaining = data.get("seatsAvailable")

    if (
        not isinstance(remaining, int)
        or isinstance(remaining, bool)
        or remaining <= 0
    ):
        return 0

    reserved = data.get("reservedSeatSummary")

    if reserved in (None, "", [], {}):
        return remaining

    unreserved = parse_unreserved(reserved)

    if unreserved is None:
        return 0

    return max(0, min(remaining, unreserved))


def read_bark_key():
    key = os.getenv("BARK_DEVICE_KEY", "").strip()

    if not key:
        try:
            key = BARK_KEY_PATH.read_text(
                encoding="utf-8"
            ).strip()

        except FileNotFoundError:
            return None

        except OSError:
            print("Could not read Bark configuration.")
            return None

    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", key):
        print("Bark key invalid.")
        return None

    return key


def notify(title, body):
    print("\n" + "=" * 65)
    print(f"ALERT: {title}")
    print(body)
    print(f"Open: {PUBLIC_COURSE_SEARCH}")
    print("=" * 65 + "\n")

    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)

    except (ImportError, RuntimeError):
        print("\a", end="", flush=True)

    key = read_bark_key()

    if not key:
        print("Bark not configured.")
        return False

    try:
        response = requests.post(
            "https://api.day.app/push",
            json={
                "device_key": key,
                "title": title,
                "body": (
                    body
                    + "\n\nCheck GMU in Patriot Web "
                    "before changing any registration."
                ),
                "url": PUBLIC_COURSE_SEARCH,
                "level": "timeSensitive",
                "group": "GMU Classes"
            },
            timeout=15
        )

        response.raise_for_status()

        try:
            result = response.json()

        except ValueError:
            print("Bark returned an unexpected response.")
            return False

        if (
            not isinstance(result, dict)
            or result.get("code") != 200
        ):
            print("Bark did not confirm delivery.")
            return False

        print("Bark accepted the phone push.")
        return True

    except requests.RequestException as exc:
        print(f"Bark notification request failed: {exc}")
        return False


def load_state():
    try:
        return json.loads(
            STATE_PATH.read_text(encoding="utf-8")
        )

    except FileNotFoundError:
        return {}

    except (ValueError, OSError):
        print("Previous state could not be read.")
        return {}


def save_state(state):
    path = STATE_PATH.with_suffix(".tmp")

    path.write_text(
        json.dumps(state, indent=2),
        encoding="utf-8"
    )

    path.replace(STATE_PATH)


def check_once(term_code, state):
    failures = 0

    for crn, course in COURSES.items():
        name = course["name"]

        try:
            current = snapshot(
                get_section(
                    term_code,
                    crn,
                    course["number"]
                )
            )

        except SearchError as exc:
            print(f"ERROR {name} (CRN {crn}): {exc}")
            failures += 1
            continue

        print(
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
            f"{seat_message(name, current)}"
        )

        previous = state.get(crn)

        if isinstance(previous, dict):
            old_open = alertable_open_seats(previous)
        else:
            old_open = 0

        new_open = alertable_open_seats(current)

        # Notify ONLY when potentially available seats increase.
        # This includes reserved seats becoming unreserved.
        #
        # Do NOT notify for:
        # - Reserved-only seat changes
        # - A class becoming full
        # - Professor changes
        # - Unrelated course changes
        # - No seat changes

        if new_open > old_open:
            title = (
                f"Possible seat opening: {name} "
                f"(CRN {crn})"
            )

            body = (
                f"{seat_message(name, current)}\n"
                f"Potentially available seats: "
                f"{old_open} -> {new_open}.\n"
                "Check Patriot Web immediately. "
                "Seats may still have restrictions. "
                "Do not drop your current registered "
                "section without a safe swap."
            )

            notify(title, body)

        state[crn] = current
        save_state(state)

        time.sleep(2)

    return failures


def demo():
    cases = [
        (
            "IT 304 DL1",
            {
                "seatsAvailable": 0,
                "reservedSeatSummary": (
                    "0 of 10 unreserved; "
                    "0 of 30 reserved"
                )
            }
        ),
        (
            "IT 341 DL1",
            {
                "seatsAvailable": 9,
                "reservedSeatSummary": (
                    "0 of 10 unreserved; "
                    "9 of 30 reserved"
                )
            }
        ),
        (
            "Hypothetical IT 341 opening",
            {
                "seatsAvailable": 10,
                "reservedSeatSummary": (
                    "1 of 10 unreserved; "
                    "9 of 30 reserved"
                )
            }
        )
    ]

    print(
        "OFFLINE DEMO: no network requests "
        "and no registration changes"
    )

    for label, row in cases:
        print(
            f"{seat_message(label, row)} | "
            f"Potentially eligible: {possible_eligible(row)}"
        )


def main():
    parser = argparse.ArgumentParser(
        description=(
            "GMU Spring 2027 class seat monitor "
            "(read-only)"
        )
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one live check of both CRNs"
    )

    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run offline demonstration"
    )

    parser.add_argument(
        "--test-notification",
        action="store_true",
        help="Send Bark phone test"
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=900,
        help="Seconds between cycles (minimum 600)"
    )

    args = parser.parse_args()

    if args.demo:
        demo()
        return 0

    if args.test_notification:
        sent = notify(
            "GMU phone alert test",
            (
                "Notifications are configured for "
                "IT 304 and IT 341. "
                "This is only a test; "
                "no seat change detected."
            )
        )

        return 0 if sent else 1

    if args.interval < 600:
        parser.error(
            "Interval must be at least 600 seconds."
        )

    print("GMU public class search monitor — READ ONLY")
    print(
        "Watching: IT 304 DL1 CRN 17582; "
        "IT 341 DL1 CRN 17593"
    )
    print(
        "This script never signs into Patriot Web "
        "and never registers/drops classes."
    )
    print(
        "Run continuous monitoring only if GMU "
        "permits automated polling."
    )

    state = load_state()
    consecutive_errors = 0

    while True:
        try:
            term_code = resolve_term_code()
            errors = check_once(term_code, state)

        except SearchError as exc:
            errors = len(COURSES)
            print(f"ERROR: {exc}")

        except requests.RequestException as exc:
            errors = len(COURSES)
            print(f"ERROR: {exc}")

        if errors:
            consecutive_errors += 1
        else:
            consecutive_errors = 0

        if args.once:
            return 1 if errors else 0

        if consecutive_errors >= 3:
            print(
                "Three consecutive check cycles failed. "
                "Stopping."
            )
            return 1

        print(
            f"Next check in {args.interval // 60} "
            "minutes. Ctrl+C to stop.\n"
        )

        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        sys.exit(main())

    except KeyboardInterrupt:
        print(
            "\nMonitoring stopped. "
            "Your registrations were never modified."
        )
        sys.exit(0)
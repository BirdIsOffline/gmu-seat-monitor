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
        response = session.request(method, f"{BASE_URL}{path}", timeout=20, **kwargs)
        response.raise_for_status()
        result = response.json()
        return result
    except requests.RequestException as exc:
        raise SearchError(f"GMU public class search request failed: {exc}") from exc
    except ValueError as exc:
        raise SearchError("GMU did not return course data in JSON format; public search may have changed.") from exc


def new_session():
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json, text/html, */*"})
    return session


def resolve_term_code():
    # Determine the term from GMU's actual public listing; never assume the code.
    with new_session() as session:
        terms = _json_request(
            session, "GET", "/classSearch/getTerms",
            params={"offset": 1, "max": 100, "searchTerm": TERM_NAME},
        )
    if isinstance(terms, dict):
        terms = terms.get("data", [])
    if not isinstance(terms, list):
        raise SearchError("Unexpected term list response from GMU.")
    matches = [
        item for item in terms if isinstance(item, dict)
        and "spring 2027" in str(item.get("description", "")).casefold()
    ]
    if not matches:
        raise SearchError("Spring 2027 was not returned by GMU's public term lookup.")
    # Favor standard Spring 2027 over other specifically titled sessions.
    matches.sort(key=lambda x: (str(x.get("description", "")).strip().casefold() != "spring 2027", len(str(x.get("description", "")))))
    code = str(matches[0].get("code", ""))
    if not code.isdigit():
        raise SearchError("GMU term lookup returned an invalid term code.")
    return code


def get_section(term_code, crn, course_number):
    with new_session() as session:
        try:
            session.get(PUBLIC_COURSE_SEARCH, timeout=20).raise_for_status()
        except requests.RequestException as exc:
            raise SearchError(f"Could not initialize GMU's public class search: {exc}") from exc
        setup = _json_request(
            session, "POST", "/term/search", data={
                "term": term_code,
                "studyPath": "",
                "studyPathText": "",
                "startDatepicker": "",
                "endDatepicker": "",
            },
        )
        if not isinstance(setup, dict) or not setup.get("fwdURL"):
            raise SearchError("Could not initialize the public term search (unexpected GMU response).")
        # Mirrors the public Search Classes page, without viewing account data.
        try:
            session.get(f"{BASE_URL}/classSearch/classSearch", timeout=20).raise_for_status()
        except requests.RequestException as exc:
            raise SearchError(f"GMU public class search page did not load: {exc}") from exc
        payload = _json_request(
            session, "GET", "/searchResults/searchResults",
            params={
                "txt_subject": "IT",
                "txt_courseNumber": course_number,
                "txt_term": term_code,
                "startDatepicker": "",
                "endDatepicker": "",
                "pageOffset": 0,
                "pageMaxSize": 100,
                "sortColumn": "subjectDescription",
                "sortDirection": "asc",
            },
        )
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise SearchError("Public course search returned unsuccessful or incompatible data.")
    items = payload.get("data")
    if not isinstance(items, list):
        raise SearchError("GMU returned an unexpected course result format.")
    for row in items:
        if not isinstance(row, dict):
            continue
        if (str(row.get("courseReferenceNumber")) == crn
                and str(row.get("courseNumber")) == course_number
                and str(row.get("subject", "")).upper() == "IT"
                and str(row.get("term")) == term_code
                and str(row.get("sequenceNumber", "")).upper() == "DL1"):
            return row
    raise SearchError(f"Exact IT {course_number} DL1 CRN {crn} not found in Spring 2027 results.")


def parse_unreserved(summary):
    """Only infer when GMU explicitly labels an available UNRESERVED count."""
    if isinstance(summary, dict):
        for key, value in summary.items():
            normalized = re.sub(r"[^a-z]", "", str(key).lower())
            if "unreserved" in normalized and any(x in normalized for x in ("available", "remaining")):
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    return value
        # Other dictionary shapes are unknown and should not be guessed.
        return None
    if not isinstance(summary, str):
        return None
    patterns = [
        r"(\d+)\s+of\s+\d+\s+unreserved",
        r"unreserved\s+seats?\s+(?:available|remaining)\s*[:=-]?\s*(\d+)",
        r"unreserved\s*[:=-]\s*(\d+)\s+of\s+\d+",
    ]
    for pattern in patterns:
        found = re.search(pattern, summary, flags=re.IGNORECASE)
        if found:
            return int(found.group(1))
    return None


def snapshot(row):
    """Small, public-data-only snapshot; don't store roster or student information."""
    keys = [
        "courseReferenceNumber", "term", "sequenceNumber", "courseTitle", "openSection",
        "maximumEnrollment", "enrollment", "seatsAvailable", "reservedSeatSummary",
        "waitCapacity", "waitCount", "waitAvailable", "crossListCapacity", "crossListAvailable",
    ]
    return {key: row.get(key) for key in keys}


def seat_message(course, data):
    remaining = data.get("seatsAvailable")
    reserved = data.get("reservedSeatSummary")
    unreserved = parse_unreserved(reserved)
    if reserved in (None, "", [], {}):
        if isinstance(remaining, int) and remaining > 0:
            detail = f"{remaining} total seats visible; no reserved-seat summary returned. Verify in Patriot Web."
        else:
            detail = f"{remaining} total seats remaining (public search)."
    elif unreserved is not None:
        detail = f"{unreserved} unreserved seats available; {remaining} total seats visible."
    else:
        detail = f"{remaining} total seats visible; reserved-seat details present. Eligibility UNKNOWN: check Patriot Web."
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


def read_bark_key():
    """Read Bark key from a GitHub Actions secret or local file; never display it."""
    key = os.getenv("BARK_DEVICE_KEY", "").strip()
    if not key:
        try:
            key = BARK_KEY_PATH.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        except OSError:
            print("Could not read local Bark configuration.")
            return None
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", key):
        print("Bark key invalid; check BARK_DEVICE_KEY secret or local setup.")
        return None
    return key


def notify(title, body):
    """Show local alert; optionally send iPhone push through the free Bark app."""
    print("\n" + "=" * 65)
    print(f"ALERT: {title}\n{body}\nOpen: {PUBLIC_COURSE_SEARCH}")
    print("=" * 65 + "\n")
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except (ImportError, RuntimeError):
        print("\a", end="", flush=True)

    key = read_bark_key()
    if not key:
        print("Bark not configured. Run: py setup_bark.py")
        return False

    # Only public course information is sent. Never send GMU account data.
    # Bark's official server sends notifications through Apple APNs.
    try:
        response = requests.post(
            "https://api.day.app/push",
            json={
                "device_key": key,
                "title": title,
                "body": body + "\n\nCheck GMU in Patriot Web before changing any registration.",
                "url": PUBLIC_COURSE_SEARCH,
                "level": "timeSensitive",
                "group": "GMU Classes",
            },
            timeout=15,
        )
        response.raise_for_status()
        try:
            result = response.json()
        except ValueError:
            print("Bark returned an unexpected response; check the app.")
            return False
        if not isinstance(result, dict) or result.get("code") != 200:
            print("Bark did not confirm delivery. Check the key and try again.")
            return False
        print("Bark accepted the phone push. Check the iPhone Lock Screen.")
        return True
    except requests.RequestException as exc:
        print(f"Bark notification request failed: {exc}")
        return False


def load_state():
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (ValueError, OSError):
        print("Previous state could not be read; starting with no saved state.")
        return {}


def save_state(state):
    path = STATE_PATH.with_suffix(".tmp")
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    path.replace(STATE_PATH)


def check_once(term_code, state):
    failures = 0
    for crn, course in COURSES.items():
        name = course["name"]
        try:
            current = snapshot(get_section(term_code, crn, course["number"]))
        except SearchError as exc:
            print(f"ERROR {name} (CRN {crn}): {exc}")
            failures += 1
            continue
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {seat_message(name, current)}")
        previous = state.get(crn)
        if previous is None:
            if possible_eligible(current):
                notify(f"Potential eligible opening: {name} (CRN {crn})", seat_message(name, current))
        elif previous != current:
            previous_possible = possible_eligible(previous)
            current_possible = possible_eligible(current)
            if current_possible and not previous_possible:
                title = f"Possible NEW eligible opening: {name} (CRN {crn})"
            else:
                title = f"Seat status changed: {name} (CRN {crn})"
            notify(title, f"Previous: {seat_message(name, previous)}\nCurrent: {seat_message(name, current)}\nSeats may still be reserved or restricted. Do not drop your current section.")
        state[crn] = current
        save_state(state)
        time.sleep(2)  # do not issue requests in bursts
    return failures


def demo():
    cases = [
        ("IT 304 DL1", {"seatsAvailable": 0, "reservedSeatSummary": "0 of 10 unreserved; 0 of 30 reserved"}),
        ("IT 341 DL1", {"seatsAvailable": 9, "reservedSeatSummary": "0 of 10 unreserved; 9 of 30 reserved"}),
        ("Hypothetical IT 341 opening", {"seatsAvailable": 10, "reservedSeatSummary": "1 of 10 unreserved; 9 of 30 reserved"}),
    ]
    print("OFFLINE DEMO: no network requests and no registration changes")
    for label, row in cases:
        print(f"{seat_message(label, row)} | Potentially eligible: {possible_eligible(row)}")


def main():
    parser = argparse.ArgumentParser(description="GMU Spring 2027 class seat-change monitor (read-only)")
    parser.add_argument("--once", action="store_true", help="Run one live check of both CRNs")
    parser.add_argument("--demo", action="store_true", help="Run offline reserved-seat logic demonstration")
    parser.add_argument("--test-notification", action="store_true", help="Send a Bark phone test; does not query GMU")
    parser.add_argument("--interval", type=int, default=900, help="Seconds between cycles (minimum 600; default 900)")
    args = parser.parse_args()
    if args.demo:
        demo()
        return 0
    if args.test_notification:
        sent = notify("GMU phone alert test", "Notifications are configured for IT 304 and IT 341. This is only a test; no seat change detected.")
        return 0 if sent else 1
    if args.interval < 600:
        parser.error("To minimize load on GMU's systems, interval must be at least 600 seconds.")
    print("GMU public class search monitor — READ ONLY")
    print("Watching: IT 304 DL1 CRN 17582; IT 341 DL1 CRN 17593")
    print("This script never signs into Patriot Web and never registers/drops classes.")
    print("Run continuous monitoring only if GMU permits automated polling.")
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
            print("Three consecutive check cycles failed. Stopping rather than repeatedly hitting the service.")
            return 1
        print(f"Next check in {args.interval // 60} minutes. Ctrl+C to stop.\n")
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nMonitoring stopped. Your registrations were never modified.")
        sys.exit(0)

Campus Seat Watch — GMU Course Availability Monitor
![Python CI](https://github.com/BirdIsOffline/gmu-seat-monitor/actions/workflows/ci.yml/badge.svg)
A read-only Python automation project that checks published university course availability and sends an iPhone push alert when potentially eligible seats increase. Built as a practical systems-integration project using Python, HTTP/JSON APIs, GitHub Actions, an external scheduler, and Bark notifications.
> **Status:** Working personal prototype. This is **not** a registration bot, does not log into a student account, and cannot guarantee seat eligibility or availability. The published seat search is institution-specific; only run automated checks with the institution's permission.
Why I built it
High-demand classes can fill quickly. The challenge is distinguishing general-availability seats from reserved seats, then alerting only when an opening might actually help. The project turns public course-search responses into an automated, low-noise notification workflow.
Architecture
```text
External scheduler (cron-job.org; configured separately)
     |
     | GitHub workflow_dispatch request (scoped API token)
     v
GitHub Actions: seat-monitor.yml
     |
     | Python monitor.py --once
     v
Public GMU course-search API (HTTP/JSON)
     |
     v
Parse reserved vs. unreserved seats
     |
     v
Compare with last saved public-data snapshot (GitHub Actions cache)
     |
     +-- No eligible-seat increase --> No phone alert
     |
     +-- Potentially eligible increase --> Bark API --> iPhone alert
```
A separate Python CI workflow runs offline tests whenever source code is pushed or a pull request is opened. CI does not contact GMU or send live phone notifications.
Technical highlights
Precise matching: validates the Spring 2027 term, subject, course number, section, and CRN.
Reserved-seat filtering: alerts for increased unreserved availability, not extra reserved-only seats.
Stateful change detection: compares public course snapshots between scheduled workflow runs.
Noisy-event suppression: ignores instructor changes, falling seat counts, and ordinary no-change checks.
Secure configuration: reads the Bark key from `BARK_DEVICE_KEY` in GitHub Actions Secrets (or from a local, gitignored file).
Automated testing: 29 offline unit tests cover seat parsing, notification conditions, state persistence, and mocked integration boundaries.
Notification behavior
Change observed	Sends an opening alert?
Unreserved seats: 0 → 1	Yes
Unreserved seats: 1 → 2	Yes
Only reserved seats: 9 → 10	No
Available unreserved seats: 1 → 0	No
Reserved capacity is released into unreserved availability	Yes, if the response reflects it
Instructor details or unrelated data change	No
No relevant change	No
Important: When reservation data is missing entirely, the monitor may flag total visible seats as potentially available. Always verify eligibility and availability in Patriot Web before changing a registration. Monitoring checks are not instantaneous, so brief openings may be missed.
Local quick start
Python 3.12+ is recommended. From the repository folder:
```bash
python -m pip install -r requirements.txt
python monitor.py --demo
python -m unittest discover -s tests -v
```
The demo and test suite do not contact GMU or Bark.
To perform one live, read-only check only if authorized:
```bash
python monitor.py --once
```
For Bark configuration, `python setup_bark.py` stores a device key in the local ignored `bark_key.txt` file. Never commit that file or paste its contents into issues, logs, or screenshots.
Deployment
The operational workflow is `.github/workflows/seat-monitor.yml`. It is triggered by the external scheduler using `workflow_dispatch`, with input `mode: check`. The Bark key is a GitHub Actions repository secret named `BARK_DEVICE_KEY`, and authorization for polling is controlled by `GMU_POLLING_APPROVED`.
The external scheduler, token, and action secrets are not contained in this public repository. Scheduled triggers are best-effort and can be delayed or fail; inspect run logs regularly. Read CLOUD_SETUP.md for setup, restrictions, and troubleshooting.
Project files
File	Purpose
`monitor.py`	Public course lookup, seat parsing, change detection, Bark alerts
`setup_bark.py`	Configure local notification delivery
`.github/workflows/seat-monitor.yml`	Operational cloud seat checks
`.github/workflows/ci.yml`	Automated tests on code changes
`tests/test_monitor.py`	Offline regression tests and mocked integration tests
`docs/ARCHITECTURE.md`	Components, data flow, design decisions, limitations
`docs/ROADMAP.md`	Planned engineering improvements
Design tradeoffs and limitations
A possible opening is not an enrollment guarantee; eligibility may depend on rules the public search does not disclose.
Cloud scheduled jobs are periodic, not continuously connected. An opening can appear and disappear between checks.
A cached snapshot can be evicted. On a cold start, the bot may send a one-time opening notification if seats are already available.
The external scheduler needs an appropriately scoped GitHub token and is an additional point of failure.
The system intentionally does not authenticate to Patriot Web, bypass reserved seating, or add/drop classes.
See architecture notes and planned improvements.
Technologies
Python · `requests` · JSON · REST-style HTTP APIs · GitHub Actions · cron-job.org · Bark · `unittest` / `unittest.mock`
Note: This is an independent student project; it is not affiliated with or endorsed by George Mason University.

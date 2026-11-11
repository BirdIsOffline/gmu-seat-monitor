# GMU Seat Monitor

Read-only course availability monitoring with automated checks, iPhone notifications, and historical analytics.

**Stack:** Python · GitHub Actions · REST/HTTP APIs · Cloudflare Workers · Cloudflare D1 · JavaScript

## Overview

GMU Seat Monitor checks public George Mason University course listings for specified sections and detects increases in potentially available seats. It uses an external scheduler to dispatch a GitHub Actions workflow, stores the previous observation for comparison, and sends Bark push notifications for qualifying changes.

A separate Cloudflare Worker stores historical observations in D1. An independently deployed, token-protected dashboard displays seat history, monitoring cadence, check results, and data freshness.

The current configuration monitors **Spring 2027**:
- **IT 304 DL1** — CRN 17582
- **IT 341 DL1** — CRN 17593

## Features

- **Exact section matching:** validates semester, subject, course number, section, and CRN against the published catalog.
- **Reservation-aware detection:** distinguishes explicitly unreserved seats from reserved-only inventory. When reservation detail is unavailable, potential availability is marked as uncertain.
- **Change-based push alerts:** compares current and previous observations to suppress unchanged, decreasing, and known reserved-only states.
- **Automated execution:** external five-minute dispatch triggers a GitHub Actions monitoring job; scheduled execution is best-effort.
- **Historical storage:** separate authenticated Cloudflare Worker records public course-level checks, observations, and opening events in D1.
- **Analytics dashboard:** separate read-token-protected Worker provides 24-hour and 7-day charts, recent checks, cadence statistics, and freshness indicators.
- **Automated tests:** offline Python and JavaScript tests cover classification, alert decisions, persistence, Worker endpoints, and failure cases.

## Architecture

```text
External scheduler
       |
       v
GitHub Actions ---> Python catalog monitor ---> Public GMU course data
       |                      |
       |                      +-- State comparison --> Bark --> iPhone
       |                      |
       |                      +-- Previous snapshot --> Actions cache
       |
       +-- Optional history capture/submit
                         |
                         v
                Cloudflare ingestion Worker
                         |
                         v
                    Cloudflare D1
                         |
                         v
                 Dashboard Worker
                         |
                         v
                   Private dashboard
```

See [system diagram](docs/SYSTEM_DIAGRAM.md) and [architecture decisions](docs/ARCHITECTURE.md) for implementation details.

## Run locally

Requires Python 3.12+.

```bash
python -m pip install -r requirements.txt
python monitor.py --demo
python demo_history.py
python demo_live_history.py
python -m unittest discover -s tests -v
```

These demos and tests use synthetic data and mocked network requests. They do not contact GMU or Bark.

Run the dashboard's Node.js tests:

```bash
node --check dashboard/worker.js
node --test dashboard/worker.test.mjs
```

For further local testing, see [offline demonstration](docs/LOCAL_DEMO.md).

## Repository guide

| Path | Contents |
| --- | --- |
| [`monitor.py`](monitor.py) | Course lookup, seat classification, change detection, and push delivery |
| [`health_runner.py`](health_runner.py) | Monitoring health reporting |
| [`cloud_history.py`](cloud_history.py) | Historical payload validation |
| [`history_capture.py`](history_capture.py) and [`history_submit.py`](history_submit.py) | Historical collection and submission |
| [`cloudflare/`](cloudflare/) | Ingestion Worker, D1 schema, migrations, and tests |
| [`dashboard/`](dashboard/) | Historical dashboard Worker and tests |
| [`tests/`](tests/) | Python tests with mocked external services |
| [`.github/workflows/`](.github/workflows/) | Monitoring and CI workflows |

## Deployment and operations

The monitoring and CI workflows are separate. Live monitoring requires explicit approval through the `GMU_POLLING_APPROVED` repository variable; Bark delivery uses a GitHub Actions secret. Cloudflare ingestion and dashboard services use separate credentials and are deployed independently.

- [Cloud setup](CLOUD_SETUP.md)
- [Dashboard deployment](dashboard/DEPLOYMENT.md)
- [Term rollover and archival](docs/TERM_LIFECYCLE.md)
- [Security](SECURITY.md)
- [Engineering roadmap](docs/ROADMAP.md)

## Limitations

The monitor does not register students, access student accounts, or make enrollment changes. A reported seat increase is **not** confirmation of registration eligibility; official registration systems remain authoritative.

GitHub Actions dispatch and execution are best-effort. Checks can be delayed or missed, brief openings may occur between samples, and the previous-state cache can be evicted. Historical statistics describe recorded observations, not guaranteed scheduler uptime. The current semester and CRNs are configured explicitly rather than switched automatically.

Automated requests to university systems require appropriate authorization. Do not enable polling without confirming access and rate-limit requirements.

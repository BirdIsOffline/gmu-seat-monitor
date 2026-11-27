# GMU Seat Monitor

Read-only course availability monitoring with reservation-aware iPhone alerts and authenticated historical analytics.

The system matches exact sections in the public GMU catalog, compares each section with its previous observation, and sends Bark notifications for increases in potentially available seats. GitHub Actions runs the monitor through an external five-minute dispatcher. Separate Cloudflare Workers ingest sanitized observations into D1 and serve a token-protected dashboard.

It does not access student accounts, register for classes, or establish registration eligibility.

## Architecture

| Component | Responsibility |
| --- | --- |
| Python monitor | Exact term, subject, course number, section and CRN matching; reservation parsing; change detection; Bark delivery |
| GitHub Actions | Gated monitoring, cached comparison state, health reports and separately bounded historical submission |
| Ingestion Worker and D1 | Authenticated, validated, atomic storage; chronological opening-event projection |
| Dashboard Worker | Authenticated read-only history API; independent cards and charts; stored-check cadence and active-course freshness |
| Offline CI | Python, Worker/D1 integration and dashboard tests without university requests or phone notifications |

See the [system diagram](docs/SYSTEM_DIAGRAM.md) and [design decisions](docs/ARCHITECTURE.md).

**Technology:** Python 3.12, Requests, SQLite, GitHub Actions, Cloudflare Workers, Cloudflare D1, JavaScript, Node.js 24 and Bark. Miniflare/workerd provides disposable local Worker/D1 integration tests.

## Section identity and lifecycle

The current configuration is scoped to Spring 2027:

| Section | CRN | Status |
| --- | --- | --- |
| IT 304 DL1 | 17582 | Retired; history only |
| IT 341 DL1 | 17593 | Active |
| IT 343 DL1 | 17599 | Active |
| IT 343 DL2 | 20981 | Active |
| IT 343 DL3 | 21051 | Active |

Comparison state and dashboard identities use CRNs, not course numbers. Historical storage uses `(term, crn)`, allowing sections of the same course to retain independent observations, alerts and event baselines. Monitor and dashboard configurations are checked for parity in CI.

Retirement stops new lookups and seat alerts without deleting cached snapshots, observations or events. Status changes are explicit; no section is retired automatically. [Lifecycle guidance](docs/TERM_LIFECYCLE.md) covers archival and the single-term limitation.

## Notifications and analytics

- Known reserved-only availability does not trigger an opening alert. Explicit unreserved counts are capped by the reported total. Missing reservation metadata is uncertain potential availability; unparseable restrictions suppress the candidate count.
- Increasing candidate counts trigger alerts identifying section and CRN. Unchanged or decreasing counts remain silent while comparison state survives.
- Actionable alerts request `sound=alarm`, `call=1`, `level=critical` and `volume=10`. Bark acceptance does not prove iPhone delivery or playback; permissions and device settings still apply.
- The dashboard presents all five identities in 24-hour and seven-day views. Each active section independently contributes to freshness; a recent check or another section cannot conceal missing observations.
- Stored-check success, cadence and freshness describe recorded samples, not guaranteed scheduler uptime, notification delivery or registration outcomes.

## Verified results

[Hosted Ubuntu CI](https://github.com/BirdIsOffline/gmu-seat-monitor/actions/runs/45922191176) passed **244 automated tests with zero skips**: 192 Python, 28 Worker/D1 integration and 24 dashboard tests. Coverage includes section isolation, reservation filtering, duplicate suppression, immutable retries, transaction rollback, delayed observations, process interruption, locking and dashboard rendering.

The deployed dashboard was checked for authenticated HTTP 200, unauthorized HTTP 401, five independent identities, four-target freshness, both chart ranges and preservation of every baseline historical row. Missing-data behavior was exercised through browser-only replay without production writes. A separately labelled live Bark alarm test received API acceptance, and the operator confirmed successful alarm playback with approximately 20 seconds of persistent ringing. It was a controlled notification test, not a genuine seat opening with the new alarm settings. This does not establish registration success or guarantee future phone delivery.

## Run offline

Use Linux/POSIX, Python 3.12 and Node.js 24 for the full suite:

```bash
python -m pip install -r requirements.txt
npm ci --prefix cloudflare
python -m unittest discover -s tests -v
npm test --prefix cloudflare
node --test dashboard/worker.test.mjs
```

Dependency installation accesses package registries. Tests use mocks, disposable databases and explicit loopback connections, not live GMU, Bark or Cloudflare services. POSIX capture/outbox tests require Linux; the complete suite is not supported on Windows. See [local tests and demonstrations](docs/LOCAL_DEMO.md).

## Repository guide

| Path | Purpose |
| --- | --- |
| [monitor.py](monitor.py), [health_runner.py](health_runner.py) | Decisions, delivery and health reporting |
| [history_capture.py](history_capture.py), [history_submit.py](history_submit.py), [cloud_history.py](cloud_history.py) | Sanitized handoff, bounded submission and HTTP protocol |
| [seat_history.py](seat_history.py), [history_outbox.py](history_outbox.py) | SQLite reference and optional persistent POSIX outbox |
| [cloudflare/](cloudflare/) | Ingestion Worker, migrations and integration tests |
| [dashboard/](dashboard/) | Independently deployed analytics Worker and tests |
| [tests/](tests/), [.github/workflows/](.github/workflows/) | Offline regressions and separate CI/monitoring workflows |

[Operations](CLOUD_SETUP.md) · [Historical ingestion](docs/CLOUDFLARE_DURABLE_HISTORY.md) · [Dashboard](dashboard/README.md) · [Security](SECURITY.md) · [Limitations and extensions](docs/ROADMAP.md)

## Operational limits

Live polling requires permission and the `GMU_POLLING_APPROVED` gate. Dispatch and execution are best-effort: delayed or missed runs and brief openings between samples cannot be reconstructed. Actions cache loss can reset duplicate suppression. An ephemeral runner can lose observations before D1 acknowledges them; capture is not lossless and delivery is not exactly-once.

A Git push does not deploy either Worker. Historical data is retained without automatic pruning. The term is explicit, and the dashboard is not a multi-term browser. No production recovery-time or recovery-point guarantee has been established.

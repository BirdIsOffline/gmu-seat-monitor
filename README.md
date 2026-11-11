# Campus Seat Watch
**Read-only university course availability monitoring, historical analytics and iPhone notifications**

> **Independent student engineering project · Python · GitHub Actions · Cloudflare Workers/D1 · JavaScript · SQLite · HTTP/JSON**

Campus Seat Watch is a deployed, read-only course availability monitoring system. An external scheduler dispatches a GitHub Actions workflow, which checks the public course catalog for two explicitly configured sections, parses reservation information, compares observed seat availability with the previous snapshot, and sends a Bark notification to an iPhone **only when potentially available seats increase**. A separate, authenticated Cloudflare Worker stores public section-level observations in D1; a second, token-protected Worker presents historical charts, last-check freshness and interval metrics.

The current live configuration targets **George Mason University Spring 2027**: IT 304 DL1 (CRN 17582) and IT 341 DL1 (CRN 17593). This is **not a registration bot**: no student login, authentication bypass, automatic enrollment, add/drop/swap, or access to personal student records. A positive total seat count does **not** guarantee unreserved eligibility. The project is not affiliated with or endorsed by GMU. Automated access requires institutional permission; do not enable or change polling without confirming authorization.

## What it does

- **Exact course matching:** resolves the requested semester from the published catalog and verifies the subject, course number, section and CRN.
- **Conservative alerting:** distinguishes known unreserved seats from reserved-only seats; a missing reservation summary is explicitly treated as *possible* availability, not guaranteed eligibility. Sends a notification for a positive change in potentially available seats and suppresses no-change, decrease and reserved-only events.
- **Serverless operations:** a separately configured five-minute dispatch schedule initiates GitHub Actions runs; the runner keeps a public-only previous snapshot in a GitHub Actions cache. Test workflows never make GMU requests.
- **Durable historical records:** an authenticated ingestion Worker atomically stores immutable, public section-level check and observation records in Cloudflare D1, with chronological opening-event logic. History submission is isolated from monitoring success and Bark decision logic.
- **Private analytics dashboard:** an independent read-only Worker queries fixed, parameterized D1 SELECT statements, offers 24-hour and 7-day charts, check-success/cadence metrics, last-recorded-check freshness, and an explicit refresh control. History API requests require a **separate** read-token secret.
- **Automated verification:** Python unit/integration tests and Worker tests run in GitHub Actions CI, using mocked endpoints and synthetic data.

## System design

```text
External scheduler (separately configured; best-effort)
      |
      | scoped GitHub workflow_dispatch, mode=check
      v
GitHub Actions check-seats job
      |
      +--> Python catalog adapter --> public course JSON (read-only)
      |             |
      |             v
      |      exact section/CRN match
      |             |
      |             v
      |      reservation parsing + previous-snapshot comparison
      |             |
      |             +--> candidate seat increase --> Bark push --> iPhone
      |             |
      |             +--> public-only current snapshot --> Actions cache
      |
      +--> independent history capture/submit (explicitly enabled)
                    |
                    v
          authenticated ingestion Worker
                    |
                    v
               Cloudflare D1
                    |
                    v
        separate token-protected dashboard Worker
                    |
                    v
         browser charts + monitoring freshness
```

No enrollment requests are issued. The dashboard only reads stored records; it does not invoke GMU searches, Bark, the ingest endpoint, or scheduled workflows.

## Verified operational snapshot

**Observed on November 10, 2026 (not a long-term SLA):** 182 stored checks / 364 course observations, 100% success **among stored checks**, a measured average interval of 5.00 minutes, a longest recorded gap of 5.28 minutes, and zero recorded gaps above 10 minutes. Both courses were present. A real Bark **test** arrived on the operator's iPhone, and an independent live monitoring run reported 2/2 successful checks with acknowledged historical ingestion. This evidence does not establish that every scheduled trigger ran, that Bark would deliver every future alert, or that a real seat-opening alert has occurred. The dashboard will show different live counts as monitoring continues.

The seat-history charts have captured a **total seat count change** for IT 341 from 3 to 2; unreserved availability remained unknown. No qualifying historical opening event was reported at the time of that snapshot.

## Quick offline demonstration

Run locally with Python 3.12+:

```bash
python -m pip install -r requirements.txt
python monitor.py --demo
python demo_history.py
python demo_live_history.py
python -m unittest discover -s tests -v
```

The demos use synthetic course observations and/or mocked HTTP interactions; **they make no requests to GMU or Bark and cannot change enrollment**. The test suite is likewise offline. To run the dashboard's Node tests:

```bash
node --check dashboard/worker.js
node --test dashboard/worker.test.mjs
```

An optional fully synthetic Worker/D1 demo is documented in [Cloudflare durable-history architecture](docs/CLOUDFLARE_DURABLE_HISTORY.md). The real dashboard is private and should not be presented as anonymously accessible; show screenshots with secrets and account details hidden, or use the offline demonstration.

## Deployment and security

- Production monitoring workflow: [`.github/workflows/seat-monitor.yml`](.github/workflows/seat-monitor.yml); separate CI: [`.github/workflows/ci.yml`](.github/workflows/ci.yml).
- Monitoring requires explicit approval through `GMU_POLLING_APPROVED`; the external scheduler and GitHub dispatch token are configured outside this repository.
- Bark device key: `BARK_DEVICE_KEY` GitHub Actions **secret**.
- History ingestion: dedicated Worker, D1 binding, opt-in variables and ingestion credentials described in [live history adapter](docs/LIVE_HISTORY_ADAPTER.md). Do not copy ingestion credentials to the dashboard.
- Dashboard: separate `gmu-seat-history-dashboard` Worker, D1 binding `DB`, and the **separate** `DASHBOARD_READ_TOKEN` secret. See [dashboard deployment and rollback](dashboard/DEPLOYMENT.md).
- Never commit device keys, access tokens, cookies, student identifiers or enrollment information. See [security guidance](SECURITY.md).

**Current manual deployment caveat:** updating GitHub code does **not** automatically redeploy the Cloudflare dashboard. Deploy Worker revisions separately, verify them and preserve the existing ingestion Worker.

## What happens after registration ends?

The currently deployed monitor is **intentionally semester-specific**. The string `Spring 2027`, both CRNs and dashboard course labels are presently hardcoded; they do **not** roll over automatically. A new term may have new CRNs, and old course matches can stop resolving. **Do not leave scheduled polling indefinitely enabled after it is no longer needed** or simply swap CRNs without verifying authorization, accurate matching and state isolation. Historic data is stored with `term` and `crn` so it can remain available, but the current dashboard displays only the configured pair.

Follow the [term rollover / archive checklist](docs/TERM_LIFECYCLE.md) for a safe manual pause, archived demo, new-term configuration plan and independent verification. An automatic term-switcher is **not** implemented. Archived public section-level data and synthetic demos provide lasting portfolio evidence without continued GMU traffic.

## Engineering tradeoffs and limitations

| Design choice | Benefit | Limitation |
|---|---|---|
| GitHub Actions + external dispatch | Hosted automation with no persistent app server | Jobs/dispatches are best-effort; gaps between stored checks cannot prove every trigger ran |
| Cached last snapshot | Simple change detection across ephemeral runners | Cache eviction can lose previous state and cause a one-time repeat candidate alert |
| Separate historical ingestion | Keeps monitoring/Bark independent of D1 availability | Original observations may be lost if the ephemeral runner is interrupted before durable submission; an offline disk outbox does not fix this on a short-lived runner |
| D1 transaction + timeline logic | Durable public observations, deterministic chronological event maintenance | Ingestion doesn't guarantee every scheduled check is captured |
| Read token + fixed queries | Private read API and bounded request shapes | D1 binding itself is not a database-enforced read-only role; access limits and token rotation need operational care |
| Five-minute sampled observations | Useful long-running trends | Short-lived seat openings between checks can be missed |
| Explicit unreserved parsing | Avoids reserved-only false positives | Catalog information may be incomplete; eligibility always requires official registration confirmation |

## Explore the code

| Path | Purpose |
|---|---|
| [`monitor.py`](monitor.py) | GMU catalog lookup, seat classification, change detection, Bark delivery and offline demo |
| [`health_runner.py`](health_runner.py) | Monitoring health report |
| [`cloud_history.py`](cloud_history.py) | Public-only history payload validation |
| [`history_capture.py`](history_capture.py), [`history_submit.py`](history_submit.py) | Bounded, isolated historical handoff |
| [`cloudflare/`](cloudflare/) | Ingest Worker, migrations and offline tests |
| [`dashboard/worker.js`](dashboard/worker.js) | Private read-only dashboard Worker |
| [`tests/`](tests/) | Offline Python tests, mocked network and failure-path checks |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Component design and engineering decisions |
| [`docs/TERM_LIFECYCLE.md`](docs/TERM_LIFECYCLE.md) | Safe semester transition and archival process |

## Project scope

This project observes **published, section-level data** and sends informational alerts for potential openings. It **does not** register a student, provide enrollment guarantees, bypass restrictions or replace official GMU guidance. It remains an independent student-built portfolio project.

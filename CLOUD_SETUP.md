# GMU Seat Monitor — cloud configuration

The monitor uses a GitHub Actions workflow, an externally configured dispatch schedule, Bark for iPhone notifications, and optional Cloudflare historical storage.

The current source configuration checks **IT 341 DL1 (CRN 17593)** and **IT 343 DL1 (CRN 17599), DL2 (CRN 20981), and DL3 (CRN 21051)** in the public Spring 2027 catalog. **IT 304 DL1 (CRN 17582) is retired**: its cached snapshots, historical observations and dashboard history remain available, but it is excluded from live polling and seat notifications. The monitor does not perform registration actions or use student credentials.

## Prerequisites

- GitHub repository with Actions enabled
- Python dependencies defined in `requirements.txt`
- Bark device key stored as a GitHub Actions secret
- Explicit permission for scheduled automated catalog access
- Optional, separately configured Cloudflare ingestion Worker and dashboard

## GitHub Actions

The operational workflow is [`.github/workflows/seat-monitor.yml`](.github/workflows/seat-monitor.yml). Offline CI is [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

1. Add `BARK_DEVICE_KEY` under **Settings → Secrets and variables → Actions → Secrets**. Never commit the key to the repository.
2. In **Actions**, open **GMU seat monitor** and run its `test` mode to check Bark delivery without querying the university catalog.
3. Confirm automated public-catalog access and polling frequency are permitted before enabling live checks.
4. Set the repository variable `GMU_POLLING_APPROVED=true` only when authorized.
5. Use `check` mode to execute a single authorized catalog check.
6. Configure external dispatch separately to trigger the operational workflow. The reported operational cadence is five minutes; GitHub execution times are best-effort.

To pause polling, disable external dispatch and set `GMU_POLLING_APPROVED=false`, or disable the operational workflow.

## Monitoring all three IT 343 sections

The source configuration now has **four active targets and one retired course**.
Each target declares `number`, `section`, `name` and `status` in `monitor.py`;
`get_section()` verifies term, subject, number, CRN and that exact section.
Live capture derives the same section from the configured identity rather than
assuming DL1. The dashboard mirrors these fields, guarded by an offline parity test.

All three IT 343 sections use the unchanged alert policy. Comparison state, observation identity and event baselines are keyed by CRN (with term in history), never by course number alone. Notifications include the section and CRN. No IT 343 section is retired automatically; wait for explicit registration confirmation and review the status change. The supplied 16-total / 0-unreserved /
16-reserved snapshot is a reference and synthetic test fixture only. Do not seed
`seat_state.json`, D1, or replay payloads from it. Each newly added section stays missing until its own real completed lookup is captured. Until all four active targets have valid observations, active-course freshness remains `no_data`; recent observations from other sections cannot hide the missing one.

After separate release approval, publish the monitor/capture changes and require
full Ubuntu CI, then separately deploy the updated dashboard using its existing
binding and read token. The ingestion Worker and schema already support all three sections;
**no ingestion deployment, migration, new credential or polling-frequency change**
is required. Preserve all existing IT 304, IT 341 and IT 343 records and the existing approval/history
opt-ins. Do not manually dispatch monitoring or insert reference records as a
shortcut to verification.

## Course retirement and release

- `monitor.py` defines each course's `status`: `active` enables the existing lookup
  and alert path; `retired` skips that path and preserves the saved snapshot.
  Unknown or missing status fails validation before polling.
- Keep the standalone `COURSES` list in `dashboard/worker.js` aligned. CI checks
  parity; no new build system or external configuration service is required.
- Health reports mark retired courses as `retired`, not missing or failed. The
  successful-check denominator includes active courses only. If all courses are
  retired, the monitor exits without catalog requests or history capture.
- Status is current configuration, not a historical fact added to old payloads.
  Ingestion validation, original UUIDs, retry bytes and event calculations remain
  unchanged for existing courses; the existing generic historical validators also accept IT 343 DL1, DL2 and DL3. Do not delete D1 rows, cache entries or backups.
- Dashboard charts/events retain all five configured course/section histories in the selected window.
  Latest stored snapshots also remain visible when older than that window.
  Retired totals are labelled history-only, not current availability. Freshness
  uses the oldest latest active-course observation across all history; fresh
  retired-only records cannot conceal missing or stale active observations.
  Check success and cadence still describe all stored checks in the window.
- Reactivating a course requires an explicit reviewed status change in both
  configurations. It never grants polling permission; `GMU_POLLING_APPROVED`
  remains mandatory for live workflow checks. Retained comparison state is reused;
  missed openings during retirement cannot be reconstructed by polling.

After review, publish the monitor/configuration and tests to the workflow's
configured branch and require successful Ubuntu CI. Confirm no older workflow
execution is still using the previous target configuration. Keep the existing
external cadence, approval gate, Bark secret and history opt-ins unchanged.
Separately deploy only the reviewed dashboard source using its existing DB
binding and read token; no ingestion Worker deployment or schema migration is
needed. Then inspect an authorized check for one IT 341 lookup and three independently matched IT 343 lookups, one retired
health row, unchanged IT 304 history, and correct dashboard freshness. No live
check or Cloudflare deployment is authorized merely by editing these files.

If dashboard rollback is necessary, preserve D1 and the monitor's retired status.
Do not revert to an older target configuration just to restore presentation.

## Historical ingestion and dashboard

History recording uses separate authenticated Cloudflare services and is not required for Bark notifications.

- [Cloudflare historical ingestion](docs/CLOUDFLARE_DURABLE_HISTORY.md)
- [Live history adapter](docs/LIVE_HISTORY_ADAPTER.md)
- [Historical dashboard deployment](dashboard/DEPLOYMENT.md)

Keep ingestion credentials separate from the dashboard read token. GitHub source changes do not automatically redeploy either Cloudflare Worker.

## Actionable Bark alert settings

Qualifying openings for all four active targets request `sound=alarm`, `call=1`,
`level=critical`, and `volume=10`. The `call` and `volume` values are serialized
as strings following the [Bark API V2 contract](https://github.com/Finb/bark-server/blob/master/docs/API_V2.md).
Reservation-aware decisions, duplicate suppression, titles, message bodies,
request timeout and delivery-error handling are unchanged. Retired IT 304 never
enters the live alert path. The explicit phone-test mode retains its existing
`timeSensitive` defaults and does not request these actionable-alert options.

Phone playback has not been tested by these offline checks. Critical behavior
requires the appropriate Bark/iOS permission; sound behavior depends on device
settings and app support. See the [official parameters](https://github.com/Finb/Bark/blob/master/docs/en-us/params.md).
Do not send a live test without separate authorization.

## Operational constraints

- A reported seat increase is informational and does not prove an individual student is eligible to enroll.
- Scheduled jobs may be delayed or skipped. Short-lived availability changes between checks can be missed.
- GitHub Actions snapshot cache eviction can cause an alert to be repeated after state is lost.
- Public endpoints may enforce usage limits. Respect university access policies and stop automated requests if denied.
- Never commit Bark keys, GitHub tokens, Cloudflare secrets, cookies, or student records.

See [security](SECURITY.md) and [term lifecycle](docs/TERM_LIFECYCLE.md) for additional safeguards.

### References

- [GMU acceptable-use policy](https://universitypolicy.gmu.edu/policies/acceptable-use-of-technology/)
- [GitHub Actions workflow dispatch](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch)
- [Cloudflare Workers secrets](https://developers.cloudflare.com/workers/configuration/secrets/)

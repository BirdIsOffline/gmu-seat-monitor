# Deployment and operations

The deployed system has a GitHub Actions monitor, external five-minute dispatch, Bark delivery, a dedicated ingestion Worker/D1 database and a separate authenticated dashboard Worker. Checked-in Wrangler configuration is for local testing, not a production inventory.

## Configuration boundaries

| Setting | Location and purpose |
| --- | --- |
| `GMU_POLLING_APPROVED` | Repository variable; literal `true` permits live workflow checks |
| `BARK_DEVICE_KEY` | GitHub Actions secret for notification delivery |
| `GMU_HISTORY_CAPTURE_ENABLED` | Repository variable; literal `true` enables structured capture |
| `GMU_HISTORY_CAPTURE_DIR` | Per-run path under `runner.temp`, not a persistent archive |
| `GMU_HISTORY_ENABLED` | Repository variable; literal `true` enables separate submission |
| `GMU_HISTORY_PRODUCTION_ENDPOINT` | Repository variable containing the HTTPS ingestion `/v1/checks` endpoint |
| `GMU_HISTORY_PRODUCTION_TOKEN` | GitHub Actions secret available only to submission; matches the Worker's `INGEST_TOKEN` |
| `DB` | D1 binding on ingestion and dashboard Workers |
| `DASHBOARD_READ_TOKEN` | Separate dashboard Worker secret, held privately by the reader |

The dispatcher uses an expiring, repository-scoped GitHub credential outside source. Cloudflare management credentials are not required by the monitoring or history clients. Keep real endpoint, account and database mappings outside public examples.

## Polling and workflow behavior

[The monitoring workflow](.github/workflows/seat-monitor.yml) is dispatched with `mode=check` by the external scheduler. It checks the polling gate, restores state, runs `health_runner.py`, validates and caches public seat state, then submits captured observations when both history opt-ins are enabled. History failure has a separate outcome and cannot replace monitoring health.

The cadence is five minutes. Dispatch acceptance does not guarantee a job starts or finishes on time. The concurrency group uses `cancel-in-progress: false`; GitHub pending-run handling is not a durable queue of every request. The health runner has a 240-second monitor timeout, the job a five-minute budget, and submission a separate 20-second deadline within the remaining budget.

To pause polling, stop external dispatch and set `GMU_POLLING_APPROVED=false`, or disable the monitoring workflow. Preserve state and history. Offline CI is separate and does not need monitoring credentials.

## Course retirement and release

The [target table](README.md#section-identity-and-lifecycle) has four active sections and one retired section. Matching requires the exact term, subject, number, section and CRN. CRNs distinguish sections with the same course number.

Maintain statuses in `monitor.py` and `dashboard/worker.js`; CI verifies parity. Retirement excludes lookups and alerts but preserves state, observations, delayed historical ingestion and events. Invalid status fails validation. All-retired configuration exits without catalog lookups or capture.

Release source through a pull request and successful Ubuntu CI. Deploy presentation changes independently using the [dashboard runbook](dashboard/DEPLOYMENT.md). Adding a section under the current protocol needs no ingestion Worker change, migration, credential rotation or cadence change. Verify normal scheduled observations; do not seed state or history from examples.

Freshness uses the oldest latest active observation. A section without real observations remains missing; other samples cannot make the aggregate healthy. Retired sections are excluded. Dashboard rollback must preserve the database and monitoring lifecycle configuration.

## Bark notification contract

Qualifying candidate increases request `sound=alarm`, `call=1`, `level=critical` and `volume=10`; `call` and `volume` are strings. Titles include section and CRN. Reservation filtering and duplicate suppression precede delivery. The function makes one request with a 15-second timeout and no automatic retry; state persistence can suppress another attempt for a rejected push.

Workflow `mode=test` uses the ordinary `timeSensitive` phone-test path rather than actionable alarm settings. Phone tests send real notifications and are deliberate operations, not CI checks. The labelled actionable-alarm test was accepted by Bark, and the operator confirmed successful playback with approximately 20 seconds of persistent ringing. The test did not represent a genuine seat opening. Ringing duration is an observed result, not a configured timing guarantee. Critical playback still depends on Bark/iOS support, permissions and settings.

[Official Bark parameters](https://github.com/Finb/Bark/blob/master/docs/en-us/params.md) · [API contract](https://github.com/Finb/bark-server/blob/master/docs/API_V2.md)

## Historical delivery and recovery

Capture and submission use dedicated opt-ins and production configuration with no staging fallback. Delivery retries submit the sealed UUID/body and never rerun catalog lookups or Bark. A timeout is unconfirmed: D1 may have committed. Rerunning the monitoring job is not a delivery retry.

Disable `GMU_HISTORY_ENABLED` to pause submission without changing ordinary monitoring. Preserve sealed bodies for identical replay. Protect backups and exports; temporary Actions disk is not a durable archive. The [handoff](docs/LIVE_HISTORY_ADAPTER.md), [D1 recovery](docs/CLOUDFLARE_DURABLE_HISTORY.md#failures-and-recovery) and [optional outbox](docs/HISTORY_RELIABILITY.md) have different durability boundaries.

## Security and deployment separation

Ingestion and dashboard use separate credentials. Use HTTPS and secret stores; never put credentials in command arguments, URLs, commits or screenshots. Source publication does not deploy Cloudflare automatically. Resource mappings and previous versions belong in private operator records.

Permission for catalog access and the polling gate remain necessary regardless of CI. Stop on denied access or rate limiting. See [security](SECURITY.md) and [term lifecycle](docs/TERM_LIFECYCLE.md).

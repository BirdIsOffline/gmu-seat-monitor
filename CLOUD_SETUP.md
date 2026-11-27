# Deployment and operations

This guide covers live polling, notifications and historical delivery. The [architecture](docs/ARCHITECTURE.md) describes the deployed components; checked-in Wrangler configuration is for local testing.

## Configuration boundaries

| Setting | Location and purpose |
| --- | --- |
| `GMU_POLLING_APPROVED` | Repository variable; literal `true` permits live workflow checks |
| `BARK_DEVICE_KEY` | GitHub Actions secret for notification delivery |
| `GMU_HISTORY_CAPTURE_ENABLED` | Repository variable; literal `true` enables structured capture |
| `GMU_HISTORY_CAPTURE_DIR` | Per-run path under `runner.temp`, not a persistent archive |
| `GMU_HISTORY_ENABLED` | Repository variable; literal `true` enables separate submission |
| `GMU_HISTORY_PRODUCTION_ENDPOINT` | GitHub Actions secret containing the unchanged HTTPS ingestion `/v1/checks` endpoint; create before publishing the workflow change |
| `GMU_HISTORY_PRODUCTION_TOKEN` | GitHub Actions secret available only to submission; matches the Worker's `INGEST_TOKEN` |
| `DB` | D1 binding on ingestion and dashboard Workers |
| `DASHBOARD_READ_TOKEN` | Separate dashboard Worker secret, held privately by the reader |

The dispatcher uses an expiring, repository-scoped GitHub credential outside source. Cloudflare management credentials are not required by the monitoring or history clients. Keep real endpoint, account and database mappings outside public examples.

## Endpoint privacy and rollout

The ingestion endpoint belongs in an Actions secret alongside its separate token. A repository variable can appear in the runner's environment listing before Python starts. Masking it inside that same step is too late. The submitter continues to receive the same environment variable; its HTTPS destination, authentication, receipts and payload contract do not change.

For a deployment that currently uses a repository variable:

1. Copy the existing variable value exactly into a repository-level Actions secret named `GMU_HISTORY_PRODUCTION_ENDPOINT`, using a private authenticated interface or protected standard input. Do not echo it, place it in command arguments, or rotate the ingestion token. Verify the secret name exists; GitHub does not return secret values for readback.
2. Keep the variable during rollout so the current main workflow and any in-flight jobs retain their input. Merge the workflow change only after the secret exists and full CI passes. Do not introduce a variable fallback.
3. Observe the next naturally scheduled main run. Require five successful course checks, an `INGESTED` outcome and a masked endpoint and token in setup logs. Verify reports contain only fixed outcomes and counts. Do not dispatch extra monitoring or notifications.
4. Remove the superseded variable only after verification and after all queued and running jobs using older workflow revisions finish. Keep the secret, endpoint and ingestion credentials unchanged. Old workflow revisions referencing the variable are no longer suitable for execution.

If verification fails, preserve both inputs and inspect the command result before retrying anything. Repair secret provisioning or prepare a protected corrective PR; do not restore an unmasked variable reference. Missing configuration fails historical submission without replacing monitoring health. The handoff exists only on temporary runner storage, so an interruption can lose unsubmitted facts.

Secret masking does not erase existing logs. GitHub deletes all logs for a run, including other files in an affected archive; individual lines cannot be selectively erased. Keep a private redacted exposure manifest, then obtain authorization to delete only affected **run log archives**, preserving run records, conclusions and unaffected run archives. Refresh the manifest after rollout because the old workflow can keep producing exposed logs until replaced. Verify affected archives are inaccessible and run records remain available. Inspect separately retained summaries or artifacts before claiming cleanup is complete. GitHub documents [secret handling](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets) and the distinct [workflow-run log API](https://docs.github.com/en/rest/actions/workflow-runs#delete-workflow-run-logs).

## Polling and workflow behavior

[The monitoring workflow](.github/workflows/seat-monitor.yml) is dispatched with `mode=check` by the external scheduler. It checks the polling gate, restores state, runs `health_runner.py`, validates and caches public seat state, then submits captured observations when both history opt-ins are enabled. History failure has a separate outcome and cannot replace monitoring health.

The cadence is five minutes. Dispatch acceptance does not guarantee a job starts or finishes on time. The concurrency group uses `cancel-in-progress: false`; GitHub pending-run handling is not a durable queue of every request. The health runner has a 240-second monitor timeout, the job a five-minute budget, and submission a separate 20-second deadline within the remaining budget.

To pause polling, stop external dispatch and set `GMU_POLLING_APPROVED=false`, or disable the monitoring workflow. Preserve state and history. Offline CI is separate and does not need monitoring credentials.

## Course retirement and release

All five active sections in the [target table](README.md#section-identity-and-lifecycle) are deployed and verified; IT 304 remains retired. Matching requires the exact term, subject, number, section and CRN.

Maintain statuses in `monitor.py` and `dashboard/worker.js`; CI verifies parity. Retirement excludes lookups and alerts but preserves state, observations, delayed historical ingestion and events. Invalid status fails validation. All-retired configuration exits without catalog lookups or capture.

Release source through a pull request and successful Ubuntu CI. Deploy presentation changes independently using the [dashboard runbook](dashboard/DEPLOYMENT.md). Adding a section under the current protocol needs no ingestion Worker change, migration, credential rotation or cadence change. Verify normal scheduled observations; do not seed state or history from examples.

See the [dashboard guide](dashboard/README.md#freshness-and-recorded-coverage) for active-section freshness rules. Dashboard rollback must preserve the database and lifecycle configuration.

## Bark notification contract

Qualifying candidate increases request `sound=alarm`, `call=1`, `level=critical` and `volume=10`; `call` and `volume` are strings. Titles include section and CRN. Reservation filtering and duplicate suppression precede delivery. The function makes one request with a 15-second timeout and no automatic retry; state persistence can suppress another attempt for a rejected push.

Workflow `mode=test` sends an ordinary `timeSensitive` notification, so it does not exercise the actionable alarm settings. A separate, labelled live alarm test was accepted by Bark and rang for approximately 20 seconds on the iPhone. This was a controlled playback test, not a genuine seat opening. Ringing duration was observed, not configured; playback depends on Bark/iOS support, permissions and device settings.

[Official Bark parameters](https://github.com/Finb/Bark/blob/master/docs/en-us/params.md) · [API contract](https://github.com/Finb/bark-server/blob/master/docs/API_V2.md)

## Historical delivery and recovery

Capture and submission use dedicated opt-ins and production configuration with no staging fallback. Delivery retries submit the sealed UUID/body and never rerun catalog lookups or Bark. A timeout is unconfirmed: D1 may have committed. Rerunning the monitoring job is not a delivery retry.

Disable `GMU_HISTORY_ENABLED` to pause submission without changing ordinary monitoring. Preserve sealed bodies for identical replay. Protect backups and exports; temporary Actions disk is not a durable archive. The [handoff](docs/LIVE_HISTORY_ADAPTER.md), [D1 recovery](docs/CLOUDFLARE_DURABLE_HISTORY.md#failures-and-recovery) and [optional outbox](docs/HISTORY_RELIABILITY.md) have different durability boundaries.

## Security and deployment separation

Ingestion and dashboard use separate credentials over HTTPS. Follow the [credential handling guidance](SECURITY.md#credentials-and-data); keep resource mappings and previous deployment versions private. Source publication does not deploy Cloudflare automatically.

Permission for catalog access and the polling gate remain necessary regardless of CI. Stop on denied access or rate limiting. See [security](SECURITY.md) and [term lifecycle](docs/TERM_LIFECYCLE.md).

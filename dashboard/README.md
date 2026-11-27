# Authenticated historical dashboard

A standalone Cloudflare Worker serves fixed read-only queries over D1 `checks`, `observations` and `opening_events`. It is deployed independently from ingestion and creates no catalog traffic or Bark notifications.

## Authentication

The HTML shell is public. `/api/dashboard?range=24h` and `range=7d` require `DASHBOARD_READ_TOKEN`, separate from ingestion credentials. Missing/incorrect credentials return HTTP 401; missing server configuration fails closed. Valid credentials and the database binding permit HTTP 200.

The token stays in tab memory, is cleared from the input on login, and goes only to the same-origin API over HTTPS. Reloading requires reentry. No token belongs in source, URLs, persistent browser storage or screenshots.

The D1 binding is a normal binding, not an SQL-enforced read-only role. Fixed SELECTs and the absence of mutation routes enforce read-only application behavior. Per-user authorization, built-in rate limiting and automatic token rotation are not implemented.

## Section identities

Cards, legends and series use CRNs rather than course numbers. The [target table](../README.md#section-identity-and-lifecycle) has five active sections and one retired section. Each IT 343 section retains its own total, latest row, color and event identity. Equal values can overlap on the chart without merging data.

Retired history remains queryable. Latest snapshots can predate the selected chart window and are labelled history-only. Status is current configuration, separate from historical observations.

## Freshness and recorded coverage

Freshness uses each active section's latest observation across all history and server time. The aggregate follows the worst state and oldest age; retired records do not contribute.

| State | Meaning |
| --- | --- |
| Healthy | Every active section has a valid observation no more than 10 minutes old |
| Delayed | Limiting age is over 10 through 15 minutes |
| Stale | Limiting age exceeds 15 minutes |
| No data | An active section lacks an observation |
| Clock issue | An active timestamp is invalid or more than two minutes ahead |
| Inactive | Every section is retired |

The browser ages status once a minute but loads new records only on login, explicit refresh or range change. Success and cadence cover stored checks; they do not count missing dispatches or prove notifications. Cadence uses `LAG` only inside the selected window.

## Presentation and bounds

- The 24-hour and seven-day views retain active and retired identities.
- Unknown unreserved availability remains unknown, not zero or confirmed eligibility.
- The sample query returns at most 14,000 chronological rows. This covers the current five-minute workload, not unlimited export. Larger workloads require a revised query contract.
- Chart segments break across gaps over 15 minutes. A single observation has no connecting segment. Times are browser-local.
- Events are capped at 50 recent sampled increases. Delayed samples can revise edges; events never generate notifications.

## Verification

Regression tests cover authentication, fixed SQL, independent identities, missing/stale states, rendering and a 12,096-row seven-day fixture. Live checks of the preceding five-identity release confirmed both ranges, five series, retained baseline history and zero JavaScript errors. Browser-only omission replay verified missing-data display without production writes. No historical opening events existed, so live event-row rendering remains unexercised; the empty state and synthetic event rendering were checked.

Run `node --check dashboard/worker.js` and `node --test dashboard/worker.test.mjs`. These use mocks, disposable SQLite and a DOM substitute. See the [deployment runbook](DEPLOYMENT.md).

# Authenticated historical dashboard

A standalone Cloudflare Worker serves fixed read-only queries over D1 `checks`, `observations` and `opening_events`. It is deployed separately from ingestion and makes no catalog requests or Bark notifications.

## Authentication

The HTML shell is public. `/api/dashboard?range=24h` and `range=7d` require `DASHBOARD_READ_TOKEN`, separate from ingestion credentials. Missing/incorrect credentials return HTTP 401; missing server configuration fails closed. Valid credentials and the database binding permit HTTP 200.

The token stays in tab memory and is cleared from the input on login. It is sent only to the same-origin API over HTTPS; reloading requires reentry. Keep it out of source, URLs, persistent browser storage and screenshots.

The D1 binding is a normal binding, not an SQL-enforced read-only role. Fixed SELECTs and the absence of mutation routes enforce read-only application behavior. Per-user authorization, built-in rate limiting and automatic token rotation are not implemented.

## Section identities

Cards, legends and series use CRNs rather than course numbers. The deployed [target table](../README.md#section-identity-and-lifecycle) has five active sections and one retired section. Every section, including the three IT 343 sections, has its own total, latest row, color and event identity. Equal values can overlap on the chart without merging data.

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
- The sample query returns at most 14,000 chronological rows. This accommodates seven days of five-minute samples for all six identities; larger workloads need pagination or aggregation.
- Chart segments break across gaps over 15 minutes. A single observation has no connecting segment. Times are browser-local.
- Events are capped at 50 recent sampled increases. Delayed samples can revise edges; events never generate notifications.

## Verification

The latest [main CI run](https://github.com/BirdIsOffline/gmu-seat-monitor/actions/runs/45937201337) passed all 25 dashboard tests as part of the 252-test suite. Tests cover authentication, fixed SQL, section isolation, freshness, missing data, rendering and a 12,096-row seven-day fixture.

Live verification of the deployed six-identity release confirmed:

- Authenticated HTTP 200 and missing/invalid-token HTTP 401.
- Six separate cards, legends and latest-observation rows, with IT 304 retired and all five other sections active.
- Independent freshness inputs from every active section and correct aggregate status in both 24-hour and seven-day views.
- Both chart ranges, the 14,000-row query bound and no JavaScript exceptions.
- Exact preservation of all baseline checks and observations, including retired IT 304 history.
- Missing-data handling for each active section through browser-only omission replay, without database changes.

IT 342 had one observation at verification, so its card, legend and data were checked but it had no connecting chart segment. Multi-point rendering is covered by automated fixtures. No historical opening events existed at that check; the live empty state and synthetic event rendering passed, while live event-row rendering remains unverified.

Run `node --check dashboard/worker.js` and `node --test dashboard/worker.test.mjs`. These use mocks, disposable SQLite and a DOM substitute. See the [deployment runbook](DEPLOYMENT.md).

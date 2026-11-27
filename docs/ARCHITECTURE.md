# Architecture and design decisions

## Deployed data flow

An external dispatcher invokes GitHub Actions at a five-minute best-effort cadence, as shown in the [system diagram](SYSTEM_DIAGRAM.md). The polling gate controls live checks; offline CI needs no monitoring credentials.

`monitor.py` resolves the term and matches exact subject, number, section and CRN. State is keyed by CRN for the current term and cached between jobs. History uses `(term, crn)` and copies completed lookups without another catalog request.

`health_runner.py` supervises monitoring and reports lookup health. Alerts and state saves precede capture for each course. A separate deadline-controlled process submits immutable sanitized checks to the ingestion Worker. D1 commits observations and chronological event repairs atomically. The dashboard reads the same history using independent authentication.

## Decisions and tradeoffs

| Decision | Reason and consequence |
| --- | --- |
| Exact section and CRN | Multiple sections share course numbers; title matching cannot separate state and alerts |
| Explicit active/retired status | Stop polling without deleting facts; configuration parity is tested |
| Reservation-aware candidates | Recognized counts must agree and be bounded; ambiguous metadata is non-actionable, while absent metadata remains uncertain |
| Change-based alerts | Suppress unchanged/decreasing candidates while state survives; cold starts can notify on already-visible potential availability |
| Separate history process | Capture/delivery failure cannot replace monitoring health or retry phone alerts |
| Direct atomic D1 ingestion | Bounded projection requires no pending inbox, scheduled drain or persistent host |
| Immutable UUID/body retries | Lost acknowledgements can be retried without duplicate observations; changed facts under the same UUID fail |
| Separate dashboard/token | Read access does not disclose ingestion credentials or expose mutations; D1 binding itself is not read-only |
| Manual Worker deployment | Git publication and offline CI do not implicitly change hosted services |

The [D1 design](CLOUDFLARE_DURABLE_HISTORY.md) explains immutable observations and revisable event edges. The deployed [handoff](LIVE_HISTORY_ADAPTER.md) uses temporary runner storage; the [optional outbox](HISTORY_RELIABILITY.md) requires persistent POSIX storage.

Cached state must be a JSON object. Loading preserves valid active and retired entries; a missing, unreadable or non-object cache uses the existing cold-start behavior. A missing or malformed per-course entry also has no prior actionable count. This is comparison state, not a durable history source or proof of a newly opened seat.

Live alerts and historical capture share reservation parsing: only recognized count keys or supported text patterns contribute, every recognized count must agree, and counts are bounded to 1,000,000. Capture reduces inputs to public counts, absent metadata or a fixed unknown marker. Direct historical validation and the ingestion Worker still enforce their own schema and bounds.

## Failure boundaries

| Failure | Current behavior and limit |
| --- | --- |
| Catalog error | Lookup fails; completed and failed sections stay distinguishable |
| Missing/delayed dispatch | Coverage has gaps; transient openings cannot be reconstructed |
| Missing, unreadable or invalid-shaped cache | Starts with an empty baseline; already-visible potential availability can notify again |
| Bark error | Returns false without automatic retry; lookup health does not verify delivery |
| Runner ends before D1 receipt | Unsubmitted facts may be permanently lost |
| Commit with lost acknowledgement | Original UUID/body retry deduplicates accepted effects |
| Delayed sample | Transaction repairs historical edges without issuing notifications |
| Bad identity, tied time or conflicting UUID | Entire historical check is rejected |
| Missing active observation | Aggregate freshness remains missing despite other recent records |
| Database restore | Recovery depends on surviving recovery points/source; no production RPO/RTO is established |

## Security and scope

No student credentials or registration actions are part of this system. History contains public identities, counts, timestamps and fixed health categories. See [security](../SECURITY.md).

The monitor is single-term. Its CRN-only cache and dashboard queries must be isolated or extended before identifiers are reused in another term. A term column in the database alone does not make the UI a multi-term browser.

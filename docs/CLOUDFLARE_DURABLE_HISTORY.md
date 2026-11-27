# D1 ingestion design and recovery

The deployed ingestion Worker accepts sanitized original check batches and commits observations and chronological opening-event repairs in one D1 transaction. It has no pending inbox, scheduled drain, persistent application host or notification side effects. [The protocol reference](CLOUD_HISTORY.md) specifies HTTP and validation; [the handoff](LIVE_HISTORY_ADAPTER.md) specifies source capture and process deadlines.

## Design decision

| Approach | Tradeoff |
| --- | --- |
| Strict direct append | Simple, but rejects a new older sample after a later one commits |
| Pending inbox and scheduled projection | Can accept during projection-specific failures, but adds queue state, claims, poison handling, scheduling and checkpoints; cannot accept during a D1 outage |
| Selected: atomic receipt and chronological repair | Accepts delayed samples with bounded adjacent-edge work; projection failure prevents receipt and historical events can be revised |

Projection compares only adjacent samples. An additional independently operated processor was not justified for this workload. An inbox in the same destination would not close the loss window before an ephemeral source reaches Cloudflare. Reconsider it if projection becomes expensive, independently available or externally effectful.

Chronological processing means the committed projection equals processing all currently stored observations in timestamp order for each `(term, crn)`. It does not mean requests arrive in that order. There is no producer watermark proving every earlier sample has arrived.

## Transaction and receipt boundary

1. Capture assigns a random UUID4 and UTC cycle-start timestamp once. Successful lookups contribute public observations; failures do not invent missing rows. Retries retain original identity, status, duration and bytes.
2. `prepare_check` sanitizes public fields into canonical JSON. The Worker authenticates, bounds the body, validates it and computes a canonical SHA-256 binding the UUID to its facts. Semantically reordered retries normalize to the same hash; altered facts cause conflict.
3. One `DB.batch()` contains a hash-checked check upsert and a JSON-rowset observation insert, with ten bound parameters. Existing check/course rows are excluded. Triggers enforce identity, timestamp and immutability constraints and repair adjacent edges under D1's write transaction. There is no handler-side read/compare/write race.
4. Receipt occurs when the complete transaction commits. A successful HTTP acknowledgement contains `INGESTED` and the matching check UUID only after batch success. Parsing, sending or local preparation is not durable acceptance.
5. A missing acknowledgement is unconfirmed. D1 may already have committed; retry original UUID/body. If the producer dies after commit, accepted observations remain without a later processing step.

`checks.received_at_utc` records first receipt, separately from sample time, and does not change on retry. It remains null for legacy rows whose receipt time is unknown.

New observations have deterministic UUID5 identities: check UUID4 as namespace and ASCII `term:crn` as name. This SHA-1 use constructs an identity, not an authentication primitive. Legacy observations received UUID4 mappings during migration; retain those mappings in exports. Integer observation/event IDs are destination-local, not cross-database replay identities.

## Delayed samples and event semantics

Inserting `x` between `p` and `n` atomically replaces edge `p -> n` with `p -> x` and `x -> n`. Each edge produces an event only when known totals permit a candidate-count comparison and that count increases. Missing totals break inference; reserved-only increases do not create openings. The first sample has no preceding edge.

A delayed intermediate sample can split one observed increase into two. An intervening unknown can remove an earlier inference. Original observations remain immutable. Events are revisable historical projections, never instructions to send Bark alerts. Use the destination observation UUID for durable event identity and refresh affected windows; integer event IDs are not an append-only feed.

Distinct checks at the same timestamp for one term/CRN conflict even if counts match. Ties across different courses are valid. Changing subject, course number or section for an existing term/CRN also conflicts. One conflicting row rolls back the entire multi-course check. Diagnose retained facts rather than changing timestamps or UUIDs to bypass rejection.

There is no lateness cutoff or full timeline rebuild during ingestion. Indexed predecessor/successor lookups bound work per observation. Missing observations do not block later direct submissions. Gaps and failed checks remain coverage evidence, not synthetic zero-seat samples. Backfill can use retained original facts, never a current lookup pretending to be a past observation.

## Bounds and capacity

The protocol permits 64 KiB and 50 distinct observations per check. The bulk insert remains two D1 API statements; per-observation trigger work is at most one edge deletion and two insertions. This input cap is not a guarantee that every request fits an account's CPU budget.

Four active targets at a five-minute cadence imply about 288 checks and up to 1,152 observations per day, before failures and retries. Approximately 1,440 base rows/day excludes events, indexes, receipt/upsert work and repairs and is not a billable-write estimate. Storage grows without automatic pruning.

Measure request CPU, rows read/written and retained storage. Consult current [Workers limits](https://developers.cloudflare.com/workers/platform/limits/), [D1 limits](https://developers.cloudflare.com/d1/platform/limits/) and [D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/); no account entitlement or free-service guarantee is asserted here. Retention must preserve replay identities and recovery baselines.

## Failures and recovery

| Failure | Interpretation and response |
| --- | --- |
| D1/constraint/projection error | Transaction rolls back; resolve fixed conflict/storage category and retain original facts |
| Commit followed by lost response | Unconfirmed; retry identical UUID/body |
| Source loss before receipt | Potential permanent loss; D1 cannot recover facts it never received |
| Missing scheduler run or failed lookup | No successful observation exists to replay |
| Destination restore | Recovery point may omit previously acknowledged rows; local receipts can overstate restored contents |
| Missing original IDs or incomplete export | Full reconstruction is not guaranteed; do not fabricate replacements |

For restore/replay, pause historical writers and preserve the affected database, migration ledger, Worker version and original exports. Restore into an isolated target, replay surviving original bodies, and verify UUID mappings, identities, timeline order, gaps, statuses and event edges before cutover. Replaying in time order reduces repair work; the current direct endpoint also accepts retained older facts.

Production RPO/RTO has not been established. Local Miniflare recovery, restart and replay tests are not a production backup or live recovery guarantee. Historical staging restore reports are distinct from independently verified production recovery. There is no independent durable source in the deployed Actions topology; lossless capture and exactly-once delivery are not provided.

## Migration history and compatibility

`0001_history.sql` defines checks, observations, event constraints, hash-based replay protection and strict append ordering. `0002_ordered_timeline.sql` adds first-receipt time, observation UUIDs, a unique chronological key and immutable-update guards, then replaces strict ordering/event insertion with bounded neighbor repair. It preserves existing observations and replay IDs; no pending tables are introduced.

Do not edit an applied migration. Before a future schema change, inventory actual schema/ledger and preserve a consistent export and matching Worker revision. Pause historical writers, test populated upgrades, conflicts, foreign-key integrity, delayed input and recovery on an isolated target, then coordinate schema and Worker cutover. The checked-in Wrangler file is local-only and must not be assumed to describe production.

Rollback across the 0002 boundary requires a compatible database/code pair. An old Worker omits observation UUIDs and has different ordering semantics; simply reverting code against the new schema is unsafe. Preserve newly accepted facts, favor a forward fix, or restore the matched snapshot and Worker on an isolated target. An older strict endpoint cannot directly replay interleaved late facts. Submission may remain paused while ordinary monitoring continues.

## Operational evidence

Monitor fixed ingestion conflict/unavailable categories, authentication/conflict/rate/storage failures, exhausted retries, receipt age, sample lag, active-course gaps and D1 resource usage. Historical summaries are separate from lookup health; absence of errors is not proof that submission ran. Do not route historical errors through seat-opening notifications.

Read-only operator queries use bound values and restricted access:

```sql
SELECT received_at_utc FROM checks
WHERE received_at_utc IS NOT NULL ORDER BY received_at_utc DESC LIMIT 1;
SELECT status, count(*) FROM checks WHERE received_at_utc >= ? GROUP BY status;
SELECT observation_uuid, observed_at_utc, classification, potential_available
FROM observations WHERE term = ? AND crn = ?
ORDER BY observed_at_utc DESC LIMIT 1;
```

Tests cover authentication, bounds, concurrent writes, changed/tied IDs, shuffled arrival against an independent SQLite reference, UUID parity, interrupted neighbor repair, populated migration, persistent local restart and replay. [Local validation](LOCAL_DEMO.md) is the command reference; the [README](../README.md#verified-results) records the current hosted result.

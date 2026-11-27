# Optional persistent POSIX outbox

`history_outbox.py` is a tested disk-backed delivery library, not the deployed Actions history path. Production uses the [per-run handoff](LIVE_HISTORY_ADAPTER.md) and [direct D1 ingestion](CLOUDFLARE_DURABLE_HISTORY.md). This reference preserves the optional queue's stricter ordering and recovery contract.

The outbox is durable only while its filesystem survives. It needs a single POSIX owner, local SQLite locking/fsync, private storage, supervision and backups. An outbox recreated on every ephemeral runner cannot provide cross-run recovery. Caches and expiring artifacts do not close that gap.

## Contract and ordering

1. A producer assigns one random UUID4 and a UTC timestamp to a completed
   check, retaining that identity for the entire handoff. Do not reconstruct a
   retry from logs, `seat_state.json`, a new lookup, a new UUID, or the current
   time. The state cache can contain observations from different or failed runs.
2. `prepare_check` validates and sanitizes only completed public course facts.
   Counts are whitelisted; arbitrary reserved-seat text becomes counts or the
   fixed `unknown` marker. A partial failed check may include successful course
   observations. Never invent an observation for a failed lookup.
3. `enqueue` revalidates canonical bytes and commits them, their SHA-256 and
   course keys together. Exact re-enqueue is a no-op, including after delivery;
   reusing a UUID for changed bytes fails. `ENQUEUED` means **local commit**, not
   D1 acceptance. Preparation/storage errors have a separate best-effort boundary
   (`enqueue_completed_best_effort`); its result cannot replace monitor health.
4. Pending records are sorted by observation UTC timestamp. Before any attempt,
   older pending input can be inserted and will run first. Distinct records with
   identical timestamps for the same course are rejected as ambiguous. Equal
   timestamps for different courses are allowed. Current protocol batches share
   one timestamp; it is a completed-check sample time, not exact opening time.
5. The earliest unresolved check blocks all later checks sharing any term/CRN.
   This includes deferred and permanently blocked records, and dependencies
   propagate through multi-course batches. Independent courses can progress;
   a historical two-course batch blocks both members together. Current source checks contain active IT 341 DL1 and IT 343 DL1, DL2 and DL3; retiring IT 304 does not discard or alter older queued two-course batches.
6. An OS `flock` serializes enqueue and drain across processes. It is held through
   HTTP; there is no expiring lease that could permit a slow old sender to commit
   after a new owner. Contention fails immediately with `OUTBOX_BUSY`. Only the
   historical producer must retain/retry the handoff; monitoring must not wait
   for that lock. No unsupported NFS/object-sync/shared-disk locking is assumed.
7. An attempt counter, time frontier and next retry time commit **before HTTP**.
   An unacknowledged attempt may already exist remotely. A new backdated record
   at/below that course's attempted frontier is rejected (`OUTBOX_LATE_RECORD`).
   The original UUID/body remains retryable. Unknown future/missing observations
   cannot be ordered by this queue: late data fails closed instead of silently
   changing opening events. All writers must use this one queue authority.
8. Only a matching `INGESTED` acknowledgement permits a delivered receipt. Receipt
   failure or process death leaves the original pending; retry may acknowledge
   an already committed D1 transaction without another observation or event.
   Check IDs, payloads and delivered receipts are retained; no pruning is added.

`validate_prepared` is a public shared boundary, used before local storage and
network transmission. The HTTP client distinguishes retryable HTTP failures from
permanent failures so a 503 is retained for automatic retry while a 404 is held.

## Expected failures

| Failure | Historical behavior | Existing monitoring/Bark |
| --- | --- | --- |
| Timeout, connection failure, HTTP 408/429/5xx (including Cloudflare edge errors), invalid acknowledgement | Keep body pending; delay 60, 120, 240… seconds capped at 3,600, persisted across restarts; no later same-course submission | Unchanged; history is separate |
| HTTP 400/401/409/413 or another permanent HTTP failure | Keep and block affected course(s); operator diagnoses before `retry` | No history-generated phone alert |
| Unexpected client exception | Fixed `CLIENT_ERROR`; keep pending with backoff; no exception text logged | No change to check outcome |
| Death before outbox commit | Transaction rolls back; producer must still hold original facts, otherwise sample is lost | Existing semantics remain |
| Death after attempt commit, during HTTP, or after remote commit but before receipt | OS releases lock; original pending body/delay survive; restart retries idempotently | No new lookup or Bark retry |
| Slow streaming response | Inactivity timeouts alone are insufficient; supervisor kills bounded historical process; queue remains pending | Must not share monitoring's process/time budget |
| Outbox unavailable, full, locked, corrupt, or on unsupported platform | Capture returns a fixed failure; no silent queue recreation or dropping oldest rows; investigate and recover from surviving source | Best-effort boundary preserves monitor result |
| Missed scheduler run or no successful public lookup | No observations exist to replay | Cannot recover a transient opening retrospectively |
| Late older data after later attempt | Reject locally at the attempted frontier; the current direct endpoint separately accepts delayed original facts | No invented/reordered events |
| Bark rejects a push | Does not suppress an independently supplied completed observation | Existing monitor handles the push exactly as before |

One drain invocation attempts at most `--limit` records (default 20), with one
HTTP attempt per record. Backoff never sleeps inside the queue. HTTP retries
never repeat a public course lookup. `drain` exits nonzero if pending/blocked
records remain; operational code must report that separately from seat health.
Wall-clock retry timing assumes a reasonably synchronized host clock; clock
rollback can delay recovery, without permitting same-course overtaking.

## Deployment preconditions

A persistent deployment would require one canonical database/lock path for all producers and consumers, no hard-linked aliases or independent queue clones, a surviving source handoff, an independently supervised bounded consumer, scoped ingestion credentials, disk/backlog monitoring and tested backups. No persistent queue host is supplied by this repository. The queue process must not receive Bark or student credentials or replace monitoring health.

## Local use and recovery

All examples are local fixtures, not production commands.
`init`, `enqueue`, `status` and `retry` perform no network operations. `drain`
requires explicit client configuration; an unset `GMU_HISTORY_ENABLED` returns
`DISABLED` without attempting a submission. Only tests use loopback HTTP.

Keep generated payloads and database backups outside the checkout. If stored
locally, retain the SQLite extension in replay/backup names (for example
`outbox.sqlite3.replay` or `outbox.sqlite3.replay.backup`), and end backups with
`.bak` or `.backup`. Git ignores these databases, locks and SQLite sidecars,
plus the example's root-level `prepared.json` export. Arbitrarily renamed exports
are not automatically protected; review staged files before committing.

```bash
mkdir -m 700 /tmp/gmu-history-example
python history_outbox.py --db /tmp/gmu-history-example/outbox.sqlite3 init
python history_outbox.py --db /tmp/gmu-history-example/outbox.sqlite3 status
```

Use the library with synthetic completed facts:

```python
from datetime import datetime, timezone
from pathlib import Path
import uuid
from cloud_history import prepare_check
from history_outbox import HistoryOutbox
from seat_history import CourseObservation

box = HistoryOutbox('/tmp/gmu-history-example/outbox.sqlite3')
prepared = prepare_check(str(uuid.uuid4()), datetime.now(timezone.utc), 0.25,
    [CourseObservation('202710', 'IT', '999', 'DL1', '99999', 0)])
box.enqueue(prepared)  # Persist once; retry from this queue, not a new prepare call.
# Optional local interchange: exact canonical bytes, no trailing newline.
Path('/tmp/gmu-history-example/prepared.json').write_bytes(prepared.body)
```

1. **Routine outage or interrupted drain:** keep the same file and `.lock` path;
   restart the bounded consumer when the endpoint recovers. `status` reports
   pending IDs, attempts, next-attempt Unix times, blocked flags and fixed codes,
   without payload bodies. A live lock must never be deleted. If a process is
   hung, terminate it using the supervisor; OS lock release enables safe restart.
2. **Permanent failure:** inspect the fixed code and configuration, without
   logging secrets. Correct auth/endpoint problems out of band. For a 409,
   distinguish changed UUID contents, changed course identity and tied course timestamps (or legacy strict-order rejection) using retained public bodies and reviewed database inspection. After
   diagnosis, `python history_outbox.py --db PATH retry CHECK_UUID` clears the
   local hold/delay; it does not alter bytes, UUID, timestamp or course ordering.
   Repeating a conflict without fixing its cause will block it again. No skip,
   drop or payload-edit command exists.
3. **Local corruption or disk loss:** stop historical producers/consumer, preserve
   files/journals and restore the outbox backup. Do not `init` a missing production
   file to hide the loss. SQLite backup API is preferred for snapshots; coordinate
   backups/restores with the sole queue owner and run `PRAGMA integrity_check` on
   the backup. Never copy just a live main file while excluding its journal.
   Retrying a stale backup is safe for retained IDs; samples absent from both
   backup and destination are unrecoverable without another surviving source.
4. **D1 point-in-time restore:** delivered receipts may now overstate remote
   contents. Keep the original outbox as an archive. Build a **new local replay
   outbox** from all retained original bodies, including delivered ones, ordered
   by timestamp. Re-enqueueing into the original queue is deliberately a no-op.
   After separate operator review, drain the replay queue into the restored
   target. Retained D1 IDs deduplicate; a missing suffix appends in order. Verify
   row/event counts and pending/blocked state on a separate test target first.
5. **Older missing records:** the queue's attempted frontier remains strict even though schema 0002 direct ingestion accepts delayed records. Do not edit the original queue to bypass that frontier or generate new UUIDs. Preserve the archive and evaluate an isolated replay queue or direct replay of original facts against the current endpoint. The legacy 0001 endpoint required a sorted rebuild for missing records behind its append frontier.

For restored-destination replay, while the original owner is stopped, this local-only script creates
the replay file without changing source data (use the Python environment with
project dependencies). No submission occurs:

```python
from pathlib import Path
import sqlite3
from cloud_history import PreparedCheck
from history_outbox import HistoryOutbox

source = Path('/path/to/retained-outbox.sqlite3').resolve()
replay_path = Path('/path/to/new-replay.sqlite3')
assert not replay_path.exists()  # Deliberate new queue, never overwrite the archive.
replay = HistoryOutbox(replay_path)
replay.initialize()
with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as db:
    for check_id, body in db.execute('SELECT check_id, body FROM submissions ORDER BY stamp, check_id'):
        replay.enqueue(PreparedCheck(check_id, body))
```

## Durability and validation

The guarantee is retry of locally committed bodies while that source survives, with idempotent destination effects for retained UUIDs. It is not lossless monitoring or exactly-once delivery. Data can be lost before enqueue, with the only disk, or through incomplete backups. D1 recovery cannot restore facts it never received. Retention of bodies, check IDs, receipts and frontiers is part of replay correctness; no pruning is implemented.

Capacity and D1 operations are centralized in [the ingestion design](CLOUDFLARE_DURABLE_HISTORY.md#bounds-and-capacity). [Local validation](LOCAL_DEMO.md) covers byte identity, delayed pending input, multi-course blocking, independent progress, retry/backoff, abrupt producer death, lock contention, corruption and restore/replay. These are isolated local tests, not a production recovery certification.

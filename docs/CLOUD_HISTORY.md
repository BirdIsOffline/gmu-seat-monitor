# Historical ingestion protocol

This reference describes the current authenticated HTTP contract. The [D1 design](CLOUDFLARE_DURABLE_HISTORY.md) owns transaction, migration and recovery decisions; [structured handoff](LIVE_HISTORY_ADAPTER.md) owns production capture and submission. Local Python SQLite does not connect directly to D1: clients send HTTP to a Worker with a `DB` binding.

## Route and authentication

Only `POST /v1/checks` accepts ingestion. There is no arbitrary SQL, read, dashboard or notification endpoint. Unknown paths/query strings and unsupported methods are rejected. Browser CORS is not enabled.

Require HTTPS outside explicit loopback tests, JSON content type and `Authorization: Bearer <secret>`. `INGEST_TOKEN` is a Worker secret, not a Cloudflare management credential. The allowed token shape is 43–128 base64url characters; use cryptographically random values. Missing or malformed server configuration fails with 503; missing/incorrect client credentials receive 401 before parsing or D1 access. Authentication compares fixed-length SHA-256 digests without an early differing-byte return. Rotation is out of band; simultaneous-token rotation is not implemented.

## Request contract

All fields are required, unknown fields are rejected, and request bodies are bounded to 64 KiB including streamed requests. Encoded/non-UTF-8 bodies are rejected. The maximum is 50 distinct observations.

| Check field | Contract |
| --- | --- |
| `version` | Integer protocol version 1 |
| `check_id` | Canonical lowercase random UUID4, assigned once |
| `executed_at_utc` | Valid fixed-width microsecond UTC timestamp: `YYYY-MM-DDTHH:MM:SS.ffffff+00:00` |
| `duration_seconds` | Finite number from 0 through 86,400 |
| `status` | `success` or `failed` |
| `error_code` | Null on success; failed checks use `TIMEOUT`, `LOOKUP_TIMEOUT`, `COURSE_LOOKUP_FAILED` or `CHECK_FAILED` |
| `observations` | Success requires at least one row; failed checks may retain successful partial rows or contain none |

| Observation field | Contract |
| --- | --- |
| `term` | Six digits |
| `subject` | 1–10 uppercase letters |
| `course_number` | 1–10 uppercase letters/digits |
| `section` | 1–10 uppercase letters/digits/hyphens |
| `crn` | 1–10 digits; unique with term within a check |
| `total_available` | Null or integer 0–1,000,000; booleans, fractions and numeric strings are invalid |
| `reserved_summary` | Null, literal `unknown`, or permitted bounded count fields |

Allowed reservation keys are `unreservedAvailable`, `unreservedRemaining`, `reservedAvailable` and `reservedRemaining`. Duplicate semantic unreserved keys must agree. The client reduces parseable public text to counts and unparseable text to `unknown`; raw labels, arbitrary errors, instructor/student details and original responses do not cross the boundary. The schema cannot prove the provenance of numeric facts.

## Derived values and acknowledgement

The Worker derives unreserved availability, classification and potential count rather than accepting events from the caller. Missing totals break event inference. Zero is full; explicit zero unreserved is reserved-only; positive unreserved is capped by total. Missing reservation metadata permits uncertain potential availability, while unparseable restrictions suppress candidates. A first historical observation establishes a baseline without an event; subsequent increases can create `unreserved_increase` or `possible_increase`.

Success is HTTP 200 with `INGESTED` and the matching original check UUID after the complete transaction commits. Retrying original or semantically reordered facts deduplicates accepted effects. Changed UUID contents, tied course timestamps or changed identity return conflict. Delayed new samples are accepted under schema 0002 and repair chronological edges.

| HTTP status | Meaning |
| --- | --- |
| 400 | Invalid input |
| 401 | Client authentication rejected |
| 409 | Identity/time/idempotency conflict |
| 413 | Payload too large |
| 415 | Unsupported media |
| 503 | Configuration or storage unavailable |

Responses are `no-store` with fixed codes, not SQL, exception text, headers or request bodies. Worker logs use fixed conflict/unavailable categories. Surrounding services must not enable raw authorization/body logging.

## Python client

`prepare_check` produces immutable `PreparedCheck.body` and `validate_prepared` rechecks it before storage/transmission. Generate UUID/time once and retain the same bytes across retries; new time or identity is a new check, not a retry.

The generic client defaults to 3.05/10-second connection/read inactivity timeouts and three attempts, with bounded backoff. It retries transport/acknowledgement failures and HTTP 408/429/5xx, not permanent errors or redirects. It requires an exact bounded acknowledgement and disables ambient proxy/netrc credentials by default. These timeouts are not a wall-clock deadline; production submission uses a supervised child process.

`CloudHistoryClient.from_env()` is a generic library interface using `GMU_HISTORY_ENABLED`, `GMU_HISTORY_ENDPOINT` and `GMU_HISTORY_TOKEN`. The deployed submitter intentionally uses dedicated `GMU_HISTORY_PRODUCTION_ENDPOINT` and `GMU_HISTORY_PRODUCTION_TOKEN`, without fallback. [Operations](../CLOUD_SETUP.md#configuration-boundaries) is the configuration reference.

## Schema compatibility

Migration 0001 used strict append ordering and rejected newly backdated checks. Migration 0002 replaced that ordering while retaining the HTTP contract, original check IDs and accepted facts. Legacy receipt time is unknown and observation UUID mappings must survive recovery. Do not apply the earlier strict-order model to current direct ingestion or confuse it with the optional local outbox's attempted frontier.

Use [local validation](LOCAL_DEMO.md) for disposable fixtures. Public dummy tokens in local examples are not valid production credentials.

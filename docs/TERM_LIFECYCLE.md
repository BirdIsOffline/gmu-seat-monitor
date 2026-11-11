# Term lifecycle and archival

## Current scope (as deployed November 10, 2026)

The public catalog monitor is currently **Spring 2027 only**:
- `TERM_NAME = "Spring 2027"` in `monitor.py`
- CRN 17582 = IT 304 DL1; CRN 17593 = IT 341 DL1
- `resolve_term_code()` currently requires "spring 2027" in the returned term description
- `dashboard/worker.js` statically names the Spring 2027 pair in HTML, read-only queries and graphs

The Cloudflare/D1 schema stores a `term` as well as a `crn` per course observation. A new term does not inherently erase historical records. However, **the current dashboard is not a multi-semester browser**, and the monitor does not automatically recognize a new term.

## If registration ends or polling permission changes (no new code needed)

1. Stop the *external scheduler's* five-minute GitHub dispatch. Confirm it no longer submits `mode=check`. Do not delete historical databases or Bark configuration.
2. Independently disable future live checks by setting the GitHub Actions repository variable `GMU_POLLING_APPROVED` to `false` (or disabling the operational workflow). The workflow's `check-seats` job is gated on that variable.
3. Confirm in GitHub Actions that checks are no longer running and verify D1 still holds the old history. A past run's "success" does not authorize continuing polling.
4. Export or archive relevant operational history as needed, without exposing credentials, personal account details, or student records.
5. Keep offline synthetic demonstrations available for regression testing after live monitoring stops. `python monitor.py --demo`, `python demo_history.py`, and `python demo_live_history.py` do not contact GMU.
6. For privacy/cost/retention, separately review Cloudflare D1 storage/usage and backups; never destroy history by deleting the production D1 database as part of a routine pause.

## Reconfigure for another term (proposed, NOT implemented)

Make changes in a new reviewed development branch; **do not overwrite the active Spring 2027 settings or restart polling to test new CRNs**.

1. Confirm university automated-query authorization for the **new term, courses and polling frequency**. If absent, keep all live polling disabled.
2. Read official course offerings and identify **exact** new term name and CRNs; verify subject, course number and section. The old CRNs must not be reused on assumption.
3. Refactor to a validated term/course configuration with an explicit `term`, CRNs and section identity and a clear human approval step. Separate the new term's comparison state from old-term snapshots (for example, use `term:crn` for state identity); otherwise a reused CRN could produce a false increase or suppress an alert.
4. Preserve old records and include a term filter in dashboard queries before displaying new-semester observations. Do not aggregate all historical CRNs as though they identify the same course across semesters.
5. Test with **mocked**, offline multi-term snapshots: unchanged total, opening, reserved-only change, reused CRN in a new term, missing term, unknown reservation data, and cold start. CI tests must make **zero GMU calls**.
6. Review operational state-cache behavior, historical ingestion identity, dashboard permission boundaries and secret storage. Do not store real student registration records.
7. Deploy separately to staging/test environments where feasible; verify rollback; only after review update production monitor configuration. A new monitoring term must be explicitly approved and deployed.
8. After changing term configuration, verify several checks, saved observations and phone test safely. Do not claim a *genuine seat-opening alert* happened when only the synthetic/test path was exercised.

## Archived data and offline tests

- Offline tests cover reserved/unreserved classification and notification decisions without live catalog access.
- Historical D1 records remain separate from the current semester configuration.
- Archived samples are point-in-time records and should not be represented as live monitoring results.

## Future roadmap, not a delivered feature

- Configurable term/course metadata (validated config + explicit approval)
- First-class archived-term selector and read-only historic dashboard
- Per-term state isolation / CRN reuse regression tests
- Optional safe expiration / polling pause control after a term ends
- Documented retention, access-control rotation and operational health alert policy

This document is operational guidance and a plan. It does **not** silently switch semesters, alter GitHub repository variables, contact GMU or add automation.

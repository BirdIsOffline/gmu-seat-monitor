# Dashboard deployment and rollback

This runbook updates the existing standalone dashboard Worker without changing ingestion, schema, monitoring targets, scheduling, notification settings or credentials.

## Inputs and preflight

Keep account/resource mappings, the endpoint and deployment versions in private deployment records. Required bindings are `DB` for history and the secret `DASHBOARD_READ_TOKEN`, distinct from the ingestion token. Local Wrangler examples do not identify production resources.

Use an immutable reviewed Git commit with successful Ubuntu CI. Verify local/remote source parity. Preserve the current deployed version, runtime settings and bindings before changing traffic.

## Dashboard-only update

1. Verify the selected Worker and database are the dashboard/history targets. Confirm the existing token works and missing credentials return HTTP 401.
2. Capture baseline counts and protected fingerprints for checks, observations and events. Keep raw data private; normal ingestion can append records during comparison.
3. Upload exactly the reviewed `dashboard/worker.js` blob as a new version. Preserve compatibility settings and bindings, retaining secrets without embedding or rotating their values. Record the returned version before activation.
4. Activate that version and check traffic assignment. Reconcile ambiguous acceptance through status reads before any further upload or activation.
5. Require authenticated HTTP 200, missing/invalid-token HTTP 401 and served HTML matching the selected source.
6. Check separate CRN cards, legends, tables, series and lifecycle labels. Each active section must independently affect freshness. Test missing data with isolated fixtures, not production edits.
7. Check both ranges, the query bound, nullable counts, events or their correct empty state, and JavaScript errors. Compare baseline history, explaining any normal ingestion activity.
8. Verify schema, ingestion deployment, credentials and unrelated settings are unchanged. Retain redacted evidence and the previous version.

Use existing observations. A dashboard release needs no new database, migration, monitoring dispatch or notification. A Git push alone does not deploy the Worker.

## Credentials and rollback

Use the existing token for routine updates. Enter it through a private masked interface, not command arguments or logs. If a rollback crosses a token rotation, verify the old version's secret bindings before activation so it does not restore an obsolete credential.

Reactivate the known-good version after checking runtime and binding compatibility. Preserve the Worker, D1 and ingestion; repeat authentication, source and history checks, then record the outcome. If a credential is exposed, address access containment and rotation separately.

The dashboard has no mutation routes, but its D1 binding is not a read-only database principal. Fixed queries and code review remain security boundaries. See [historical recovery](../docs/CLOUDFLARE_DURABLE_HISTORY.md#failures-and-recovery).

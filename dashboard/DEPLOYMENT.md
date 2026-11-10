# Dashboard deployment runbook (Cloudflare dashboard; manually authorized)

This runbook applies to the **separate** dashboard Worker from PR #7. The ingestion Worker `gmu-seat-history-production-worker`, its `/v1/checks` endpoint, the external scheduler, GitHub Actions, Bark settings, database schema and monitoring should remain **unchanged**. Do not import an ingestion token into this Worker.

## Prerequisites and identifiers

- GitHub source: https://raw.githubusercontent.com/BirdIsOffline/gmu-seat-monitor/feature/history-dashboard/dashboard/worker.js
- New Worker name: `gmu-seat-history-dashboard` (must NOT be the existing `gmu-seat-history-production-worker`)
- Existing D1 database: `gmu-seat-history-production` (do not choose staging or recovery)
- Worker D1 binding variable: **`DB`**
- New Worker Secret name: **`DASHBOARD_READ_TOKEN`** (NOT `INGEST_TOKEN`; do not reuse the ingestion token)
- No extra database or migration is needed. Existing tables: `checks`, `observations`, `opening_events`.

## Dashboard-only deployment

1. In https://dash.cloudflare.com/ select your account then **Workers & Pages** → **Create application** → **Create Worker** (or **Hello World**) and name the new Worker exactly `gmu-seat-history-dashboard`. Deploy the initial boilerplate. **Never select or edit the existing production ingestion Worker.**
2. Open the NEW Worker's **Settings → Bindings** (some UI versions label this as **Bindings**). Select **Add binding → D1 database**, set the binding variable to `DB`, and choose **`gmu-seat-history-production`**. Save/deploy binding settings. This shares the existing database but does not create another database or change the schema.
3. On your own Windows PC, run `py -c "import secrets; print(secrets.token_urlsafe(32))"`. It prints one fresh random 43-character token. **Store it privately in a password manager** and do not paste it in Git, chat, screenshots or a public shell history.
4. In the NEW Worker's **Settings → Variables and Secrets → Add**, choose type **Secret**, name `DASHBOARD_READ_TOKEN`, value = the token from step 3. Deploy changes. **Do not use plaintext Variable type** and **do not copy the ingestion token**.
5. Open the raw GitHub source URL above. Select all and copy the full JavaScript file. Return to the NEW Worker and choose **Edit code**. Replace the Hello World source in the primary Worker script with the copied JavaScript. Save/deploy. This code is standalone; it needs no imports, libraries or build commands.
6. Open the NEW Worker's `https://...workers.dev/` URL. You should see **Seat availability history** with a read-token input (not sample data before authentication).
7. Security check: request `https://...workers.dev/api/dashboard?range=24h` in a *new browser tab with no authorization header*. It should reply with HTTP 401 and JSON code `UNAUTHORIZED` (not data).
8. Back on `/`, enter the **separate dashboard token** from your password manager. Confirm two course rows (IT 304 DL1 / CRN 17582, IT 341 DL1 / CRN 17593) plus 24-hour and 7-day charts. IT 341 unknown unreserved status must read **Unknown**, not "3 unreserved".
9. Share only the publicly visible Worker **URL without token or query credentials**, and a screenshot with any secret hidden, so the endpoint and presentation can be checked. Never share the token.
10. After a successful live review, consider whether to merge PR #7. No merge is needed to paste its source into a new Worker.

## What approval does and does not authorize

- Authorized: separate dashboard Worker using the existing D1 as a **read-only query source**, tested with a separate read token.
- Not authorized: overwriting the existing ingestion Worker; changing DB rows, migrations, monitoring frequency, Bark settings, access credentials or scheduled workflow; enabling public historical JSON without authentication.
- The D1 binding itself is a regular D1 binding (not an SQL-enforced read-only database role); read-only behavior is enforced by the dashboard source containing only fixed SELECT statements. Review code before changing it.
- The page HTML is public. JSON history requires the secret. The browser keeps the token in tab memory and clears it from the input after login, but a compromised browser could access it; protect the device/password manager.
- Do not store the token in a URL, browser local storage, GitHub Action, Cloudflare plaintext variable, or log.

## Rollback and verification

- If the dashboard behaves unexpectedly, disable the **new dashboard Worker's** `workers.dev` route or delete **only that dashboard Worker** in Cloudflare. **Do not delete the database or existing ingestion Worker**.
- Verify GitHub Actions health and `HISTORY ... INGESTED` remain normal after rollout. D1 query checks can independently confirm data still records.
- This setup does not itself create a durable retry queue for missed monitoring samples, and a 100% stored-check rate does not prove all scheduled checks were delivered.

Cloudflare references:
- https://developers.cloudflare.com/learning-paths/workers/get-started/first-worker/
- https://developers.cloudflare.com/d1/best-practices/remote-development/
- https://developers.cloudflare.com/workers/configuration/secrets/

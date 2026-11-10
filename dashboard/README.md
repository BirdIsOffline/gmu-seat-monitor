# Phase 3 historical dashboard — isolated draft, **not deployed**

An optional separate Cloudflare Worker renders a self-contained historical dashboard and queries the existing D1 tables (`checks`, `observations`, `opening_events`) **read-only**. It does not import or modify the ingestion Worker, monitor, GitHub Actions workflow, Bark notifications, or external scheduler; it creates no GMU traffic. It has no data-writing routes or calls to the production history ingestion endpoint.

## Privacy and deployment gates

- **No automatic deployment.** Do not merge or deploy without a separate review, production binding verification, and user authorization.
- The dashboard uses a **separate** `DASHBOARD_READ_TOKEN` secret, never the history ingestion token. Its HTML is public but history JSON requires this token; the browser holds it only in tab memory and sends it only to same-origin `/api/dashboard` over HTTPS. Do not put the token into source code, GitHub commits, URLs, logs or chat.
- The D1 binding must be the operator-approved **production** history database, not staging or recovery. Use a separate Worker from `gmu-seat-history-production-worker`. No ingestion routes or credentials are present in this app.
- Require a trusted HTTPS endpoint before use. For stronger access controls, review Cloudflare Access separately before public deployment. This app does not implement IP rate limits or token rotation; configure them at the edge if exposing publicly.
- Free-tier capacity, retention and Access availability must be verified for the account. Observe query/CPU usage if deploying. This draft sets no schedule and makes no outbound requests.

## Dashboard behavior

- Switch between 24-hour and 7-day windows. Displays selected-window check success rate, latest reported total seat counts, time-series line charts for both CRNs, latest classifications/unreserved status, sampled opening events and recorded-check cadence metrics.
- Cadence metrics use `LAG` on *stored checks inside the selected time window*: measured interval count, average/longest interval, and counts over 10 or 15 minutes. They cannot detect missing triggers before the first stored check, after the most recent check, or determine that every scheduled trigger ran. They measure recorded-history spacing, **not uptime or delivery guarantee**.
- Unknown unreserved seats are rendered as `Unknown`, never zero or eligible. `total_available=3` for IT 341 does **not** prove 3 unreserved seats exist.
- Chart segments break if consecutive observations are more than 15 minutes apart. Time shown in the user's browser local timezone, marked as local in the data table header.
- Token is not stored in localStorage, sessionStorage, cookies or URLs. Reload requires reentry.
- Fixed queries, two fixed course CRNs, bounded returned history and 50-event cap. No raw database, credentials, student data or arbitrary SQL are exposed through the API.

## Review and dry run

The Worker source can be checked using `node --check dashboard/worker.js`. The standalone Node tests can be run with `node --test dashboard/worker.test.mjs`. They rely only on mocked D1 results, issue no network requests and do not prove live Cloudflare behavior.

No production Worker deployment configuration or real D1 database ID is committed. Before future activation, provision a separate Workers service with the dashboard source and a D1 `DB` binding pointing to the reviewed production database, set `DASHBOARD_READ_TOKEN` using Cloudflare's secret store, and verify privately against the real schema. Keep monitoring and the ingestion Worker unchanged.

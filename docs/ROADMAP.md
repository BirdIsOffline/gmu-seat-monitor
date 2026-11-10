# Roadmap: from working prototype to portfolio-quality service

The project currently performs read-only periodic checks with targeted iPhone alerts. These are **planned features**, not completed claims.

## Phase 1 — Automated verification and documentation (included in this upgrade)

- [x] 29 offline tests with mocked course lookup and Bark requests
- [x] New CI workflow on push/pull request (verify first successful CI run after upload)
- [x] Expanded README with architecture, setup, alerts, and limitations
- [x] Document data handling and security assumptions

## Phase 2 — Operations and reliability (next priority)

- [ ] Add a health signal for each monitoring run: timestamp, success/failure, response duration
- [ ] Alert on repeated monitoring failures without flooding the phone
- [ ] Add safe, bounded retry/backoff on transient 5xx/network errors, respecting GMU's permitted request rate
- [ ] Record run outcomes independently from successful GitHub dispatch requests
- [ ] Measure missed / delayed runs and improve resilience against cache eviction

## Phase 3 — Data storage and useful analytics

- [x] Offline SQLite schema (`checks`, `observations`, `opening_events`) and public-fact validation
- [x] Queries for sampled openings per day and monitoring success rate
- [x] Authenticated Worker/D1 ingestion; staging validation reported by operator
- [x] Offline disk-backed ordered outbox, immutable retries and crash/restart tests
- [x] Offline Cloudflare-first atomic ingestion with immutable UUIDs and chronological delayed-sample repair
- [ ] Review and deploy schema/Worker together to staging, measure quotas and validate rollback
- [ ] Add a reviewed structured producer handoff, with explicit reporting of unconfirmed capture and pre-Cloudflare data-loss risk
- [ ] Independently review and activate historical delivery, without changing monitoring or Bark
- [ ] Measure observed availability duration with explicit sampling/gap semantics
- [ ] Add retention limits and avoid persisting student information

See [Cloudflare-first history](CLOUDFLARE_DURABLE_HISTORY.md) for the current
implementation and remaining data-loss risks. Caches/artifacts are not durable
source storage; stronger capture through Cloudflare outages would need an
independent durable source.

## Phase 4 — Demonstration and maintainability

- [ ] Separate API client, seat parser, state store, and notifier into modules
- [ ] Add a local demo dashboard powered by sample/anonymized public data
- [ ] Include architecture diagrams and a screenshot/GIF of the demo interface
- [ ] Record a short demonstration video and document operating costs and tradeoffs

## Resume milestones to report truthfully

- **Now:** Built a cloud-hosted Python monitor integrating public JSON data, GitHub Actions, a scheduled API trigger, and iOS alerts.
- **After CI lands:** Added automated tests with GitHub CI and documented 29 test scenarios.
- **After operations improvements:** Report measurable reliability only after collecting real logs.
- **After dashboard/data storage:** Add SQLite or visualization technologies to the resume *only when implemented*.

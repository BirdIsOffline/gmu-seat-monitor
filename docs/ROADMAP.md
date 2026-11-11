# Project roadmap and internship portfolio evidence
Updated: November 10, 2026

## Implemented and verified

- [x] Spring 2027 read-only section lookup for IT 304 DL1 (17582) and IT 341 DL1 (17593)
- [x] Stateful candidate-opening decisions with reserved-only suppression and Bark push integration
- [x] External best-effort five-minute GitHub Actions dispatch and isolated offline CI
- [x] Reported successful two-course live checks and a real Bark **test** reaching the operator's iPhone
- [x] Transactional Cloudflare Worker/D1 historical ingestion with immutable/chronological observations
- [x] Authenticated, separate read-only D1 dashboard (24-hour and 7-day views)
- [x] Dashboard check-success/cadence statistics and last-stored-check freshness
- [x] Offline synthetic notification/seat classification tests and history demos
- [x] README of deployed architecture, privacy, security boundaries and limits

**Measured snapshot, not a guarantee:** the dashboard showed 182 stored checks and 100% success *among stored checks*, average check-to-check interval 5.00 minutes, longest 5.28 minutes, and zero >10-minute stored gaps as of November 10, 2026. Neither that sample nor a successful Bark test proves 24/7 dispatch completion or future opening-alert delivery.

## Internship portfolio completion — next priorities

- [ ] Publish sanitized dashboard screenshots (without secrets/account details) and an architecture diagram
- [ ] Record a 60–90 second walkthrough showing synthetic course opening, historical data, and test coverage
- [ ] Add a single offline integration scenario that asserts an opening decision causes the expected mocked Bark request, without GMU traffic or a real phone alert
- [ ] Review deployment permissions, token rotation and long-term retention; confirm automated public-catalog polling authorization with GMU
- [ ] Update resume using implementation-supported claims; avoid false promises about an actual enrollment or live opening alert
- [ ] Optionally document a short operations log from currently available evidence; longer-term data can be added later

## Future engineering (optional, not required to present this project)

- [ ] Safe, validated configuration for new semesters and CRNs (currently hardcoded)
- [ ] Per-term CRN state isolation and historical term selector
- [ ] Explicit auto-pause/term expiry controls following authorization review
- [ ] Detect missing scheduler triggers independently of successfully stored checks
- [ ] Rate-limited operational alerts on repeated ingestion/monitoring failures
- [ ] Durable source-outbox architecture that survives an ephemeral runner disappearing before Cloudflare submission
- [ ] Bounded retention/export policy and more flexible presentation of anonymized historical datasets

Follow [the term lifecycle runbook](TERM_LIFECYCLE.md) before changing semesters. None of these future items should be described as shipped.

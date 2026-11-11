# Engineering roadmap

## Implemented

- [x] Exact read-only lookup for the configured Spring 2027 GMU sections
- [x] Reservation-aware candidate availability classification and change-based Bark notifications
- [x] External best-effort dispatch with separate monitoring and offline CI workflows
- [x] Historical ingestion using Cloudflare Workers and D1
- [x] Token-protected historical dashboard with 24-hour and 7-day views
- [x] Stored-check cadence, success metrics, and latest-record freshness indicators
- [x] Synthetic monitor, notification-path, ingestion, and dashboard tests
- [x] Documented architecture, term lifecycle, and security boundaries

## Planned improvements

- [ ] Validated configuration for new terms and CRNs without hardcoded values
- [ ] Per-term previous-state isolation and historical term selector
- [ ] Term expiration and explicit pause controls
- [ ] Independent monitoring for missing scheduler dispatches
- [ ] Rate-limited alerts for repeated operational failures
- [ ] More durable source-side handoff for samples lost when ephemeral runners end unexpectedly
- [ ] Documented retention, export, and credential rotation policies

The items above are future work unless checked as implemented. See [term lifecycle](TERM_LIFECYCLE.md) before changing semesters.

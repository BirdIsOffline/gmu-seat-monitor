# Course lifecycle and term archival

## Current scope

The [target table](../README.md#section-identity-and-lifecycle) has four active Spring 2027 sections and one retired section. Each identity declares number, section, name and status in monitor and dashboard configuration; CI checks parity.

Only active identities are polled or notified. Retirement preserves state, observations, events and queries. The dashboard labels retired history and excludes it from active freshness. Status changes do not rewrite historical facts. No section retires automatically.

## Retirement and pause

Change a section's status in both configurations, test the active allowlist, health, history preservation and freshness, then release through the normal PR process. Deploy dashboard presentation independently. Reactivation reuses state; missed openings during retirement cannot be reconstructed.

To pause all live requests, stop dispatch and set `GMU_POLLING_APPROVED=false` or disable the operational workflow. Check for remaining runs. Preserve historical storage rather than deleting a database to stop polling. Offline tests remain available after monitoring ends.

## Another term

Multi-term monitoring is not implemented. D1 records `(term, crn)`, but comparison state is CRN-only and dashboard queries have no term filter. Reused CRNs can mix baselines or histories unless all components are extended together.

Before changing terms:

- Verify polling permission and exact identities for the new term and cadence.
- Introduce explicit term configuration, separate comparison state and term-filtered queries.
- Preserve original rows and replay IDs rather than reinterpreting them as new-term data.
- Test reused CRNs, multiple sections, cold starts, invalid terms, lifecycle status and reservations offline.
- Review backups, access and rollback; release monitor and dashboard independently.

A successful prior check does not authorize another term's polling. Automatic switching, expiration and retrospective reconstruction are not provided.

## Archives

Protect exports and recovery evidence. Preserve original IDs: deleting replay identities can change deduplication behavior. Retention must preserve baselines and a tested restore path. See [D1 recovery](CLOUDFLARE_DURABLE_HISTORY.md#failures-and-recovery) and [operations](../CLOUD_SETUP.md).

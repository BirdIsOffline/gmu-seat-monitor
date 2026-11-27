# Limitations and extension points

The [README](../README.md) describes deployed features and verified results. These are possible extensions, not delivered capabilities or scheduled commitments.

| Area | Current limit | Possible extension |
| --- | --- | --- |
| Term lifecycle | Explicit term; CRN-only comparison cache | Validated term configuration, per-term state and UI filtering |
| Scheduling | Best-effort dispatch and ephemeral runners | Independent missing-dispatch detection and rate-limited health alerts |
| Source durability | Handoff can disappear before D1 receipt | Independently durable capture and explicit recovery ownership |
| Access control | Shared bearer tokens | Reviewed edge access controls, rate limits and rotation policy |
| Retention | Unbounded growth and retained replay identities | Export/retention policy preserving replay and event invariants |
| Recovery | Local failure/replay tests; no production RPO/RTO | Isolated restore drills and defined recovery objectives |
| Dashboard scale | 12,000 samples and 50 events | Pagination or aggregation before expanding workload |
| Dependencies | Action tags, broad Python constraint, npm overrides | Independently reviewed pinning and maintenance policy |

Extensions must preserve polling permission, sampling semantics and original history. Automatic registration and fabrication of missing samples remain outside scope. See [lifecycle](TERM_LIFECYCLE.md) and [historical recovery](CLOUDFLARE_DURABLE_HISTORY.md#failures-and-recovery).

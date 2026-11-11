# GMU Seat Monitor — system diagram

The diagram shows the Spring 2027 monitoring workflow, state comparison, Bark notifications, D1 ingestion, and the separate historical dashboard.

```mermaid
flowchart TB
    A["External scheduler\n5-minute best-effort dispatch"] -->|"Scoped workflow_dispatch"| B["GitHub Actions\ncheck-seats job"]
    B -->|"Read-only HTTP/JSON"| C["GMU public course catalog\nSpring 2027 exact section + CRN"]
    C --> D["Python monitor.py\nseat parsing + change detection"]
    E["Previous snapshot\nGitHub Actions cache"] <-->|"public section-only state"| D
    D -->|"Potentially available seats increase"| F["Bark API"]
    F --> G["iPhone notification"]
    D -->|"Completed observations"| H["Optional independent\nhistory capture + submit"]
    H -->|"Authenticated ingestion"| I["Cloudflare ingestion Worker"]
    I -->|"Atomic transaction"| J[("Cloudflare D1\nchecks + observations + events")]
    J -->|"Fixed SELECT queries"| K["Separate Cloudflare\ndashboard Worker"]
    L["Private browser\ndashboard read token"] --> K
    K -->|"24h / 7d charts,\ncadence + freshness"| L
    M["GitHub CI\nMocked offline tests"] -.-> D
    M -.-> I
    M -.-> K
```

## Service boundaries

- No student account login, enrollment, registration changes or CAPTCHA bypass.
- Cloudflare ingestion and dashboard use distinct authentication secrets; the dashboard is a separate read-only query path.
- Historical source facts can still be lost if an ephemeral GitHub runner disappears before acknowledgment.
- The current monitored term and CRNs are fixed to Spring 2027; disable live polling when it is no longer needed. [Term lifecycle](TERM_LIFECYCLE.md).
- Architecture updates in GitHub do **not** automatically redeploy the Cloudflare Workers.

## Offline verification

Run `python monitor.py --demo` and `python demo_history.py` for local synthetic examples; run `python -m unittest discover -s tests -v` for mocked API, state-transition and notification-contract tests. No credentials or university requests are needed.

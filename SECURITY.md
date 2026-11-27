# Security and responsible operation

## Boundaries

The monitor makes read-only requests to the public catalog. It does not use student credentials, sign in, bypass access controls or perform registration. Scheduled access requires permission and the `GMU_POLLING_APPROVED` gate. Stop polling if access is denied or permission expires.

Ingestion deliberately writes sanitized public observations to D1. The separate dashboard exposes authenticated fixed SELECT queries. Its binding is not an SQL-enforced read-only role: code restrictions, separate credentials and deployment review provide that boundary.

## Credentials and data

- Store the Bark key, ingestion endpoint and ingestion token in GitHub Actions secrets. Keep the dashboard token in a distinct Cloudflare secret and a private password manager.
- Keep expiring, repository-scoped dispatch credentials outside source. Cloudflare management credentials do not belong in client payloads or monitoring steps.
- Never publish student information, registration records, cookies, tokens, real endpoints, raw inventories, databases, exports or recovery evidence.
- `.gitignore` covers known local files, not arbitrarily named exports. Review staged content and commit metadata; use GitHub noreply addresses for new commits.
- For an exposed secret, revoke or rotate it, restrict affected logs, and assess historical copies. Removing the current file does not erase Git history or clones.

Runner setup can print repository variables before application code runs. Supply the production endpoint through the secret context; keep exception messages, HTTP bodies and endpoint values out of reports. Existing exposed archives require separately authorized cleanup; see the [migration sequence](CLOUD_SETUP.md#endpoint-privacy-and-rollout).

The ingestion API bounds payloads and accepts only public identity/count fields. It cannot establish the provenance of numeric observations. Errors use fixed categories; surrounding infrastructure must not log authorization headers or request bodies.

Dashboard HTML is public; history JSON requires its read token. The token stays in tab memory, not persistent browser storage or URLs. A compromised browser or exposed token can read history. Per-user authorization, application rate limiting and overlapping-token rotation are not implemented.

## Workflow and test isolation

CI runs offline tests and disposable D1 fixtures without live monitoring credentials. Monitoring, history submission and manual Worker deployment are separate. Both workflows pin actions to verified upstream commit SHAs, use read-only repository permissions and disable checkout credential persistence. Python requirements pin the full dependency graph with artifact hashes; npm uses its committed lockfile. These controls require regular dependency review.

Tests use synthetic tokens and public identifiers; keep real credentials and private data out of fixtures. The [dependency security record](docs/DEPENDENCY_SECURITY_REVIEW.md) documents an earlier remediation. See [repository hardening](docs/REPOSITORY_SECURITY.md) for historical findings, enforced controls and rollback guidance.

## Reliability and disclosure

API acceptance does not prove phone playback. Positive seat counts do not establish eligibility. Caches, ephemeral runners and best-effort dispatch are not durable capture guarantees. See [failure boundaries](docs/ARCHITECTURE.md#failure-boundaries).

Report vulnerabilities through an existing private contact channel for the repository owner; keep secrets out of public issues. No dedicated private reporting channel is configured.

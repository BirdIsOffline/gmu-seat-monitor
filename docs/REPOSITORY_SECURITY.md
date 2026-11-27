# Repository security and maintenance

## Historical audit and enforced controls

The 2026-11-27 review used commit `6daf98aafc4c5700121da2e0357e1bc3ebcbd0a9`. The baseline findings are retained below alongside the controls enforced after commit `b87431f264a052cf99e2571f3085cc6eb79148fb` passed Ubuntu CI.

| Control | Observed baseline | Enforced follow-up |
| --- | --- | --- |
| Main branch | No protection or effective ruleset | PRs, required up-to-date CI and resolved conversations; administrator enforcement; no bypass, force pushes or deletion |
| Actions | All actions allowed; full-SHA enforcement off | Exact approved SHA allowlist and full-SHA enforcement enabled |
| Default workflow token | Read-only; PR creation/approval disabled | Preserved |
| External fork workflow approval | First-time contributors | All external contributor workflows require approval |
| Secret scanning / push protection | Both enabled; no open secret alerts | Both remain enabled; no open secret alerts at verification |
| Dependabot | Security-update PRs disabled; alert inventory inaccessible | Free dependency graph/alerts enabled; inventory accessible with zero open alerts at verification; automatic update PRs remain disabled |

The original alert-state and inventory requests returned 404/403. Follow-up verification confirmed enabled alerts and accessible inventory. These historical access failures do not describe the enforced settings; alert counts are point-in-time findings.

## Local workflow and dependency controls

Both workflows pin every external action to a 40-character commit SHA verified against its official `actions/*` repository. Existing major versions are retained. All three checkout steps disable credential persistence, and workflow permissions remain `contents: read`. Python installation uses `--require-hashes`.

`requirements.txt` pins Requests and all four runtime dependencies with SHA-256 hashes from official PyPI release metadata. The chosen versions match the latest successful monitoring run inspected during the audit. Universal wheels and source archives are approved; Charset Normalizer also includes native wheels for the Linux/Windows Python 3.12 and local Windows Python 3.14 targets. Unexpected artifacts fail hash checking.

At review time:

- The five pinned Python versions had no reported advisories in PyPI metadata. The old `requests>=2.31,<3` constraint admitted affected older releases.
- The existing local environment contained Certifi 2026.7.22, affected by [GHSA-6q48-6c7w-9gw2](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2). Certifi 2026.11.20 is pinned. The latest monitored run already used that safe version; the global local environment was not changed.
- `npm audit`, including development dependencies, reported zero vulnerabilities. All 98 locked packages have registry URLs and integrity hashes. The scoped overrides documented in the [historical dependency review](DEPENDENCY_SECURITY_REVIEW.md) remain unchanged.

These are point-in-time advisory checks. Hashes verify selected artifacts, not the absence of malicious upstream code. Python/Node patch releases, `ubuntu-latest` and hosted tooling still change; the build is not fully hermetic.

## Enforced repository settings

The active `main` protection has no routine bypass, including administrators:

- Pull requests with **zero required approving reviews**, no code-owner review and no independent latest-push approval.
- Required GitHub Actions check: `Offline tests (Python 3.12, Node 24)`, with up-to-date branches. Operational monitoring jobs are not merge requirements.
- Resolved conversations; blocked force pushes and branch deletion.

Default workflow permissions are read-only, workflow PR creation/approval is disabled, and **all external contributor workflows** require approval. Approval allows untrusted CI code to run; inspect workflow changes before approving.

Only these external action references are allowed; GitHub-wide and verified-publisher allowances are disabled:

```text
actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803
actions/checkout@11d5960a326750d5838078e36cf38b85af677262
actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1
actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065
actions/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38
actions/cache/restore@0057852bfaa89a56745cba8c7296529d2fc39830
actions/cache/save@0057852bfaa89a56745cba8c7296529d2fc39830
```

Full-length SHA enforcement is enabled after verification of every deployed `uses:` entry. Older refs and tag-based reruns are incompatible. Secret scanning, push protection and free dependency alerts remain enabled. No paid feature or automatic dependency merge is configured.

## Public execution boundaries

PR CI has no Bark or ingestion secrets, OIDC permission, deployment job, `pull_request_target` or privileged `workflow_run` trigger. It tests untrusted code on disposable GitHub-hosted runners. Public readers can see workflow logs and restore accessible default-branch caches; cache contents must remain public seat counts or package data. Fork-PR caches are scoped to the PR merge ref and cannot replace the default branch's cache.

The operational workflow remains separately dispatched and gated by `GMU_POLLING_APPROVED`. Bark and ingestion credentials are scoped to their existing steps. This limits accidental exposure; actions and dependencies in the same job remain trusted because they can alter files or processes before a secret-bearing step. A read-only `GITHUB_TOKEN` does not make third-party service tokens read-only. Repository writers can execute workflow code on selectable refs and remain trusted; keep write access and the scheduler's repository-scoped Actions permission narrow. Branch protection alone does not isolate secrets from writers.

The scheduler, five-minute cadence, cache keys, concurrency, history deadlines, course lifecycle and alert logic are unchanged. Dashboard access continues to use a separate token. Application-level rate limiting, per-user dashboard access and production recovery objectives remain documented limitations.

## Validation and operational verification

The [PR CI run](https://github.com/BirdIsOffline/gmu-seat-monitor/actions/runs/45961850766) and [merged main CI run](https://github.com/BirdIsOffline/gmu-seat-monitor/actions/runs/45962142154) each passed all **252 tests with zero failures or skips**. All three corrected Git-ignore tests passed on Ubuntu. Hash-verified Python installation took two seconds on the PR run and one second on main; jobs completed in 52 and 51 seconds within the unchanged five-minute timeout.

Naturally scheduled monitoring succeeded [before enforcement](https://github.com/BirdIsOffline/gmu-seat-monitor/actions/runs/45962958385) and [after enforcement](https://github.com/BirdIsOffline/gmu-seat-monitor/actions/runs/45964230251), using the approved commit. Both runs passed all five active lookups and recorded successful history ingestion under the pinned actions and hash-verified dependencies. No manual monitoring dispatch or notification test was used for this verification. These runs verify operation at those times, not uninterrupted future delivery.

Before publication, all 145 Windows-supported Python tests and 25 dashboard tests passed. The three Git-ignore tests send explicit UTF-8 LF bytes to Git and decode its output without changing assertions. No Linux runtime was available locally; the subsequent full Ubuntu runs completed that validation.

Official actionlint v1.7.12 passed both workflows after release checksum and asset-digest verification. All nine action references and 13 PyPI artifact hashes were reverified. Hash-checked Windows/Linux Python 3.12 target installations succeeded; target installation alone does not execute Linux code. A paired uncached Windows install measured no additional installation cost from hash checking. Network and runner variability can still exhaust job budgets.

The original audit changed no hosted services. The separately approved follow-up enforced the repository controls above without changing monitoring credentials, cadence, targets, alerts, database or dashboard deployment.

## Updates and rollback

For an update, resolve action tags in the official repository, verify the commit and action path, and obtain package hashes from PyPI. Review advisories, rerun Windows/Linux resolution and tests, then use a PR. `npm ci` consumes the lockfile; never use an unreviewed force upgrade to clear an audit.

Use a new corrective or revert PR; preserve history, credentials, cache state and D1. Keep immutable action pins and use a known-safe, hash-locked dependency set. Do not restore an affected Certifi release or the broad Requests constraint. Update the allowlist for an approved replacement SHA before merging it; do not disable integrity checks to make a failing run pass.

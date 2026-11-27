# Repository security and maintenance

## Audit baseline

The 2026-11-27 review used commit `6daf98aafc4c5700121da2e0357e1bc3ebcbd0a9`. Repository settings below are the observed baseline, not settings changed by this work.

| Control | Observed baseline | Recommended configuration |
| --- | --- | --- |
| Main branch | No protection or effective ruleset | Require PRs and the offline CI check; prevent bypass, force pushes and deletion |
| Actions | All actions allowed; full-SHA enforcement off | Exact approved SHA allowlist and full-SHA enforcement after compatible workflows merge |
| Default workflow token | Read-only; PR creation/approval disabled | Retain |
| External fork workflow approval | First-time contributors | All external contributors |
| Secret scanning / push protection | Both enabled; no open secret alerts | Retain; investigate any future alert privately |
| Dependabot | Security-update PRs disabled; alert inventory inaccessible | Confirm alert access, enable free alerts and review updates through PRs |

The alert-state endpoint returned 404 and detailed Dependabot alerts returned 403. Their complete status requires confirmation in repository settings; this audit does not claim that there are no dependency alerts.

## Local workflow and dependency controls

Both workflows pin every external action to a 40-character commit SHA verified against its official `actions/*` repository. Existing major versions are retained. All three checkout steps disable credential persistence, and workflow permissions remain `contents: read`. Python installation uses `--require-hashes`.

`requirements.txt` pins Requests and all four runtime dependencies with SHA-256 hashes from official PyPI release metadata. The chosen versions match the latest successful monitoring run inspected during the audit. Universal wheels and source archives are approved; Charset Normalizer also includes native wheels for the Linux/Windows Python 3.12 and local Windows Python 3.14 targets. Unexpected artifacts fail hash checking.

At review time:

- The five pinned Python versions had no reported advisories in PyPI metadata. The old `requests>=2.31,<3` constraint admitted affected older releases.
- The existing local environment contained Certifi 2026.7.22, affected by [GHSA-6q48-6c7w-9gw2](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2). Certifi 2026.11.20 is pinned. The latest monitored run already used that safe version; the global local environment was not changed.
- `npm audit`, including development dependencies, reported zero vulnerabilities. All 98 locked packages have registry URLs and integrity hashes. The scoped overrides documented in the [historical dependency review](DEPENDENCY_SECURITY_REVIEW.md) remain unchanged.

These are point-in-time advisory checks. Hashes verify selected artifacts, not the absence of malicious upstream code. Python/Node patch releases, `ubuntu-latest` and hosted tooling still change; the build is not fully hermetic.

## Solo-maintainer repository settings

Apply an active rule to `main` with no routine bypass, including administrators:

- Require a pull request with **zero required approving reviews**. Do not require code-owner review or approval of the latest push by another person.
- Require `Offline tests (Python 3.12, Node 24)` from GitHub Actions, with the branch up to date before merging. Do not require the operational `check-seats` or `test-bark` jobs.
- Require resolved conversations. Block force pushes and branch deletion.

Retain read-only default workflow permissions and disabled workflow PR creation/approval. Require approval for **all external contributor workflows**. Approval allows untrusted CI code to run; inspect workflow changes before approving.

After both compatible workflows merge, select only these external action references, with GitHub-wide and verified-publisher allowances disabled:

```text
actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803
actions/checkout@11d5960a326750d5838078e36cf38b85af677262
actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1
actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065
actions/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38
actions/cache/restore@0057852bfaa89a56745cba8c7296529d2fc39830
actions/cache/save@0057852bfaa89a56745cba8c7296529d2fc39830
```

Enable full-length SHA enforcement only after verifying every `uses:` entry on the deployed default branch. Older refs and reruns with tag-based workflows will then be incompatible. Keep secret scanning and push protection enabled. Dependency graph and Dependabot alerts are free for public repositories; confirm their state before relying on them. No paid security feature is required.

## Public execution boundaries

PR CI has no Bark or ingestion secrets, OIDC permission, deployment job, `pull_request_target` or privileged `workflow_run` trigger. It tests untrusted code on disposable GitHub-hosted runners. Public readers can see workflow logs and restore accessible default-branch caches; cache contents must remain public seat counts or package data. Fork-PR caches are scoped to the PR merge ref and cannot replace the default branch's cache.

The operational workflow remains separately dispatched and gated by `GMU_POLLING_APPROVED`. Bark and ingestion credentials are scoped to their existing steps. This limits accidental exposure; actions and dependencies in the same job remain trusted because they can alter files or processes before a secret-bearing step. A read-only `GITHUB_TOKEN` does not make third-party service tokens read-only. Repository writers can execute workflow code on selectable refs and remain trusted; keep write access and the scheduler's repository-scoped Actions permission narrow. Branch protection alone does not isolate secrets from writers.

The scheduler, five-minute cadence, cache keys, concurrency, history deadlines, course lifecycle and alert logic are unchanged. Dashboard access continues to use a separate token. Application-level rate limiting, per-user dashboard access and production recovery objectives remain documented limitations.

## Validation and rollout

1. Review the local diff and package/action provenance; run the [offline suite](LOCAL_DEMO.md). Publish through a PR and require the full Ubuntu CI suite before merging.
2. Verify the merged main CI and observe the next normally scheduled run. Do not dispatch a monitoring or notification test for this rollout.
3. Configure main protection and external-contributor approval. Verify the exact action allowlist and enable SHA enforcement only after workflow compatibility is confirmed.
4. Verify secret scanning, push protection and Dependabot alert availability. Preserve redacted verification evidence privately.

All 145 Windows-supported Python tests and 25 dashboard tests pass with zero failures or skips. The three Git-ignore tests now send explicit UTF-8 LF bytes to Git and decode its output; their assertions and coverage are unchanged.

Official actionlint v1.7.12 passes both workflows after verification against the release checksum list and GitHub asset digest. All nine action references and 13 PyPI artifact hashes were reverified. Hash-checked target installations for Windows/Linux Python 3.12 succeeded. These package checks do not execute Linux code. No Linux runtime was available locally; full Ubuntu PR CI is required before merging.

A paired uncached Windows install measured no additional installation cost from hash checking. Hosted job timeouts remain unchanged; network delays and runner variability can still exhaust the existing shared budget. No repository setting or hosted service was changed.

## Updates and rollback

For an update, resolve action tags in the official repository, verify the commit and action path, and obtain package hashes from PyPI. Review advisories, rerun Windows/Linux resolution and tests, then use a PR. `npm ci` consumes the lockfile; never use an unreviewed force upgrade to clear an audit.

Before publication, discard only the reviewed local changes if they are rejected. After publication, use a new corrective or revert PR; preserve history, credentials, cache state and D1. Keep immutable action pins and use a known-safe, hash-locked dependency set. Do not restore an affected Certifi release or the broad Requests constraint. Update the allowlist for an approved replacement SHA before merging it; do not disable integrity checks to make a failing run pass.

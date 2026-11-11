# GMU Seat Monitor — cloud configuration

The monitor uses a GitHub Actions workflow, an externally configured dispatch schedule, Bark for iPhone notifications, and optional Cloudflare historical storage.

The current setup checks the public Spring 2027 catalog for IT 304 DL1 (CRN 17582) and IT 341 DL1 (CRN 17593). It does not perform registration actions or use student credentials.

## Prerequisites

- GitHub repository with Actions enabled
- Python dependencies defined in `requirements.txt`
- Bark device key stored as a GitHub Actions secret
- Explicit permission for scheduled automated catalog access
- Optional, separately configured Cloudflare ingestion Worker and dashboard

## GitHub Actions

The operational workflow is [`.github/workflows/seat-monitor.yml`](.github/workflows/seat-monitor.yml). Offline CI is [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

1. Add `BARK_DEVICE_KEY` under **Settings → Secrets and variables → Actions → Secrets**. Never commit the key to the repository.
2. In **Actions**, open **GMU seat monitor** and run its `test` mode to check Bark delivery without querying the university catalog.
3. Confirm automated public-catalog access and polling frequency are permitted before enabling live checks.
4. Set the repository variable `GMU_POLLING_APPROVED=true` only when authorized.
5. Use `check` mode to execute a single authorized catalog check.
6. Configure external dispatch separately to trigger the operational workflow. The reported operational cadence is five minutes; GitHub execution times are best-effort.

To pause polling, disable external dispatch and set `GMU_POLLING_APPROVED=false`, or disable the operational workflow.

## Historical ingestion and dashboard

History recording uses separate authenticated Cloudflare services and is not required for Bark notifications.

- [Cloudflare historical ingestion](docs/CLOUDFLARE_DURABLE_HISTORY.md)
- [Live history adapter](docs/LIVE_HISTORY_ADAPTER.md)
- [Historical dashboard deployment](dashboard/DEPLOYMENT.md)

Keep ingestion credentials separate from the dashboard read token. GitHub source changes do not automatically redeploy either Cloudflare Worker.

## Operational constraints

- A reported seat increase is informational and does not prove an individual student is eligible to enroll.
- Scheduled jobs may be delayed or skipped. Short-lived availability changes between checks can be missed.
- GitHub Actions snapshot cache eviction can cause an alert to be repeated after state is lost.
- Public endpoints may enforce usage limits. Respect university access policies and stop automated requests if denied.
- Never commit Bark keys, GitHub tokens, Cloudflare secrets, cookies, or student records.

See [security](SECURITY.md) and [term lifecycle](docs/TERM_LIFECYCLE.md) for additional safeguards.

### References

- [GMU acceptable-use policy](https://universitypolicy.gmu.edu/policies/acceptable-use-of-technology/)
- [GitHub Actions workflow dispatch](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch)
- [Cloudflare Workers secrets](https://developers.cloudflare.com/workers/configuration/secrets/)

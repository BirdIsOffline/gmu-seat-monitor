# GMU Seat Monitor — cloud setup (Bark, no Discord/Telegram)

This is a **read-only** Spring 2027 monitor. It checks IT 304 DL1 (CRN 17582)
and IT 341 DL1 (CRN 17593) and alerts your iPhone using Bark. It never
logs into Patriot Web, registers, drops, or swaps classes.

## Current operating context

The operating setup now uses an external five-minute workflow dispatch schedule,
as reported by the operator. The original provisioning instructions below predate
that setup and describe a 15-minute GitHub schedule; they are not instructions to
change the current schedule. This reliability milestone changes neither scheduler
nor live workflow, GMU checks, authorization settings or Bark behavior.

Cloudflare staging ingestion and recovery have been verified by the operator;
production monitoring is still disconnected. The new historical outbox is an
offline component, with no automatic activation. Read the
[historical reliability operations and recovery procedure](docs/HISTORY_RELIABILITY.md)
before any future setup. It requires durable source storage; adding the outbox to
an ephemeral Actions runner or caching it does not guarantee recovery. No cloud
resource or database modification is part of this milestone.

## First — confirm the monitoring is allowed

Ask GMU ITS or the Registrar whether scheduled, read-only public course-search
requests (up to every 15 minutes, for two CRNs) are allowed. GMU's published
acceptable-use policy does not expressly grant permission for bot polling.
**The included scheduled workflow is deliberately paused** until you add the
repository variable `GMU_POLLING_APPROVED=true` after confirmation.

## Create a cloud project

1. Sign in to GitHub.com (free account is enough).
2. Create a new repository named `gmu-seat-monitor`.
3. Choose **Public** for the option where GitHub-hosted Actions minutes
   are included without charge. The repository contains only public course
   info and program code; no Bark key or personal login details. If you prefer
   **Private**, GitHub includes a limited amount of Actions compute time;
   a 15-minute schedule might exceed its free monthly quota.
4. Put the contents of this extracted folder into the repository, preserving
   `.github/workflows/seat-monitor.yml`. You can use GitHub Desktop, git, or
   GitHub's Add file > Upload files (if the nested workflow file does not get
   uploaded, create it with Add file > Create new file and paste its content).
5. Go to **Settings > Secrets and variables > Actions > Secrets > New
   repository secret**. Set name to `BARK_DEVICE_KEY`. For value, open your
   local `bark_key.txt` file and copy only the device key (never share it in a
   chat or screenshot). **Never upload `bark_key.txt` to GitHub.**
6. On the repository's **Actions** tab, select **GMU seat monitor** then
   **Run workflow**. Select mode **test**. It sends a Bark alert without
   contacting GMU. Confirm that alert arrives, and check the workflow log.
7. **After GMU confirms automated public-search checks are permitted:**
   Under **Settings > Secrets and variables > Actions > Variables > New
   repository variable**, create `GMU_POLLING_APPROVED` set to `true`.
   Run workflow > mode **check** to test a single cloud seat check; inspect
   output for both courses and verify that no error occurred. Future scheduled
   runs then execute roughly every 15 minutes while GitHub schedules them.
8. To stop, delete or change the variable to `false`, or disable the workflow
   from Actions. Stopping it doesn't affect your current GMU registrations.

## Important limitations

- GitHub schedules are **not guaranteed real-time**. They can start late or be
  skipped. A brief opening between checks might be missed. This is not a
  guaranteed 24/7 live connection or automatic registration service.
- Public repositories' scheduled workflows are automatically disabled after
  60 days of inactivity; check the repository regularly.
- GitHub free Actions in public repositories are subject to its policies.
- Each run starts a short-lived machine, checks both courses once, and exits;
  seat state is stored as public-only JSON in a GitHub Actions cache, not in
  your source repository. The cache may be evicted. If it is lost, a one-time
  repeat alert can occur when seats are available.
- The GMU public search could deny requests from GitHub cloud machines even
  if it works from your computer. Inspect the workflow logs after one cloud
  test. **Never attempt to bypass a block or authentication restriction.**
- Bark key is a secret. No GMU credentials should be uploaded, and screenshots
  must not show keys. If your key is exposed, rotate it.
- Reserved seats are **not** automatically considered available to you.
- Do **not** drop your already-registered IT 304/341 section 004 seats based on
  an alert. Check Patriot Web and get a safe switch procedure from GMU.

Helpful official docs:
- GMU policy: https://universitypolicy.gmu.edu/policies/acceptable-use-of-technology/
- GitHub Actions schedules: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
- GitHub Actions billing: https://docs.github.com/en/actions/concepts/billing-and-usage

# Architecture and design decisions

## Components

1. **External scheduler:** cron-job.org sends authenticated HTTP requests to GitHub's `workflow\_dispatch` endpoint, with `mode=check`. The scheduler is configured outside the repository; its access token is not committed.
2. **GitHub Actions runner:** starts a temporary Linux job for each trigger. The operational workflow uses Python, installs `requests`, restores a prior public-data snapshot from the Actions cache, invokes `monitor.py --once`, and saves updated state.
3. **Course search adapter (`monitor.py`):** discovers the Spring 2027 term code, initializes the public course search, retrieves JSON, and matches subject, course number, section, and CRN rather than relying on a loose title match.
4. **Seat decision:** `parse\_unreserved` interprets explicitly labeled availability; `alertable\_open\_seats` limits candidate seats to total availability. The code compares newly observed candidate counts with the previous snapshot.
5. **Push delivery:** notifications are sent through Bark using a device key in the GitHub Actions secret `BARK\_DEVICE\_KEY`.
6. **CI:** `.github/workflows/ci.yml` uses Python unit tests with mocked network access and never runs live course checks.

## Core decisions

**Reserved vs. general seats.** A positive total seat count alone is not proof that a student can enroll. When a reservation summary is returned, an alert requires an explicit positive count of unreserved seats. If reservation metadata is missing, results are marked *potentially available* and require manual verification.

**Noise reduction.** The latest alert policy reports increases in candidate seats (0→1, 1→2, etc.), not class closure, reserved-only changes, or unrelated changes.

**Separate test and live workflows.** Automated tests should run whenever code changes, not poll GMU on every commit. The live job remains explicitly authorized and separately scheduled.

**Read only.** The script does not sign into user accounts or submit registration changes. The student must verify and complete any safe course switch manually.

## Failure modes and limitations

|Failure|Current behavior|Proposed improvement|
|-|-|-|
|API changed or unreachable|Raises a search error; job fails|Send a rate-limited operational health alert after repeated failures|
|Cache eviction / cold start|Previous snapshot may be unavailable|Persist a bounded snapshot in a small durable store|
|GH API dispatch accepted but runner fails|External scheduler reports 204, but monitor may fail|Track end-to-end run outcomes separately|
|Opening appears between runs|May be missed|Evaluate approved data source or frequency; no claim of real-time coverage|
|Reserved metadata absent|Treats visible seats as potentially available|Add a clearer classification for unknown eligibility|
|GitHub token expiry|External triggers fail|Track expiry and renew scoped token securely|

## Data and secrets

The saved snapshot contains public section-level availability, not student rosters. Bark device keys are configured as secrets, and the GitHub dispatch token lives only in the external scheduler. The repository should not include cookies, student credentials, API bearer tokens, or personal enrollment data.


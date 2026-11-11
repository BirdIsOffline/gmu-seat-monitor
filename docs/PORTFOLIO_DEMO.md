# Recruiter-facing walkthrough (offline, safe, repeatable)

**Purpose:** Demonstrate how the project works even after the current university registration term ends. This is a script and evidence checklist, **not a claim that an enrollment change was executed or that a genuine seat-opening alert occurred**.

## 60–90 second demo plan

1. **Problem (0–12 s):** Explain that a class may have "3 total seats" while its unreserved eligibility is unknown. Show the principle of avoiding false-positive reserved-only alerts.
2. **Architecture (12–30 s):** Point to the [README](../README.md) pipeline: external dispatch → Actions/Python → exact course JSON → reservation parsing → Bark; in parallel → immutable D1 history → isolated authenticated dashboard.
3. **Offline behavior (30–52 s):** Locally run `python monitor.py --demo`. It displays a full class, a reserved-only state, and a hypothetical unreserved opening without any external network or notification requests. Optional: show the corresponding passing test names in `tests/test_monitor.py`.
4. **Cloud history (52–72 s):** Show **sanitized** private dashboard screenshots: stored check count, 24h/7d graph, cadence and freshness. The shown metrics are observed samples, not guarantees of every scheduled dispatch.
5. **Security and engineering tradeoff (72–90 s):** Mention read-token protection, isolated ingestion, and two limitations: cache eviction / potential loss before D1 submission, and lack of automatic new-term rollover. Finish with an explicit summary of how you tested the failure modes.

## Proof to collect

- One screenshot of the public GitHub repository README and green CI status.
- One screenshot of the *private dashboard after authenticating*, with the browser and all tokens/Cloudflare account information hidden.
- One screenshot of the offline demo console, clearly labeled synthetic.
- Optional: a single Mermaid/ASCII architecture graphic, using the README diagram.
- Optional: 60–90-second MP4/GIF of the demonstration; don't capture or speak secrets.

Do **not** include Cloudflare `DASHBOARD_READ_TOKEN`, Bark device key, scheduler GitHub token, personal email, private university account screens or student records.

## Technical talking points for interviews

- **Why use strict CRN matching?** Loose course-name matching can point to the wrong section and generate misleading alerts.
- **How do reservations affect the decision?** Known reserved-only seats are excluded; missing reservation detail is reported only as *possible* availability. Alerts never promise eligibility.
- **Why two Cloudflare Workers?** Separate ingester and dashboard access: one authenticated write path, one restricted read-only query path.
- **What does a 100% dashboard success metric mean?** Only that all *stored checks in the selected window* reported success; it does not account for a trigger that never produced a stored record.
- **What if GitHub Actions dies before D1 receives a sample?** There is a data-loss window; local caches/artifacts don't guarantee durable outbox continuity on ephemeral runners.
- **What happens after the semester?** Stop authorized scheduled monitoring; retain term-separated public history and use the synthetic demos. Configurable rollover has not been implemented; see [TERM_LIFECYCLE.md](TERM_LIFECYCLE.md).
- **How does the system protect credentials?** GitHub and Cloudflare secrets, distinct ingestion and dashboard credentials; no student login or personal enrollment data is used.

## Suggested resume bullets (use after final review)

**GMU Seat Monitor — Python, GitHub Actions, JavaScript, Cloudflare Workers/D1, REST APIs**

- Built a read-only, cloud-hosted course monitoring pipeline that matches exact university sections, classifies reserved vs. potentially available seats, and sends targeted iOS push alerts when candidate availability increases.
- Implemented authenticated transactional Cloudflare D1 history storage and a separate private JavaScript dashboard showing time-series seat availability, monitoring cadence, run outcomes and last-check freshness.
- Developed mocked API/notification tests and GitHub Actions CI; validated production recording and iPhone test delivery while documenting failure modes and the limits of sampled monitoring.

These are suggested project descriptions, not evidence of actual enrollments or an actual live opening notification.

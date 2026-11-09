# Security and responsible usage

- This repository contains **only read-only monitoring code**. No account login, CAPTCHA bypass, or automatic class registration.
- Do not commit `bark_key.txt`, GitHub personal-access tokens, cookies, student IDs, passwords, `.env` files, or private enrollment information.
- Use GitHub Actions Secrets for `BARK_DEVICE_KEY`; configure the external dispatch token outside GitHub source files, scoped to this repository with the minimum required permission and an expiration date.
- If a secret is exposed, revoke/rotate it immediately and remove it from Git history if necessary.
- Public course data may still be subject to university access and rate-limit rules. Obtain permission for scheduled requests and stop immediately if the site blocks the monitor.
- The monitoring API is not an official enrollment guarantee; students must confirm eligibility and existing registration protections before taking action.

To report a vulnerability, contact the repository owner privately rather than opening a public issue with sensitive details.

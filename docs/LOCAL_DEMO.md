# Local demonstration and testing

The project includes offline demonstrations for seat classification, monitoring history, and notification decisions. These commands use synthetic data or mocked network interactions; they do not query GMU or send Bark notifications.

## Prerequisites

- Python 3.12+
- Node.js for dashboard tests

Install Python dependencies from the repository root:

```bash
python -m pip install -r requirements.txt
```

## Monitor demonstration

```bash
python monitor.py --demo
```

This exercises simulated states including full sections, reserved-only availability, and a possible unreserved opening. It requires no live credentials.

## Historical data demonstrations

```bash
python demo_history.py
python demo_live_history.py
```

These scripts demonstrate the history data structures and submission flow using synthetic or mocked inputs. See [history architecture](CLOUDFLARE_DURABLE_HISTORY.md) for storage and ingestion details.

## Automated verification

```bash
python -m unittest discover -s tests -v
node --check dashboard/worker.js
node --test dashboard/worker.test.mjs
```

Python tests cover the monitor, notification pipeline, state tracking, and history components. Dashboard tests use mocked D1 responses. The repository's CI workflow runs offline and does not access university systems or send push alerts.

## System overview

See the [system diagram](SYSTEM_DIAGRAM.md) and [architecture](ARCHITECTURE.md) for the production data flow.

The live dashboard is token-protected. Keep dashboard tokens, Bark device keys, scheduler credentials, and Cloudflare account information out of screenshots, logs, and committed files.

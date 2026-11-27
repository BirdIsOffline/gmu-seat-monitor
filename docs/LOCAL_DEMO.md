# Local tests and demonstrations

Tests use synthetic data, mocked external services and disposable local databases. They do not query GMU, send Bark notifications or use hosted Cloudflare services. Dependency installation accesses package registries.

## Full Linux/POSIX validation

Use Python 3.12 and Node.js 24, matching [CI](../.github/workflows/ci.yml). Locking, process-timer and outbox tests require POSIX; the complete suite is not supported on Windows. From the repository root:

```bash
python -m pip install -r requirements.txt
npm ci --prefix cloudflare
python -m unittest discover -s tests -v
npm test --prefix cloudflare
node --check dashboard/worker.js
node --test dashboard/worker.test.mjs
```

The hosted baseline is **244 tests, zero skips**: 192 Python, 28 Worker/D1 integration and 24 dashboard tests. Mocks and loopback integration establish protocol behavior, not guaranteed live availability. Use the committed npm lockfile.

## Demonstrations

```bash
python monitor.py --demo
python demo_history.py
python demo_live_history.py
npm run demo --prefix cloudflare
```

The monitor demo covers full, reserved-only and potentially unreserved states with delivery mocked. The SQLite demo uses a temporary database. Structured handoff preserves original bytes through a mocked lost acknowledgement. The Worker demo uses disposable Miniflare D1 for delayed timeline repair and replay.

Stable identifiers, dates, seat counts and dummy tokens are fixtures, not production observations. Legacy fixtures deliberately activate retired identities to retain classification/lifecycle coverage. They do not seed production state or history.

## Windows-compatible subset

For example:

```powershell
python -m unittest discover -s tests -p test_it343.py -v
python -m unittest discover -s tests -p test_notification_pipeline.py -v
node --test dashboard/worker.test.mjs
```

Passing a subset does not replace Ubuntu CI. Do not add platform skips or weaken assertions to report a full local pass.

## Local Worker development

[Wrangler configuration](../cloudflare/wrangler.toml) has a placeholder database identifier, no account/routes and disabled public previews. Its example token is public and local-only.

```bash
cd cloudflare
cp .dev.vars.example .dev.vars
CLOUDFLARE_SEND_METRICS=false npm run migrate:local
CLOUDFLARE_SEND_METRICS=false npm run dev
```

These scripts select local storage and loopback. Do not substitute production bindings or remote flags for an offline demo. Keep databases, tokens, endpoint mappings and browser captures outside Git.

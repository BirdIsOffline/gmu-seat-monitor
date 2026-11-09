# PR #3 dependency security review

The original lockfile at `846b0f7` reproduces npm's **3 high-severity affected
packages**: `miniflare`, `sharp`, and `undici`. This is a package count, not three
individual advisories. The audit response contains two Sharp advisories and 23
Undici advisories. Miniflare inherits their severity and has no separate advisory.

## Affected dependency paths and fixes

| Package/path | Installed before | Vulnerable versions | Available remediation |
| --- | --- | --- | --- |
| Direct Miniflare | 4.20260730.0 | This release pins vulnerable Sharp and Undici | Latest stable Miniflare is still this release; scope overrides to this version instead of switching to the Miniflare 5 beta |
| Miniflare → Sharp | 0.35.2 | Below 0.35.4 / below 0.35.5, by advisory | 0.35.4 fixes the libheif finding; 0.35.5 also fixes librsvg; select 0.35.5 |
| Miniflare → Undici | 7.28.0 | See individual ranges below | 7.29.0, 7.29.1, or 7.30.1 by advisory; select 7.30.1 to cover all reported findings |

Wrangler 4.158.0 already uses Miniflare 5.20261104.0-beta with patched Sharp
0.35.5 and Undici 7.30.1. Keep the existing Wrangler and direct stable Miniflare
versions. The changes deduplicate patched dependencies; no new major or beta
upgrade is introduced. npm reports no automatic Miniflare/Sharp fix for the pinned
graph; that does not mean no patched transitive release exists.

## Advisory inventory from npm audit

Each row applies to the package's installed version above. Fixed versions are the
first versions outside the reported vulnerable range; the selected replacements
cover every row. Links identify the advisories returned by the registry audit.

| Package | Advisory | Severity | Vulnerable range | First fixed version | Issue |
| --- | --- | --- | --- | --- | --- |
| sharp | [GHSA-rgj7-g3m4-5g8c](https://github.com/advisories/GHSA-rgj7-g3m4-5g8c) | high | `<0.35.4` | 0.35.4 | sharp: Vulnerabilities in libheif: GHSA-g89c-p67h-r497 and GHSA-2jg2-4ch7-h545 |
| sharp | [GHSA-wq5f-xc86-pv6w](https://github.com/advisories/GHSA-wq5f-xc86-pv6w) | high | `<0.35.5` | 0.35.5 | sharp : Vulnerability in librsvg dependency CVE-2026-96889 |
| undici | [GHSA-8xcm-r25x-g524](https://github.com/advisories/GHSA-8xcm-r25x-g524) | moderate | `>=7.0.0 <7.29.0` | 7.29.0 | undici vulnerable to downstream response desynchronization via retry interceptor |
| undici | [GHSA-4cwx-7wf7-3272](https://github.com/advisories/GHSA-4cwx-7wf7-3272) | high | `>=7.0.0 <7.29.0` | 7.29.0 | undici vulnerable to cross-user information disclosure and parse-time crash via degenerate private cache directives |
| undici | [GHSA-m8rv-5g2x-5cg5](https://github.com/advisories/GHSA-m8rv-5g2x-5cg5) | moderate | `>=7.0.0 <7.29.0` | 7.29.0 | undici vulnerable to CRLF Injection via blob-like body 'type' property |
| undici | [GHSA-jr45-8vmc-qm54](https://github.com/advisories/GHSA-jr45-8vmc-qm54) | moderate | `>=7.0.0 <7.29.0` | 7.29.0 | undici vulnerable to cross-user information disclosure via whitespace around equals in Cache-Control directives |
| undici | [GHSA-v3r7-h72x-cjcm](https://github.com/advisories/GHSA-v3r7-h72x-cjcm) | moderate | `>=7.0.0 <7.29.0` | 7.29.0 | undici vulnerable to cookie attribute injection via unsanitized domain and unparsed setCookie fields |
| undici | [GHSA-3wwx-pv8p-q78v](https://github.com/advisories/GHSA-3wwx-pv8p-q78v) | moderate | `>=7.28.0 <7.29.1` | 7.29.1 | undici vulnerable to Denial of Service via unhandled error in WebSocket permessage-deflate decompression |
| undici | [GHSA-pmjh-fq2x-6v4x](https://github.com/advisories/GHSA-pmjh-fq2x-6v4x) | moderate | `>=7.11.0 <7.29.1` | 7.29.1 | undici vulnerable to Denial of Service via orphaned RetryHandler response body |
| undici | [GHSA-r53p-7pc4-xj5r](https://github.com/advisories/GHSA-r53p-7pc4-xj5r) | low | `>=7.0.0 <7.29.1` | 7.29.1 | undici vulnerable to downstream response splitting via retry interceptor |
| undici | [GHSA-rfgv-xxqx-mfg5](https://github.com/advisories/GHSA-rfgv-xxqx-mfg5) | high | `>=7.0.0 <7.29.1` | 7.29.1 | undici vulnerable to Denial of Service via unrequested WebSocket subprotocol |
| undici | [GHSA-3xpg-4rpp-hhhm](https://github.com/advisories/GHSA-3xpg-4rpp-hhhm) | moderate | `>=7.15.0 <7.29.1` | 7.29.1 | undici vulnerable to Denial of Service via unbounded decompression of compressed responses |
| undici | [GHSA-2jfj-6hjv-fm6j](https://github.com/advisories/GHSA-2jfj-6hjv-fm6j) | moderate | `>=7.0.0 <7.29.1` | 7.29.1 | undici vulnerable to cross-user cookie disclosure via Set-Cookie caching in shared caches |
| undici | [GHSA-2gqq-gqf2-x968](https://github.com/advisories/GHSA-2gqq-gqf2-x968) | low | `>=7.1.0 <7.29.1` | 7.29.1 | undici vulnerable to response truncation via oversized chunked responses in the dump interceptor |
| undici | [GHSA-w293-vg96-wgc3](https://github.com/advisories/GHSA-w293-vg96-wgc3) | high | `>=7.24.1 <7.29.1` | 7.29.1 | undici vulnerable to TLS certificate validation bypass via dropped connect options in BalancedPool |
| undici | [GHSA-8436-99hf-9mmv](https://github.com/advisories/GHSA-8436-99hf-9mmv) | low | `>=7.0.0 <7.29.1` | 7.29.1 | undici vulnerable to caching and replay of unsafe HTTP method responses |
| undici | [GHSA-rx4f-c7p8-82vq](https://github.com/advisories/GHSA-rx4f-c7p8-82vq) | moderate | `>=7.0.0 <7.29.1` | 7.29.1 | undici vulnerable to Denial of Service via WebSocketStream unclean close |
| undici | [GHSA-4qv8-2p9j-2685](https://github.com/advisories/GHSA-4qv8-2p9j-2685) | moderate | `>=7.0.0 <7.30.1` | 7.30.1 | undici vulnerable to Denial of Service via unbounded memory in the EventSource parser |
| undici | [GHSA-f72v-6w45-q472](https://github.com/advisories/GHSA-f72v-6w45-q472) | moderate | `>=7.15.0 <7.30.1` | 7.30.1 | undici vulnerable to Subresource Integrity bypass via non-lowercase algorithm tokens |
| undici | [GHSA-xjj6-f33q-929c](https://github.com/advisories/GHSA-xjj6-f33q-929c) | high | `>=7.18.0 <7.30.1` | 7.30.1 | undici vulnerable to Denial of Service via unhandled error on excessive content-encodings |
| undici | [GHSA-4w7w-5xrc-9h26](https://github.com/advisories/GHSA-4w7w-5xrc-9h26) | high | `>=7.0.0 <7.30.1` | 7.30.1 | undici vulnerable to cache poisoning and cross-user disclosure via cross-origin redirects |
| undici | [GHSA-2m5v-pch6-74hx](https://github.com/advisories/GHSA-2m5v-pch6-74hx) | high | `>=7.0.0 <7.30.1` | 7.30.1 | undici vulnerable to TLS certificate validation bypass via dropped tls options in Agent |
| undici | [GHSA-qrxm-fqhp-jwvm](https://github.com/advisories/GHSA-qrxm-fqhp-jwvm) | high | `>=7.0.0 <7.30.1` | 7.30.1 | undici vulnerable to Denial of Service via TLS 1.2 certificate-changing renegotiation |
| undici | [GHSA-9fm3-97px-rjf6](https://github.com/advisories/GHSA-9fm3-97px-rjf6) | moderate | `>=7.0.0 <7.30.1` | 7.30.1 | undici WebSocket client vulnerable to Denial of Service via unbounded automatic PONG buffering |
| undici | [GHSA-55wc-3pg8-jfw6](https://github.com/advisories/GHSA-55wc-3pg8-jfw6) | moderate | `>=7.15.0 <7.30.1` | 7.30.1 | undici vulnerable to Denial of Service via unhandled exception in the decompress interceptor |

The Sharp libheif advisory groups upstream GHSA-g89c-p67h-r497 and
GHSA-2jg2-4ch7-h545; the librsvg advisory identifies CVE-2026-96889.

## Exposure in this repository

All affected npm packages are **development dependencies**. The Worker source
imports none of them; hosted Workers use Cloudflare's runtime. The Python client
uses Requests, not Undici. No deployed Worker exists for this prototype.

- **Sharp:** Miniflare loads it in local Images/cf.image transformation handlers.
  The Worker has no image binding, uploads, image transformation, or image fetches;
  CI uses synthetic JSON course observations. The image-decoding paths implicated
  by libheif/librsvg have no identified reachable path in this configuration.
  A future image feature or untrusted local fixture would change that assessment.
- **Undici cache/retry/dump/decompress/cookie/blob/SRI/EventSource findings:** the
  project does not configure these optional interceptors or APIs or consume
  untrusted external HTTP content. Miniflare's patched HTTP client still serves
  synthetic dispatchFetch/loopback requests. The audit's affected direct Miniflare
  instance is the test harness; the Wrangler dependency copy was already patched.
- **Undici WebSocket findings:** the ingestion route is JSON POST only, never a
  WebSocket client. Miniflare does contain inspector/WebSocket support, so do not
  dismiss the advisories just because they are dev-only. No attacker-controlled
  WebSocket peer is used by these tests.
- **Undici TLS/Agent/BalancedPool findings:** the affected tests communicate over
  plain loopback HTTP and deny Worker outbound fetches. They do not exercise
  custom TLS options, BalancedPool, or TLS renegotiation with an untrusted peer.

The test harness and local CLI bind loopback, the Worker has no outbound fetch,
CI's Worker outbound service rejects external traffic, and no live secrets are
loaded. No exploit path was demonstrated for the actual configuration. Loopback
is not a security boundary against a malicious local process; exposing development
ports or adding untrusted images/outbound services would increase risk. Patch
these dependencies rather than rely on current feature restrictions.

## Change and compatibility assessment

Add version-scoped npm overrides under `miniflare@4.20260730.0` for Sharp 0.35.5
and Undici 7.30.1, then regenerate the lockfile with ordinary npm install.
No `npm audit fix --force` is used. Sharp remains within 0.35.x (a patch update),
and Undici remains within major 7 (a minor update). Their engine requirements
are satisfied by Node 24 in CI. Miniflare's API/workerd version, Wrangler,
Worker compatibility date, application code, SQL migration, authentication,
monitoring/notification code, and scheduling remain unchanged.

A clean `npm ci` verifies manifest/lock consistency. The complete local Worker
suite exercises HTTP dispatch, authentication, idempotency, SQL transactions,
rollback and Python-to-D1 parity with the patched tooling. A synthetic in-memory
PNG encode/decode smoke check confirms Sharp's native library loads. This does
not replace upstream testing of unused image/WebSocket/interceptor features.

## Validation and remaining risks

- Clean `npm ci`: succeeds without vulnerability warnings.
- Full `npm audit` including dev dependencies: **0 vulnerabilities**.
- Python: **86 tests pass**.
- Worker/D1: **14 tests pass**, including Python-to-D1 parity.
- Python and JavaScript syntax checks: pass.
- Both existing offline demonstrations: pass.
- No authentication, SQL, notification, production workflow, or scheduling code changes.

Audit results describe known registry advisories at review time, not a guarantee
against unknown flaws. Overrides require maintenance: re-evaluate/remove them
when a patched stable Miniflare release adopts these versions. Node's bundled
HTTP libraries and the host runtime have their own update lifecycle. Production
limitations from CLOUD_HISTORY.md (no durable ordered outbox, live D1 recovery
validation, rate limiting, or deployment approval) remain. Do not merge or deploy
as part of this dependency fix; rerun hosted CI and independent review.

// Synthetic, disposable Miniflare D1 only. No credentials or external requests.
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { Miniflare } from "miniflare";

const token = "synthetic_local_only_token_00000000000000000000000";
const mf = new Miniflare({ modules: true,
  scriptPath: new URL("src/worker.js", import.meta.url).pathname,
  compatibilityDate: "2026-07-01", d1Databases: ["DB"], bindings: { INGEST_TOKEN: token },
  host: "127.0.0.1", port: 0,
  outboundService: () => { throw new Error("External network forbidden"); },
});
try {
  const db = await mf.getD1Database("DB");
  for (const file of readdirSync(new URL("migrations/", import.meta.url)).filter((s) => s.endsWith(".sql")).sort()) {
    const statements = readFileSync(new URL(`migrations/${file}`, import.meta.url), "utf8")
      .split("-- statement-breakpoint").map((s) => s.replace(/^\s*--[^\n]*(?:\n|$)/gm, "").trim()).filter(Boolean);
    await db.batch(statements.map((sql) => db.prepare(sql)));
  }
  const samples = [0, 1, 2].map((total, i) => ({ version: 1, check_id: randomUUID(),
    executed_at_utc: `2026-11-09T00:${String(i * 5).padStart(2, "0")}:00.000000+00:00`,
    duration_seconds: .25, status: "success", error_code: null,
    observations: [{ term: "202710", subject: "IT", course_number: "304", section: "DL1", crn: "17582",
      total_available: total, reserved_summary: { unreservedAvailable: total } }],
  }));
  const submit = (p) => mf.dispatchFetch("http://localhost/v1/checks", {
    method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(p),
  });
  assert.equal((await submit(samples[2])).status, 200);
  assert.equal((await submit(samples[0])).status, 200);
  // Interrupt the late sample after deleting the old edge: the entire receipt
  // must roll back. Repeated failure doesn't require another course lookup.
  console.log("Injecting a synthetic D1 error; the next unavailable log is expected.");
  await db.prepare(`CREATE TRIGGER demo_failure BEFORE INSERT ON opening_events
    BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END`).run();
  assert.equal((await submit(samples[1])).status, 503);
  assert.equal((await db.prepare("SELECT count(*) AS n FROM checks").first()).n, 2);
  await db.prepare("DROP TRIGGER demo_failure").run();
  assert.equal((await submit(samples[1])).status, 200);
  assert.equal((await submit(samples[1])).status, 200); // Lost-reply retry.
  const totals = (await db.prepare("SELECT total_available FROM observations ORDER BY observed_at_utc").all())
    .results.map((r) => r.total_available);
  const edges = (await db.prepare("SELECT previous_available, new_available FROM opening_events ORDER BY detected_at_utc").all()).results;
  assert.deepEqual(totals, [0, 1, 2]);
  assert.deepEqual(edges, [{ previous_available: 0, new_available: 1 }, { previous_available: 1, new_available: 2 }]);
  console.log("OFFLINE DIRECT D1 DEMO: arrival 10m, 0m, failed 5m, recovered 5m, duplicate 5m");
  console.log(JSON.stringify({ immutable_samples: totals, chronological_openings: edges, checks: 3 }, null, 2));
  console.log("Receipt + projection commit together. Disposable local D1 removed; no GMU/Bark requests.");
} finally { await mf.dispose(); }

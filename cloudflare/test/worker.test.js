import { after, before, beforeEach, test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { Miniflare } from "miniflare";
import worker from "../src/worker.js";

const TOKEN = "synthetic_local_only_token_00000000000000000000000";
const MAX_BYTES = 64 * 1024;
let mf, db;
const migrations = readFileSync(new URL("../migrations/0001_history.sql", import.meta.url), "utf8")
  .split("-- statement-breakpoint").map((s) => s.trim()).filter((s) => s && !s.startsWith("-- D1"));
const course = (changes = {}) => ({ term: "202710", subject: "IT", course_number: "304", section: "DL1",
  crn: "17582", total_available: 0, reserved_summary: { unreservedAvailable: 0 }, ...changes });
const payload = (changes = {}) => ({ version: 1, check_id: randomUUID(),
  executed_at_utc: "2026-11-09T00:00:00.000000+00:00", duration_seconds: 0.25,
  status: "success", error_code: null, observations: [course()], ...changes });
const request = (value, changes = {}) => new Request("http://localhost/v1/checks", {
  method: "POST", headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" },
  body: JSON.stringify(value), ...changes,
});
const send = (value, changes = {}) => mf.dispatchFetch("http://localhost/v1/checks", {
  method: "POST", headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" },
  body: JSON.stringify(value), ...changes,
});
const rows = async (table) => (await db.prepare(`SELECT * FROM ${table} ORDER BY 1`).all()).results;
async function stats() {
  return Promise.all(["checks", "observations", "opening_events"].map(async (t) => (await rows(t)).length));
}

before(async () => {
  mf = new Miniflare({ modules: true, scriptPath: new URL("../src/worker.js", import.meta.url).pathname,
    compatibilityDate: "2026-07-01", d1Databases: ["DB"], bindings: { INGEST_TOKEN: TOKEN },
    host: "127.0.0.1", port: 0,
    // Any unintended Worker outbound fetch fails; tests never call external services.
    outboundService: () => { throw new Error("External network forbidden"); },
  });
  db = await mf.getD1Database("DB");
  await db.batch(migrations.map((sql) => db.prepare(sql)));
});
beforeEach(async () => {
  await db.batch(["DELETE FROM opening_events", "DELETE FROM observations", "DELETE FROM checks"].map((s) => db.prepare(s)));
});
after(async () => { await mf?.dispose(); });

test("ingests a complete check and acknowledges committed UUID", async () => {
  const p = payload();
  const r = await send(p);
  assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), { code: "INGESTED", check_id: p.check_id });
  assert.equal(r.headers.get("cache-control"), "no-store");
  assert.deepEqual(await stats(), [1, 1, 0]);
  assert.equal((await rows("observations"))[0].classification, "full");
});

test("authentication fails before touching D1 or parsing a body", async () => {
  for (const auth of [null, "Basic aaa", `Bearer ${"x".repeat(50)}`]) {
    const headers = { "Content-Type": "application/json" };
    if (auth) headers.Authorization = auth;
    const r = await worker.fetch(request({}, { headers }), {});
    // Missing server secret is unavailable, regardless of supplied credentials.
    assert.equal(r.status, 503);
    const unauthorized = await worker.fetch(request({}, { headers }), { INGEST_TOKEN: TOKEN,
      DB: { batch() { assert.fail("unauthorized database access"); } } });
    assert.equal(unauthorized.status, 401);
    assert.equal(JSON.stringify(await unauthorized.json()).includes(TOKEN), false);
  }
  assert.deepEqual(await stats(), [0, 0, 0]);
});

test("no query, arbitrary SQL, read, or alternate method endpoint", async () => {
  for (const [path, method, expected] of [["/sql", "POST", 404], ["/v1/checks?sql=x", "POST", 404],
    ["/v1/checks", "GET", 405], ["/v1/checks", "OPTIONS", 405]]) {
    const r = await mf.dispatchFetch(`http://localhost${path}`, { method });
    assert.equal(r.status, expected);
    assert.equal(r.headers.has("access-control-allow-origin"), false);
  }
});

test("rejects malformed, wrong media type, encoded and oversized bodies", async () => {
  const headers = { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" };
  for (const body of ["{", "null", "[]", '{"x": NaN}', "", new Uint8Array([0xff])]) {
    assert.equal((await send({}, { body, headers })).status, 400);
  }
  assert.equal((await send({}, { headers: { ...headers, "Content-Type": "text/plain" } })).status, 415);
  assert.equal((await send({}, { headers: { ...headers, "Content-Encoding": "gzip" } })).status, 400);
  assert.equal((await send({}, { body: "x".repeat(MAX_BYTES + 1) })).status, 413);
  // Streaming bound must work without a declared Content-Length as well.
  const stream = new ReadableStream({ start(c) { c.enqueue(new Uint8Array(MAX_BYTES + 1)); c.close(); } });
  assert.equal((await worker.fetch(request({}, { body: stream, duplex: "half" }), { INGEST_TOKEN: TOKEN })).status, 413);
  assert.deepEqual(await stats(), [0, 0, 0]);
});

test("validates every field and rejects private or derived data", async () => {
  const invalid = [
    { version: 2 }, { version: true }, { check_id: "student@example.com" },
    { executed_at_utc: "2026-02-30T00:00:00.000000+00:00" },
    { executed_at_utc: "2026-11-09T00:00:00Z" }, { duration_seconds: -1 },
    { duration_seconds: true }, { duration_seconds: 86401 }, { status: "partial" },
    { error_code: "TIMEOUT" }, { status: "failed", error_code: "PRIVATE_TEXT" },
    { observations: [] }, { observations: Array(51).fill(course()) },
    { observations: [course(), course()] }, { studentId: "secret" },
    ...[ { term: 202710 }, { term: "202710\n" }, { subject: "it" },
      { course_number: "304' OR 1=1" }, { section: null }, { crn: "abc" },
      { total_available: true }, { total_available: -1 }, { total_available: 0.5 },
      { total_available: 1000001 }, { reserved_summary: "private text" },
      { reserved_summary: [] }, { reserved_summary: { studentName: "private" } },
      { reserved_summary: { unreservedAvailable: true } },
      { reserved_summary: { unreservedAvailable: 0, unreservedRemaining: 1 } },
      { classification: "unreserved" }, { potential_available: 500 },
    ].map((o) => ({ observations: [course(o)] })),
  ];
  for (const change of invalid) {
    assert.equal((await send(payload(change))).status, 400, JSON.stringify(change));
  }
  const missing = payload(); delete missing.observations[0].crn;
  assert.equal((await send(missing)).status, 400);
  assert.deepEqual(await stats(), [0, 0, 0]);
});

test("identical, reordered and concurrent retries preserve one opening event", async () => {
  assert.equal((await send(payload())).status, 200);
  const p = payload({ executed_at_utc: "2026-11-09T00:05:00.000000+00:00",
    observations: [course({ total_available: 1, reserved_summary: { unreservedAvailable: 1 } })] });
  assert.equal((await send(p)).status, 200);
  assert.equal((await send(p)).status, 200);
  const results = await Promise.all([send(p), send(p)]);
  assert.deepEqual(results.map((r) => r.status), [200, 200]);
  assert.deepEqual(await stats(), [2, 2, 1]);
  const event = (await rows("opening_events"))[0];
  assert.deepEqual([event.previous_available, event.new_available, event.classification], [0, 1, "unreserved_increase"]);
  assert.equal((await send({ ...p, duration_seconds: 1 })).status, 409);
  assert.deepEqual(await stats(), [2, 2, 1]);
});

test("hash canonicalizes observation and count key order", async () => {
  const p = payload({ observations: [course(), course({ crn: "17593", course_number: "341",
    reserved_summary: { reservedAvailable: 9, unreservedAvailable: 0 } })] });
  assert.equal((await send(p)).status, 200);
  p.observations.reverse();
  p.observations[0].reserved_summary = { unreservedAvailable: 0, reservedAvailable: 9 };
  assert.equal((await send(p)).status, 200);
  assert.deepEqual(await stats(), [1, 2, 0]);
});

test("reserved-only changes do not generate events", async () => {
  for (let i = 0; i < 2; i++) {
    const p = payload({ executed_at_utc: `2026-11-09T00:0${i}:00.000000+00:00`,
      observations: [course({ total_available: 9 + i, reserved_summary: { unreservedAvailable: 0, reservedAvailable: 9 + i } })] });
    assert.equal((await send(p)).status, 200);
  }
  assert.deepEqual(await stats(), [2, 2, 0]);
});

test("out-of-order, same-time and changed identity roll back entire batch", async () => {
  const baseline = payload();
  await send(baseline);
  for (const changes of [{}, { executed_at_utc: "2026-11-08T00:00:00.000000+00:00" },
    { executed_at_utc: "2026-11-10T00:00:00.000000+00:00", observations: [course({ section: "DL2" })] }]) {
    const p = payload(changes);
    // A different course is inserted first, then the conflict must roll it back.
    p.observations.push(course({ crn: "10000" }));
    assert.equal((await send(p)).status, 409);
    assert.deepEqual(await stats(), [1, 1, 0]);
  }
});

test("trigger failure rolls back check, observations, and event, then safe retry succeeds", async () => {
  await send(payload());
  await db.prepare(`CREATE TRIGGER synthetic_failure BEFORE INSERT ON opening_events
    BEGIN SELECT RAISE(ABORT, 'synthetic transaction failure'); END`).run();
  const p = payload({ executed_at_utc: "2026-11-10T00:00:00.000000+00:00",
    observations: [course({ total_available: 1, reserved_summary: null })] });
  try {
    const r = await send(p);
    assert.equal(r.status, 503);
    assert.deepEqual(await r.json(), { code: "UNAVAILABLE" });
    assert.deepEqual(await stats(), [1, 1, 0]);
  } finally { await db.prepare("DROP TRIGGER synthetic_failure").run(); }
  assert.equal((await send(p)).status, 200);
  assert.deepEqual(await stats(), [2, 2, 1]);
});

test("missing D1 returns safe unavailable response", async () => {
  const r = await worker.fetch(request(payload()), { INGEST_TOKEN: TOKEN });
  assert.equal(r.status, 503);
  assert.deepEqual(await r.json(), { code: "UNAVAILABLE" });
});

test("foreign keys, uniqueness, and event constraints remain active", async () => {
  await send(payload());
  const id = (await rows("checks"))[0].check_id;
  await assert.rejects(db.prepare("DELETE FROM checks WHERE check_id=?").bind(id).run(), /FOREIGN KEY/);
  await assert.rejects(db.prepare("UPDATE observations SET total_available=-1").run(), /CHECK/);
  await assert.rejects(db.prepare("UPDATE checks SET status='failed', error_code=NULL").run(), /CHECK/);
  assert.equal((await db.prepare("PRAGMA foreign_keys").first()).foreign_keys, 1);
  const indexes = (await db.prepare("PRAGMA index_list(observations)").all()).results;
  assert.ok(indexes.some((i) => i.name === "idx_observations_course_time"));
  await send(payload({ executed_at_utc: "2026-11-10T00:00:00.000000+00:00",
    observations: [course({ total_available: 1, reserved_summary: null })] }));
  await assert.rejects(db.prepare(`INSERT INTO opening_events
    (observation_id, previous_observation_id, previous_available, new_available, detected_at_utc, classification)
    SELECT observation_id, previous_observation_id, previous_available, new_available, detected_at_utc, classification
    FROM opening_events`).run(), /UNIQUE/);
  await assert.rejects(db.prepare("UPDATE opening_events SET new_available=previous_available").run(), /CHECK/);
});

test("term and CRN maintain separate first-sample baselines", async () => {
  await send(payload());
  for (const change of [{ term: "202810" }, { crn: "17593", course_number: "341" }]) {
    const r = await send(payload({ observations: [course({ total_available: 3, reserved_summary: null, ...change })] }));
    assert.equal(r.status, 200);
  }
  assert.deepEqual(await stats(), [3, 3, 0]);
});

test("Python client roundtrip and SQLite opening parity against real local D1", { timeout: 30_000 }, async () => {
  const python = process.env.PYTHON || (existsSync("../.venv/bin/python") ? "../.venv/bin/python" : "python3");
  // Async child required: Miniflare must keep servicing localhost requests.
  const { spawn } = await import("node:child_process");
  const url = new URL("/v1/checks", await mf.ready).href;
  const child = spawn(python, ["test/parity.py", url], { env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
  let out = "", err = "";
  child.stdout.on("data", (x) => { out += x; }); child.stderr.on("data", (x) => { err += x; });
  const exit = await new Promise((resolve, reject) => { child.on("close", resolve); child.on("error", reject); });
  assert.equal(exit, 0, err);
  const reference = JSON.parse(out);
  const actual = (await rows("observations")).map(({ total_available, unreserved_available, classification, potential_available }) =>
    ({ total_available, unreserved_available, classification, potential_available }));
  assert.deepEqual(actual, reference.observations);
  const events = (await rows("opening_events")).map(({ previous_available, new_available, classification }) =>
    ({ previous_available, new_available, classification }));
  assert.deepEqual(events, reference.events);
  assert.equal((await rows("checks")).length, reference.checks);
});

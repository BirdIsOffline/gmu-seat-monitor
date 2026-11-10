import { after, before, beforeEach, test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createHash, randomUUID } from "node:crypto";
import { Miniflare } from "miniflare";
import worker from "../src/worker.js";

const TOKEN = "synthetic_local_only_token_00000000000000000000000";
const MAX_BYTES = 64 * 1024;
let mf, db;
const migrationFiles = readdirSync(new URL("../migrations/", import.meta.url)).filter((s) => s.endsWith(".sql")).sort();
const migration = (name) => readFileSync(new URL(`../migrations/${name}`, import.meta.url), "utf8")
  .split("-- statement-breakpoint").map((s) => s.replace(/^\s*--[^\n]*(?:\n|$)/gm, "").trim()).filter(Boolean);
const migrations = migrationFiles.flatMap(migration);
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
const instance = (options = {}) => new Miniflare({ modules: true,
  scriptPath: new URL("../src/worker.js", import.meta.url).pathname,
  compatibilityDate: "2026-07-01", d1Databases: ["DB"], bindings: { INGEST_TOKEN: TOKEN },
  host: "127.0.0.1", port: 0,
  outboundService: () => { throw new Error("External network forbidden"); }, ...options,
});
const sample = (minute, total) => payload({
  executed_at_utc: `2026-11-09T00:${String(minute).padStart(2, "0")}:00.000000+00:00`,
  observations: [course({ total_available: total, reserved_summary: null })],
});
const edges = async (database = db) => (await database.prepare(`
  SELECT p.observed_at_utc AS previous_time, n.observed_at_utc AS new_time,
    e.previous_available, e.new_available, e.classification
  FROM opening_events e JOIN observations p ON p.observation_id=e.previous_observation_id
    JOIN observations n ON n.observation_id=e.observation_id
  ORDER BY n.term, n.crn, n.observed_at_utc`).all()).results;

before(async () => {
  mf = instance();
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

test("same-time and changed identity roll back entire batch", async () => {
  const baseline = payload();
  await send(baseline);
  for (const changes of [{},
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
  await assert.rejects(db.prepare("UPDATE observations SET total_available=-1").run(), /IMMUTABLE_OBSERVATION/);
  await assert.rejects(db.prepare("UPDATE checks SET status='failed', error_code=NULL").run(), /CHECK/);
  assert.equal((await db.prepare("PRAGMA foreign_keys").first()).foreign_keys, 1);
  const indexes = (await db.prepare("PRAGMA index_list(observations)").all()).results;
  assert.ok(indexes.some((i) => i.name === "idx_observations_timeline"));
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

test("shuffled Python submissions match chronological SQLite reference and UUID5", { timeout: 30_000 }, async () => {
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
  const actual = (await db.prepare("SELECT * FROM observations ORDER BY observed_at_utc").all()).results
    .map(({ total_available, unreserved_available, classification, potential_available, observation_uuid }) =>
      ({ total_available, unreserved_available, classification, potential_available, observation_uuid }));
  assert.deepEqual(actual, reference.observations);
  const events = (await db.prepare("SELECT * FROM opening_events ORDER BY detected_at_utc").all()).results
    .map(({ previous_available, new_available, classification }) =>
    ({ previous_available, new_available, classification }));
  assert.deepEqual(events, reference.events);
  assert.equal((await rows("checks")).length, reference.checks);
});

test("outbox restart after D1 commit retries identical payload before newer course observation", { timeout: 30_000 }, async () => {
  const { spawn } = await import("node:child_process");
  const python = process.env.PYTHON || (existsSync("../.venv/bin/python") ? "../.venv/bin/python" : "python3");
  const directory = mkdtempSync(join(tmpdir(), "history-outbox-"));
  const url = new URL("/v1/checks", await mf.ready).href;
  async function run(mode) {
    const child = spawn(python, ["test/outbox_roundtrip.py", url, join(directory, "outbox.sqlite3"), mode],
      { env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
    let out = "", err = "";
    child.stdout.on("data", (x) => { out += x; });
    child.stderr.on("data", (x) => { err += x; });
    const deadline = setTimeout(() => child.kill("SIGKILL"), 10_000);
    try {
      const exit = await new Promise((resolve, reject) => { child.on("close", resolve); child.on("error", reject); });
      return { exit, out, err };
    } finally { clearTimeout(deadline); }
  }
  try {
    const interrupted = await run("interrupt");
    assert.equal(interrupted.exit, 23, interrupted.err);
    assert.deepEqual(await stats(), [1, 1, 0]);
    const recovered = await run("recover");
    assert.equal(recovered.exit, 0, recovered.err);
    assert.deepEqual(JSON.parse(recovered.out), { delivered: 2, attempts: [2, 1] });
    assert.deepEqual(await stats(), [2, 2, 1]); // Retry did not add an observation/event.
    const event = (await rows("opening_events"))[0];
    assert.deepEqual([event.previous_available, event.new_available], [0, 1]);
    // Simulate a point-in-time restore to the committed prefix in LOCAL D1 only.
    const newer = "00000000-0000-4000-8000-000000000002";
    await db.batch([
      db.prepare("DELETE FROM opening_events"),
      db.prepare("DELETE FROM observations WHERE check_id=?").bind(newer),
      db.prepare("DELETE FROM checks WHERE check_id=?").bind(newer),
    ]);
    assert.deepEqual(await stats(), [1, 1, 0]);
    const replayed = await run("replay");
    assert.equal(replayed.exit, 0, replayed.err);
    assert.deepEqual(JSON.parse(replayed.out), { replayed: 2 });
    assert.deepEqual(await stats(), [2, 2, 1]);
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test("delayed samples repair both chronological edges without changing stored facts", async () => {
  const first = sample(0, 0), last = sample(10, 2), middle = sample(5, 1);
  await send(first); await send(last);
  const originals = await rows("observations");
  assert.deepEqual((await edges()).map((e) => [e.previous_available, e.new_available]), [[0, 2]]);
  assert.equal((await send(middle)).status, 200);
  assert.deepEqual((await edges()).map((e) => [e.previous_available, e.new_available]), [[0, 1], [1, 2]]);
  assert.deepEqual((await rows("observations")).slice(0, 2), originals);
  // A late unknown sample invalidates the inferred middle -> last opening.
  assert.equal((await send(sample(7, null))).status, 200);
  assert.deepEqual((await edges()).map((e) => [e.previous_available, e.new_available]), [[0, 1]]);
  const beforeRetry = await edges();
  assert.equal((await send(middle)).status, 200);
  assert.deepEqual(await edges(), beforeRetry);
  assert.deepEqual(await stats(), [4, 4, 1]);
});

test("an older baseline creates a successor opening and a late full sample can move it", async () => {
  await send(sample(10, 1));
  assert.equal((await rows("opening_events")).length, 0);
  await send(sample(0, 0));
  assert.equal((await edges())[0].previous_time, sample(0, 0).executed_at_utc);
  // Inserting a positive sample moves the opening earlier, rather than duplicating it.
  await send(sample(5, 1));
  assert.equal((await edges()).length, 1);
  assert.equal((await edges())[0].new_time, sample(5, 1).executed_at_utc);
  // A full sample after that now establishes an additional observed opening.
  await send(sample(7, 0));
  assert.deepEqual((await edges()).map((e) => [e.previous_available, e.new_available]), [[0, 1], [0, 1]]);
});

test("submillisecond timestamps retain their original chronological order", async () => {
  const checks = [0, 1, 2].map((total, i) => ({ ...sample(0, total),
    executed_at_utc: `2026-11-09T00:00:00.00000${i + 1}+00:00`,
  }));
  for (const i of [2, 0, 1]) assert.equal((await send(checks[i])).status, 200);
  assert.deepEqual((await edges()).map((e) => [e.previous_time, e.new_time]), [
    [checks[0].executed_at_utc, checks[1].executed_at_utc],
    [checks[1].executed_at_utc, checks[2].executed_at_utc],
  ]);
});

test("concurrent distinct multi-course writes converge to chronological history", async () => {
  const totals = [0, 2, 1, null, 3, 0, 1, 2, 2, 0, 1, 0];
  const checks = totals.map((total, i) => {
    const p = sample(i, total);
    p.observations.push(course({ crn: "17593", course_number: "341", total_available: 9 + i,
      reserved_summary: { unreservedAvailable: 0 } }));
    return p;
  });
  const replies = await Promise.all([...checks].reverse().map((p) => send(p)));
  assert.ok(replies.every((r) => r.status === 200));
  const actual = await edges();
  assert.deepEqual(actual.map((e) => [e.previous_available, e.new_available]), [[0, 2], [0, 1], [1, 2], [0, 1]]);
  assert.deepEqual(actual.map((e) => e.new_time), [1, 6, 7, 10].map((i) => checks[i].executed_at_utc));
  assert.deepEqual(await stats(), [12, 24, 4]);
  const snapshot = await rows("observations");
  assert.ok((await Promise.all(checks.map((p) => send(p)))).every((r) => r.status === 200));
  assert.deepEqual(await rows("observations"), snapshot);
  assert.deepEqual(await edges(), actual);
});

test("concurrent contradictory UUID reuse and tied sample times fail atomically", async () => {
  const a = sample(0, 0), b = { ...a, duration_seconds: 1 };
  assert.deepEqual((await Promise.all([send(a), send(b)])).map((r) => r.status).sort(), [200, 409]);
  assert.deepEqual(await stats(), [1, 1, 0]);
  // Both submit the same timestamp to a new course: one whole check must lose.
  const tied = [sample(1, 1), sample(1, 2)].map((p, i) => ({ ...p, observations: [
    course({ crn: "17593", course_number: "341" }), course({ crn: String(20000 + i) }),
  ] }));
  assert.deepEqual((await Promise.all(tied.map((p) => send(p)))).map((r) => r.status).sort(), [200, 409]);
  assert.deepEqual(await stats(), [2, 3, 0]);
});

test("interruption during successor repair rolls back receipt, deletion and both edges", async () => {
  await send(sample(0, 0)); await send(sample(10, 2));
  const before = await Promise.all([rows("checks"), rows("observations"), rows("opening_events")]);
  const successor = before[1][1].observation_id;
  await db.prepare(`CREATE TRIGGER fail_successor BEFORE INSERT ON opening_events
    WHEN NEW.observation_id=${successor} BEGIN SELECT RAISE(ABORT, 'synthetic interruption'); END`).run();
  const late = sample(5, 1);
  try {
    for (let i = 0; i < 3; i++) {
      assert.equal((await send(late)).status, 503);
      assert.deepEqual(await Promise.all([rows("checks"), rows("observations"), rows("opening_events")]), before);
    }
  } finally { await db.prepare("DROP TRIGGER fail_successor").run(); }
  assert.equal((await send(late)).status, 200);
  assert.deepEqual(await stats(), [3, 3, 2]);
  assert.equal((await send(late)).status, 200);
  assert.deepEqual(await stats(), [3, 3, 2]);
});

test("maximum batch uses two D1 statements and retains immutable UUIDs and receipt", async () => {
  const p = payload({ observations: Array.from({ length: 50 }, (_, i) => course({ crn: String(10000 + i) })) });
  const boundedDB = { prepare: (sql) => db.prepare(sql), async batch(statements) {
    assert.equal(statements.length, 2);
    return db.batch(statements);
  } };
  assert.equal((await worker.fetch(request(p), { DB: boundedDB, INGEST_TOKEN: TOKEN })).status, 200);
  const originalCheck = await rows("checks"), originals = await rows("observations");
  assert.match(originalCheck[0].received_at_utc, /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}\+00:00$/);
  assert.equal(new Set(originals.map((r) => r.observation_uuid)).size, 50);
  for (const row of originals) assert.match(row.observation_uuid, /^[a-f0-9-]{14}5[a-f0-9-]{21}$/);
  assert.equal((await send(p)).status, 200);
  assert.deepEqual(await rows("checks"), originalCheck);
  assert.deepEqual(await rows("observations"), originals);
  await assert.rejects(db.prepare("UPDATE observations SET observation_uuid=observation_uuid").run(), /IMMUTABLE_OBSERVATION/);
  for (const assignment of ["duration_seconds=1", "received_at_utc=NULL", "executed_at_utc='changed'"]) {
    await assert.rejects(db.prepare(`UPDATE checks SET ${assignment}`).run(), /IMMUTABLE_CHECK/);
  }
  assert.deepEqual(await stats(), [1, 50, 0]);
});

test("0002 migrates populated 0001 without changing observations, events or retry identity", async () => {
  const local = instance();
  try {
    const legacy = await local.getD1Database("DB");
    await legacy.batch(migration("0001_history.sql").map((sql) => legacy.prepare(sql)));
    const checks = [sample(0, 0), sample(10, 2)];
    for (const p of checks) {
      const total = p.observations[0].total_available;
      const canonical = JSON.stringify([1, p.executed_at_utc, .25, "success", null,
        [["202710", "IT", "304", "DL1", "17582", total, null, "null", total ? "possible" : "full", total]]]);
      await legacy.batch([
        legacy.prepare(`INSERT INTO checks VALUES (?, ?, 'success', .25, NULL, ?)`)
          .bind(p.check_id, p.executed_at_utc, createHash("sha256").update(canonical).digest("hex")),
        legacy.prepare(`INSERT INTO observations
          (check_id, term, subject, course_number, section, crn, total_available, unreserved_available,
           reserved_summary_json, observed_at_utc, classification, potential_available)
          VALUES (?, '202710', 'IT', '304', 'DL1', '17582', ?, NULL, 'null', ?, ?, ?)`)
          .bind(p.check_id, total, p.executed_at_utc, total ? "possible" : "full", total),
      ]);
    }
    const beforeObservations = (await legacy.prepare("SELECT * FROM observations ORDER BY observation_id").all()).results;
    const beforeEvents = await edges(legacy);
    assert.equal((await worker.fetch(request(sample(5, 1)), { DB: legacy, INGEST_TOKEN: TOKEN })).status, 503);
    await legacy.batch(migration("0002_ordered_timeline.sql").map((sql) => legacy.prepare(sql)));
    const migrated = (await legacy.prepare("SELECT * FROM observations ORDER BY observation_id").all()).results;
    assert.deepEqual(migrated.map(({ observation_uuid, ...r }) => r), beforeObservations);
    for (const row of migrated) assert.match(row.observation_uuid, /^[a-f0-9-]{14}4[a-f0-9-]{21}$/);
    assert.deepEqual(await edges(legacy), beforeEvents);
    assert.equal((await legacy.prepare("SELECT received_at_utc FROM checks LIMIT 1").first()).received_at_utc, null);
    for (const p of checks) assert.equal((await worker.fetch(request(p), { DB: legacy, INGEST_TOKEN: TOKEN })).status, 200);
    assert.deepEqual((await legacy.prepare("SELECT * FROM observations ORDER BY observation_id").all()).results, migrated);
    assert.equal((await worker.fetch(request(sample(5, 1)), { DB: legacy, INGEST_TOKEN: TOKEN })).status, 200);
    assert.deepEqual((await edges(legacy)).map((e) => [e.previous_available, e.new_available]), [[0, 1], [1, 2]]);
  } finally { await local.dispose(); }
});

test("direct receipt survives local D1 restart and lost acknowledgement without a producer disk", async () => {
  const directory = mkdtempSync(join(tmpdir(), "history-direct-"));
  let local = instance({ d1Persist: directory });
  const early = sample(0, 0), late = sample(10, 2), delayed = sample(5, 1);
  const submit = async (p) => local.dispatchFetch("http://localhost/v1/checks", {
    method: "POST", headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" },
    body: JSON.stringify(p),
  });
  try {
    let durable = await local.getD1Database("DB");
    await durable.batch(migrations.map((sql) => durable.prepare(sql)));
    await submit(late); // Deliberately discard response, as if the reply was lost.
    const committed = await durable.prepare("SELECT * FROM observations").first();
    await local.dispose();
    local = instance({ d1Persist: directory });
    durable = await local.getD1Database("DB");
    assert.deepEqual(await durable.prepare("SELECT * FROM observations").first(), committed);
    assert.equal((await submit(late)).status, 200); // Same retained UUID/body.
    assert.equal((await submit(early)).status, 200);
    assert.equal((await submit(delayed)).status, 200);
    assert.deepEqual((await edges(durable)).map((e) => [e.previous_available, e.new_available]), [[0, 1], [1, 2]]);
    assert.equal((await durable.prepare("SELECT count(*) AS n FROM checks").first()).n, 3);
    assert.deepEqual(await durable.prepare("SELECT * FROM observations WHERE check_id=?").bind(late.check_id).first(), committed);
  } finally { await local.dispose(); rmSync(directory, { recursive: true, force: true }); }
});

test("original partial monitor check survives subprocess submission and lost receipt retry", { timeout: 15_000 }, async () => {
  const { spawn } = await import("node:child_process");
  const python = process.env.PYTHON || (existsSync("../.venv/bin/python") ? "../.venv/bin/python" : "python3");
  const url = new URL("/v1/checks", await mf.ready).href;
  const child = spawn(python, ["test/live_roundtrip.py", url],
    { env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
  let out = "", err = "";
  child.stdout.on("data", (x) => { out += x; });
  child.stderr.on("data", (x) => { err += x; });
  const deadline = setTimeout(() => child.kill("SIGKILL"), 12_000);
  try {
    const exit = await new Promise((resolve, reject) => { child.on("close", resolve); child.on("error", reject); });
    assert.equal(exit, 0, err);
    const result = JSON.parse(out);
    assert.equal(result.lookups, 2);
    assert.equal(result.bark_calls, 1);
    assert.deepEqual(await stats(), [1, 1, 0]);
    const check = (await rows("checks"))[0];
    assert.equal(check.check_id, result.check_id);
    assert.equal(check.status, "failed");
    assert.equal(check.error_code, "COURSE_LOOKUP_FAILED");
    const observation = (await rows("observations"))[0];
    assert.equal(observation.crn, "17582");
    assert.equal(observation.unreserved_available, 1);
    assert.equal(JSON.stringify(observation).includes("PRIVATE_TEXT"), false);
  } finally { clearTimeout(deadline); }
});

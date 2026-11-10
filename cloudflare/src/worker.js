// Direct D1 receipt + chronological projection. No background tasks or outbound fetches.
const MAX_BYTES = 64 * 1024;
const MAX_OBSERVATIONS = 50;
const TOO_LARGE = Symbol("too_large");
const COUNTS = ["unreservedAvailable", "unreservedRemaining", "reservedAvailable", "reservedRemaining"];
const ERRORS = ["TIMEOUT", "LOOKUP_TIMEOUT", "COURSE_LOOKUP_FAILED", "CHECK_FAILED"];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

class InputError extends Error {}
function requireValue(condition) {
  if (!condition) throw new InputError();
}
function keys(value, expected) {
  requireValue(value !== null && typeof value === "object" && !Array.isArray(value));
  requireValue(Object.keys(value).length === expected.length && expected.every((k) => Object.hasOwn(value, k)));
}
function text(value, pattern) {
  requireValue(typeof value === "string" && pattern.exec(value)?.[0] === value);
  return value;
}
function count(value) {
  requireValue(Number.isInteger(value) && value >= 0 && value <= 1_000_000);
  return value;
}

function validate(input) {
  keys(input, ["version", "check_id", "executed_at_utc", "duration_seconds", "status", "error_code", "observations"]);
  requireValue(input.version === 1);
  const id = text(input.check_id, UUID);
  // Fixed-width microsecond UTC timestamps sort exactly like Python's _stamp().
  const stamp = text(input.executed_at_utc, /^(?:[0-9]{4})-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}\+00:00$/);
  const date = new Date(stamp);
  requireValue(!stamp.startsWith("0000") && Number.isFinite(date.getTime())
    && date.toISOString().slice(0, 19) === stamp.slice(0, 19));
  requireValue(typeof input.duration_seconds === "number" && Number.isFinite(input.duration_seconds)
    && input.duration_seconds >= 0 && input.duration_seconds <= 86400);
  requireValue(input.status === "success" || input.status === "failed");
  requireValue(input.status === "success" ? input.error_code === null : ERRORS.includes(input.error_code));
  requireValue(Array.isArray(input.observations) && input.observations.length <= MAX_OBSERVATIONS
    && (input.status === "failed" || input.observations.length > 0));
  const seen = new Set();
  const rows = input.observations.map((o) => {
    keys(o, ["term", "subject", "course_number", "section", "crn", "total_available", "reserved_summary"]);
    const identity = [text(o.term, /^[0-9]{6}$/), text(o.subject, /^[A-Z]{1,10}$/),
      text(o.course_number, /^[0-9A-Z]{1,10}$/), text(o.section, /^[A-Z0-9-]{1,10}$/), text(o.crn, /^[0-9]{1,10}$/)];
    const key = `${o.term}:${o.crn}`;
    requireValue(!seen.has(key));
    seen.add(key);
    const total = o.total_available === null ? null : count(o.total_available);
    let summary = o.reserved_summary;
    let unreserved = null;
    if (summary !== null && summary !== "unknown") {
      requireValue(typeof summary === "object" && !Array.isArray(summary));
      requireValue(Object.keys(summary).every((k) => COUNTS.includes(k)));
      const clean = {};
      // Preserve parser precedence while giving semantically identical dictionaries one hash.
      for (const k of COUNTS) if (Object.hasOwn(summary, k)) clean[k] = count(summary[k]);
      requireValue(!(Object.hasOwn(clean, "unreservedAvailable") && Object.hasOwn(clean, "unreservedRemaining"))
        || clean.unreservedAvailable === clean.unreservedRemaining);
      summary = clean;
      unreserved = clean.unreservedAvailable ?? clean.unreservedRemaining ?? null;
    }
    const absent = summary === null || (typeof summary === "object" && Object.keys(summary).length === 0);
    const kind = total === null ? "unknown" : total === 0 ? "full" : absent ? "possible"
      : unreserved === null ? "unknown" : unreserved === 0 ? "reserved_only" : "unreserved";
    const potential = total === null ? 0 : absent ? total : unreserved === null ? 0 : Math.min(total, unreserved);
    return [...identity, total, unreserved, JSON.stringify(summary), kind, potential];
  });
  rows.sort((a, b) => a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[4] < b[4] ? -1 : a[4] > b[4] ? 1 : 0);
  return { id, stamp, duration: input.duration_seconds, status: input.status, error: input.error_code, rows };
}

function response(status, code, extra = {}) {
  return Response.json({ code, ...extra }, { status, headers: {
    "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
  } });
}

async function authenticated(request, secret) {
  // Fail closed on missing/misconfigured secrets; compare fixed-length digests.
  if (typeof secret !== "string" || /^[A-Za-z0-9_-]{43,128}$/.exec(secret)?.[0] !== secret) return null;
  const header = request.headers.get("Authorization") || "";
  if (!/^Bearer [A-Za-z0-9_-]{43,128}$/.test(header)) return false;
  const hash = async (s) => new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s)));
  const [given, expected] = await Promise.all([hash(header.slice(7)), hash(secret)]);
  let diff = 0;
  for (let i = 0; i < expected.length; i++) diff |= given[i] ^ expected[i];
  return diff === 0;
}

async function readBody(request) {
  const length = request.headers.get("Content-Length");
  if (length !== null) {
    requireValue(/^[0-9]+$/.test(length));
    if (Number(length) > MAX_BYTES) return TOO_LARGE;
  }
  requireValue(!request.headers.has("Content-Encoding"));
  const reader = request.body?.getReader();
  requireValue(reader);
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_BYTES) {
        await reader.cancel();
        return TOO_LARGE;
      }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
}

async function observationUUID(checkId, term, crn) {
  // RFC 9562 UUID5: the immutable check UUID is the namespace, course is the name.
  // Identity is independent of counts, arrival order, row IDs and retries.
  const namespace = Uint8Array.from(checkId.replaceAll("-", "").match(/../g), (s) => parseInt(s, 16));
  const name = new TextEncoder().encode(`${term}:${crn}`);
  const bytes = new Uint8Array(16 + name.length);
  bytes.set(namespace); bytes.set(name, 16);
  const uuid = new Uint8Array(await crypto.subtle.digest("SHA-1", bytes)).slice(0, 16);
  uuid[6] = (uuid[6] & 15) | 0x50;
  uuid[8] = (uuid[8] & 63) | 0x80;
  const hex = [...uuid].map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== "/v1/checks" || url.search) return response(404, "NOT_FOUND");
    if (request.method !== "POST") return response(405, "METHOD_NOT_ALLOWED");
    const auth = await authenticated(request, env.INGEST_TOKEN);
    if (auth === null) return response(503, "UNAVAILABLE");
    if (!auth) return response(401, "UNAUTHORIZED");
    if (!/^application\/json(?:\s*;\s*charset=utf-8)?$/i.test(request.headers.get("Content-Type") || "")) {
      return response(415, "UNSUPPORTED_MEDIA_TYPE");
    }
    let check;
    try {
      const input = await readBody(request);
      if (input === TOO_LARGE) return response(413, "TOO_LARGE");
      check = validate(input);
    } catch { return response(400, "INVALID_REQUEST"); }
    const { id, stamp, duration, status, error, rows } = check;
    const canonical = JSON.stringify([1, stamp, duration, status, error, rows]);
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical));
    const hash = [...new Uint8Array(digest)].map((x) => x.toString(16).padStart(2, "0")).join("");
    try {
      const identified = await Promise.all(rows.map(async (row) => [await observationUUID(id, row[0], row[4]), ...row]));
      const statements = [env.DB.prepare(`INSERT INTO checks
        (check_id, executed_at_utc, status, duration_seconds, error_code, request_hash, received_at_utc)
        VALUES (?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%f000+00:00', 'now'))
        ON CONFLICT(check_id) DO UPDATE SET request_hash=excluded.request_hash`)
        .bind(id, stamp, status, duration, error, hash)];
      // One bounded JSON rowset avoids 51 statements at the 50-course input cap.
      // Two statements and ten bound parameters total, including an empty failure.
      statements.push(env.DB.prepare(`INSERT INTO observations
        (check_id, term, subject, course_number, section, crn, total_available,
         unreserved_available, reserved_summary_json, observed_at_utc, classification,
         potential_available, observation_uuid)
        SELECT ?, json_extract(r.value, '$[1]'), json_extract(r.value, '$[2]'),
          json_extract(r.value, '$[3]'), json_extract(r.value, '$[4]'), json_extract(r.value, '$[5]'),
          json_extract(r.value, '$[6]'), json_extract(r.value, '$[7]'), json_extract(r.value, '$[8]'),
          ?, json_extract(r.value, '$[9]'), json_extract(r.value, '$[10]'), json_extract(r.value, '$[0]')
        FROM json_each(?) r
        WHERE NOT EXISTS (SELECT 1 FROM observations
          WHERE check_id=? AND term=json_extract(r.value, '$[1]') AND crn=json_extract(r.value, '$[5]'))`)
        .bind(id, stamp, JSON.stringify(identified), id));
      // D1 batch is a single transaction; triggers compare/detect within its write lock.
      const results = await env.DB.batch(statements);
      if (results.some((r) => !r.success)) throw new Error("BATCH_FAILED");
      return response(200, "INGESTED", { check_id: id });
    } catch (exc) {
      // Never log request bodies, authorization, raw exceptions, or SQL.
      const conflict = /IDEMPOTENCY_CONFLICT|OBSERVATION_TIME_CONFLICT|COURSE_IDENTITY/.test(String(exc?.message));
      console.warn(conflict ? "history_ingestion_conflict" : "history_ingestion_unavailable");
      return response(conflict ? 409 : 503, conflict ? "CONFLICT" : "UNAVAILABLE");
    }
  },
};

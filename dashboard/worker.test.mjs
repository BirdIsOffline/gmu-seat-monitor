import test from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import worker from './worker.js';

if (!globalThis.crypto) globalThis.crypto = webcrypto;
const token = 't'.repeat(43);
const base = 'https://dashboard.example';

function mockDb() {
  const calls = [];
  const db = {
    prepare(sql) {
      assert.match(sql, /^\s*(?:SELECT|WITH) /);
      assert.doesNotMatch(sql, /\b(?:INSERT|UPDATE|DELETE|DROP|ALTER|CREATE)\b/i);
      return {
        bind(...args) {
          calls.push({ sql, args });
          return {
            async all() {
              if (sql.includes('LAG(executed_at_utc)')) return { results: [{
                measured_intervals: 134, average_minutes: 5, longest_gap_minutes: 5.28,
                gaps_over_10_minutes: 0, gaps_over_15_minutes: 0,
              }] };
              if (sql.includes('FROM checks')) return { results: [{ total: 135, success: 135, failed: 0, latest: '2026-11-10T20:05:18.252629+00:00' }] };
              if (sql.includes('FROM observations')) return { results: [
                { crn: '17582', observed_at_utc: '2026-11-10T20:05:18.252629+00:00', total_available: 0, unreserved_available: null, classification: 'full' },
                { crn: '17593', observed_at_utc: '2026-11-10T20:05:18.252629+00:00', total_available: 3, unreserved_available: null, classification: 'unknown' },
              ] };
              return { results: [] };
            },
          };
        },
      };
    },
  };
  return { db, calls };
}

test('HTML landing page contains no token or database records', async () => {
  const { db, calls } = mockDb();
  const res = await worker.fetch(new Request(base + '/'), { DB: db, DASHBOARD_READ_TOKEN: token });
  assert.equal(res.status, 200);
  assert.match(res.headers.get('Content-Security-Policy'), /connect-src 'self'/);
  assert.match(await res.text(), /Seat availability history/);
  assert.equal(calls.length, 0);
});

test('missing, incorrect, and malformed credentials cannot read history', async () => {
  const { db, calls } = mockDb();
  for (const authorization of [undefined, 'Bearer ' + 'x'.repeat(43), 'Basic ' + token, 'Bearer bad']) {
    const headers = authorization ? { Authorization: authorization } : {};
    const res = await worker.fetch(new Request(base + '/api/dashboard?range=24h', { headers }), { DB: db, DASHBOARD_READ_TOKEN: token });
    assert.equal(res.status, 401);
  }
  assert.equal(calls.length, 0);
});

test('read-only API returns bounded, fixed-query course data', async () => {
  const { db, calls } = mockDb();
  const res = await worker.fetch(new Request(base + '/api/dashboard?range=24h', { headers: { Authorization: 'Bearer ' + token } }), { DB: db, DASHBOARD_READ_TOKEN: token });
  assert.equal(res.status, 200);
  assert.equal(res.headers.get('cache-control'), 'private, no-store');
  const data = await res.json();
  assert.equal(data.health.total, 135);
  assert.equal(data.samples.length, 2);
  assert.equal(data.samples[1].classification, 'unknown');
  assert.equal(data.samples[1].unreserved_available, null);
  assert.deepEqual(data.events, []);
  assert.deepEqual(data.cadence, {
    measured_intervals: 134, average_minutes: 5, longest_gap_minutes: 5.28,
    gaps_over_10_minutes: 0, gaps_over_15_minutes: 0,
  });
  assert.equal(calls.length, 4);
  assert.ok(calls.every(call => call.args.every(arg => typeof arg === 'string')));
  assert.equal(calls[1].args[0], '17582');
  assert.equal(calls[1].args[1], '17593');
});

test('unknown periods and mutation methods are not accepted', async () => {
  const { db, calls } = mockDb();
  for (const url of ['/api/dashboard?range=year', '/api/dashboard?range=24h&sql=DROP', '/api/dashboard', '/v1/checks']) {
    const res = await worker.fetch(new Request(base + url, { headers: { Authorization: 'Bearer ' + token } }), { DB: db, DASHBOARD_READ_TOKEN: token });
    assert.equal(res.status, 404);
  }
  const res = await worker.fetch(new Request(base + '/api/dashboard?range=24h', { method: 'POST', headers: { Authorization: 'Bearer ' + token } }), { DB: db, DASHBOARD_READ_TOKEN: token });
  assert.equal(res.status, 404);
  assert.equal(calls.length, 0);
});

test('fail closed without a dedicated token or database binding', async () => {
  const { db, calls } = mockDb();
  const req = () => new Request(base + '/api/dashboard?range=7d', { headers: { Authorization: 'Bearer ' + token } });
  assert.equal((await worker.fetch(req(), { DB: db })).status, 503);
  assert.equal((await worker.fetch(req(), { DASHBOARD_READ_TOKEN: token })).status, 503);
  assert.equal(calls.length, 0);
});

test('cadence SQL counts intervals only inside the requested window', async () => {
  const { db, calls } = mockDb();
  const response = await worker.fetch(new Request(base + '/api/dashboard?range=7d', {
    headers: { Authorization: 'Bearer ' + token },
  }), { DB: db, DASHBOARD_READ_TOKEN: token });
  assert.equal(response.status, 200);
  const cadence = calls.find(call => call.sql.includes('LAG(executed_at_utc)'));
  assert.ok(cadence);
  assert.match(cadence.sql, /LAG\(executed_at_utc\) OVER \(ORDER BY executed_at_utc\)/);
  assert.match(cadence.sql, /WHERE executed_at_utc >= \?/);
  assert.match(cadence.sql, /previous_check IS NOT NULL/);
  assert.match(cadence.sql, /minutes > 10/);
  assert.match(cadence.sql, /minutes > 15/);
  assert.equal(cadence.args.length, 1);
  assert.match(cadence.args[0], /^\d{4}-\d{2}-\d{2}T/);
  const data = await response.json();
  assert.equal(data.range, '7d');
  assert.equal(data.cadence.longest_gap_minutes, 5.28);
});

test('HTML labels cadence as recorded, not guaranteed trigger delivery', async () => {
  const { db } = mockDb();
  const response = await worker.fetch(new Request(base + '/'), { DB: db, DASHBOARD_READ_TOKEN: token });
  const html = await response.text();
  assert.match(html, /Recorded check cadence/);
  assert.match(html, /Does not measure missed triggers/);
  assert.match(html, /id="gapIntervals"/);
  assert.match(html, /id="gapLongest"/);
});

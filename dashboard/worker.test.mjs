import test from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';
import { DatabaseSync } from 'node:sqlite';
import vm from 'node:vm';
import worker, { classifyFreshness, monitoringSummary, COURSES } from './worker.js';

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
              if (sql.includes('FROM checks')) return { results: [{ total: 135, success: 135, failed: 0, latest: '2026-11-10T20:05:18.252629+00:00', latest_overall: '2026-11-10T20:05:18.252629+00:00' }] };
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
  assert.equal(data.health.latest_overall, '2026-11-10T20:05:18.252629+00:00');
  assert.ok(Number.isFinite(Date.parse(data.generated_at_utc)));
  assert.ok(['healthy','delayed','stale'].includes(data.freshness.state));
  assert.equal(data.samples.length, 2);
  assert.equal(data.samples[1].classification, 'unknown');
  assert.equal(data.samples[1].unreserved_available, null);
  assert.deepEqual(data.events, []);
  assert.deepEqual(data.cadence, {
    measured_intervals: 134, average_minutes: 5, longest_gap_minutes: 5.28,
    gaps_over_10_minutes: 0, gaps_over_15_minutes: 0,
  });
  assert.equal(calls.length, 5);
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

test('freshness classifier handles exact thresholds and missing/future timestamps', () => {
  const now = Date.parse('2026-11-10T23:00:00.000Z');
  const ago = minutes => new Date(now - minutes * 60000).toISOString();
  assert.deepEqual(classifyFreshness(ago(0), now), { state: 'healthy', age_minutes: 0 });
  assert.deepEqual(classifyFreshness(ago(10), now), { state: 'healthy', age_minutes: 10 });
  assert.equal(classifyFreshness(ago(10.01), now).state, 'delayed');
  assert.equal(classifyFreshness(ago(15), now).state, 'delayed');
  assert.equal(classifyFreshness(ago(15.01), now).state, 'stale');
  assert.deepEqual(classifyFreshness(null, now), { state: 'no_data', age_minutes: null });
  assert.equal(classifyFreshness('invalid-time', now).state, 'clock_error');
  assert.equal(classifyFreshness(new Date(now + 3 * 60000).toISOString(), now).state, 'clock_error');
  assert.deepEqual(classifyFreshness(new Date(now + 60000).toISOString(), now), { state: 'healthy', age_minutes: 0 });
});

test('freshness uses active observations across all history, independent of chart range', async () => {
  const { db, calls } = mockDb();
  const request = new Request(base + '/api/dashboard?range=7d', {
    headers: { Authorization: 'Bearer ' + token },
  });
  const response = await worker.fetch(request, { DB: db, DASHBOARD_READ_TOKEN: token });
  assert.equal(response.status, 200);
  const data = await response.json();
  assert.equal(data.health.latest_overall, '2026-11-10T20:05:18.252629+00:00');
  assert.equal(data.freshness.state, 'stale');
  const healthQuery = calls.find(call => call.sql.includes('FROM checks') && !call.sql.includes('LAG(executed_at_utc)'));
  assert.match(healthQuery.sql, /SELECT MAX\(executed_at_utc\) FROM checks/);
});

test('dashboard displays freshness and supports explicit refresh without extra requests in background', async () => {
  const { db, calls } = mockDb();
  const response = await worker.fetch(new Request(base + '/'), { DB: db, DASHBOARD_READ_TOKEN: token });
  const html = await response.text();
  assert.match(html, /Active-course freshness/);
  assert.match(html, /id="freshnessState"/);
  assert.match(html, /id="refreshHistory"/);
  assert.match(html, /setInterval\(paintFreshness,60000\)/);
  assert.match(html, /new records load only when you click Refresh history/);
  assert.equal(calls.length, 0);
});


const now = Date.parse('2026-11-10T23:00:00.000Z');
const observed = (crn, minutes, at = now) => ({ crn,
  observed_at_utc: new Date(at - minutes * 60000).toISOString(), total_available: 3,
  unreserved_available: null, classification: 'unknown' });

test('retired course is history-only and never a stale active target', () => {
  const data = monitoringSummary([observed('17582', 10000), observed('17593', 5)], now);
  assert.equal(data.courses[0].status, 'retired');
  assert.deepEqual(data.courses[0].freshness, { state: 'retired', age_minutes: null });
  assert.equal(data.courses[0].latest.total_available, 3);
  assert.deepEqual(data.freshness, { state: 'healthy', age_minutes: 5 });
});

test('fresh retired observations cannot mask a stale or missing active course', () => {
  assert.equal(monitoringSummary([observed('17582', 0), observed('17593', 30)], now).freshness.state, 'stale');
  assert.equal(monitoringSummary([observed('17582', 0)], now).freshness.state, 'no_data');
  assert.equal(monitoringSummary([], now).freshness.state, 'no_data');
});

test('only active courses determine missing, clock-error and worst-course freshness', () => {
  const bothActive = COURSES.map(c => ({ ...c, status: 'active' }));
  assert.equal(monitoringSummary([observed('17582', 30), observed('17593', 5)], now, bothActive).freshness.state, 'stale');
  assert.equal(monitoringSummary([observed('17593', 5)], now, bothActive).freshness.state, 'no_data');
  assert.equal(monitoringSummary([observed('17582', -5), observed('17593', 5)], now).freshness.state, 'healthy');
  assert.equal(monitoringSummary([observed('17593', -5)], now).freshness.state, 'clock_error');
});

test('all-retired configuration is inactive, and invalid status fails closed', () => {
  const retired = COURSES.map(c => ({ ...c, status: 'retired' }));
  assert.deepEqual(monitoringSummary([], now, retired).freshness, { state: 'inactive', age_minutes: null });
  assert.throws(() => monitoringSummary([], now, [{ ...COURSES[0], status: 'retire' }]), /INVALID_COURSE_STATUS/);
});

// Execute the fixed dashboard SQL against disposable in-memory SQLite, not only mocks.
function sqliteFixture() {
  const sql = new DatabaseSync(':memory:');
  sql.exec(`CREATE TABLE checks(check_id TEXT, executed_at_utc TEXT, status TEXT);
    CREATE TABLE observations(observation_id INTEGER, check_id TEXT, crn TEXT,
      observed_at_utc TEXT, total_available INTEGER, unreserved_available INTEGER, classification TEXT);
    CREATE TABLE opening_events(observation_id INTEGER, detected_at_utc TEXT,
      previous_available INTEGER, new_available INTEGER, classification TEXT);`);
  const stamp = minutes => new Date(Date.now()-minutes*60000).toISOString().replace('Z','000+00:00');
  const add = (id, crn, minutes) => {
    const at = stamp(minutes);
    sql.prepare('INSERT INTO checks VALUES (?, ?, ?)').run(String(id), at, 'success');
    sql.prepare('INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?)').run(id,String(id),crn,at,3,null,'unknown');
    return at;
  };
  const db = { prepare(query) {
    assert.match(query, /^\s*(?:SELECT|WITH) /);
    assert.doesNotMatch(query, /\b(?:INSERT|UPDATE|DELETE|DROP|ALTER|CREATE)\b/i);
    return { bind: (...args) => ({ all: async () => ({ results: sql.prepare(query).all(...args) }) }) };
  } };
  return { sql, db, add };
}
const apiRequest = range => new Request(base+'/api/dashboard?range='+range, { headers: { Authorization:'Bearer '+token } });

test('real SQL preserves retired samples/events, with latest snapshot outside chart range', async () => {
  const { sql, db, add } = sqliteFixture();
  try {
    const old = add(1,'17582',8*24*60);
    add(2,'17582',30);add(3,'17593',5);
    sql.prepare('INSERT INTO opening_events VALUES (?, ?, ?, ?, ?)').run(2,new Date(Date.now()-30*60000).toISOString(),0,3,'possible_increase');
    const before = JSON.stringify(sql.prepare('SELECT * FROM observations').all());
    for (const range of ['24h','7d']) {
      const data = await (await worker.fetch(apiRequest(range), { DB:db, DASHBOARD_READ_TOKEN:token })).json();
      assert.equal(data.health.total,2);
      assert.equal(data.samples.length,2);
      assert.equal(data.events[0].crn,'17582');
      assert.equal(data.courses[0].status,'retired');
      assert.equal(data.freshness.state,'healthy');
      assert.notEqual(data.courses[0].latest.observed_at_utc,old);
      assert.equal(data.cadence.measured_intervals,1);
    }
    assert.equal(JSON.stringify(sql.prepare('SELECT * FROM observations').all()),before);
    // A retired course can age out of both chart windows and still retain its last stored snapshot.
    sql.prepare('DELETE FROM opening_events').run();
    sql.prepare('DELETE FROM observations WHERE observation_id=2').run();
    const data = await (await worker.fetch(apiRequest('7d'), { DB:db, DASHBOARD_READ_TOKEN:token })).json();
    assert.equal(data.samples.length,1);
    assert.equal(data.courses[0].latest.observed_at_utc,old);
    assert.equal(data.courses[0].freshness.state,'retired');
    assert.equal(JSON.parse(before).length,3);
  } finally { sql.close(); }
});

test('real SQL cannot use a fresh retired-only check to mark active monitoring healthy', async () => {
  const { sql, db, add } = sqliteFixture();
  try {
    add(1,'17593',30);add(2,'17582',0);
    const before = JSON.stringify(sql.prepare('SELECT * FROM observations').all());
    const data = await (await worker.fetch(apiRequest('24h'), { DB:db, DASHBOARD_READ_TOKEN:token })).json();
    assert.equal(data.health.total,2);assert.equal(data.health.success,2);
    assert.equal(data.freshness.state,'stale');
    assert.equal(data.courses[0].freshness.state,'retired');
    assert.equal(JSON.stringify(sql.prepare('SELECT * FROM observations').all()),before);
  } finally { sql.close(); }
});

test('browser rendering labels retired history, computes counts, and ages only active freshness', async () => {
  const { db } = mockDb();
  const html = await (await worker.fetch(new Request(base+'/'), {})).text();
  const data = await (await worker.fetch(apiRequest('24h'), { DB:db, DASHBOARD_READ_TOKEN:token })).json();
  Object.assign(data,monitoringSummary([observed('17582',10000),observed('17593',5)],now));
  const elements = new Map();
  const element = () => ({ textContent:'', children:[], hidden:false,
    appendChild(child){this.children.push(child)},replaceChildren(){this.children=[]},
    setAttribute(){},addEventListener(){},getContext(){return { scale(){},clearRect(){},beginPath(){},moveTo(){},lineTo(){},stroke(){},fillText(){} }} });
  const get = id => {if(!elements.has(id))elements.set(id,element());return elements.get(id)};
  let elapsed = 0, requests = 0;
  const context = vm.createContext({ document:{ getElementById:get,createElement:element,querySelectorAll:()=>[] },
    window:{ addEventListener(){},devicePixelRatio:1 },performance:{now:()=>elapsed},
    setInterval(){},Date,console,fetch:async()=>{requests++;return { ok:true,json:async()=>data }} });
  vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],context);
  await vm.runInContext('load()',context);
  assert.match(get('label304').textContent,/Retired.*history only/);
  assert.match(get('legend341').textContent,/Active/);
  assert.match(get('coverage').textContent,/1 active \/ 1 retired/);
  assert.equal(get('latestRows').children.length,2);
  assert.equal(get('latestRows').children[0].children[1].textContent,'Retired · history only');
  assert.equal(get('freshnessState').textContent,'Healthy');
  elapsed=11*60000;vm.runInContext('paintFreshness()',context);
  assert.equal(get('freshnessState').textContent,'Stale');
  assert.equal(requests,1);
});

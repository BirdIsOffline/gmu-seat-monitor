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
                { crn: '17596', observed_at_utc: '2026-11-10T20:05:18.252629+00:00', total_available: 2, unreserved_available: null, classification: 'unknown' },
                { crn: '17599', observed_at_utc: '2026-11-10T20:05:18.252629+00:00', total_available: 7, unreserved_available: null, classification: 'unknown' },
                { crn: '20981', observed_at_utc: '2026-11-10T20:05:18.252629+00:00', total_available: 11, unreserved_available: null, classification: 'unknown' },
                { crn: '21051', observed_at_utc: '2026-11-10T20:05:18.252629+00:00', total_available: 16, unreserved_available: 0, classification: 'reserved_only' },
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
  assert.equal(data.samples.length, 6);
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
  assert.deepEqual(calls[1].args.slice(0,6), COURSES.map(c=>c.crn));
  assert.match(calls[1].sql, /LIMIT 14000/);
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

const withNewSections = rows => [...rows, observed('17596',5), observed('17599',5), observed('20981',5)];

test('retired course is history-only and never a stale active target', () => {
  const data = monitoringSummary(withNewSections([observed('17582', 10000), observed('17593', 5), observed('21051', 5)]), now);
  assert.equal(data.courses[0].status, 'retired');
  assert.deepEqual(data.courses[0].freshness, { state: 'retired', age_minutes: null });
  assert.equal(data.courses[0].latest.total_available, 3);
  assert.deepEqual(data.freshness, { state: 'healthy', age_minutes: 5 });
});

test('fresh retired observations cannot mask a stale or missing active course', () => {
  assert.equal(monitoringSummary(withNewSections([observed('17582', 0), observed('17593', 30), observed('21051', 5)]), now).freshness.state, 'stale');
  assert.equal(monitoringSummary([observed('17582', 0)], now).freshness.state, 'no_data');
  assert.equal(monitoringSummary([], now).freshness.state, 'no_data');
});

test('only active courses determine missing, clock-error and worst-course freshness', () => {
  const bothActive = COURSES.filter(c=>['17582','17593'].includes(c.crn)).map(c => ({ ...c, status: 'active' }));
  assert.equal(monitoringSummary([observed('17582', 30), observed('17593', 5)], now, bothActive).freshness.state, 'stale');
  assert.equal(monitoringSummary([observed('17593', 5)], now, bothActive).freshness.state, 'no_data');
  assert.equal(monitoringSummary(withNewSections([observed('17582', -5), observed('17593', 5), observed('21051', 5)]), now).freshness.state, 'healthy');
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
    add(2,'17582',30);add(3,'17593',5);add(4,'21051',5);add(5,'17599',5);add(6,'20981',5);add(7,'17596',5);
    sql.prepare('INSERT INTO opening_events VALUES (?, ?, ?, ?, ?)').run(2,new Date(Date.now()-30*60000).toISOString(),0,3,'possible_increase');
    const before = JSON.stringify(sql.prepare('SELECT * FROM observations').all());
    for (const range of ['24h','7d']) {
      const data = await (await worker.fetch(apiRequest(range), { DB:db, DASHBOARD_READ_TOKEN:token })).json();
      assert.equal(data.health.total,6);
      assert.equal(data.samples.length,6);
      assert.equal(data.events[0].crn,'17582');
      assert.equal(data.courses[0].status,'retired');
      assert.equal(data.freshness.state,'healthy');
      assert.notEqual(data.courses[0].latest.observed_at_utc,old);
      assert.equal(data.cadence.measured_intervals,5);
    }
    assert.equal(JSON.stringify(sql.prepare('SELECT * FROM observations').all()),before);
    // A retired course can age out of both chart windows and still retain its last stored snapshot.
    sql.prepare('DELETE FROM opening_events').run();
    sql.prepare('DELETE FROM observations WHERE observation_id=2').run();
    const data = await (await worker.fetch(apiRequest('7d'), { DB:db, DASHBOARD_READ_TOKEN:token })).json();
    assert.equal(data.samples.length,5);
    assert.equal(data.courses[0].latest.observed_at_utc,old);
    assert.equal(data.courses[0].freshness.state,'retired');
    assert.equal(JSON.parse(before).length,7);
  } finally { sql.close(); }
});

test('real SQL cannot use a fresh retired-only check to mark active monitoring healthy', async () => {
  const { sql, db, add } = sqliteFixture();
  try {
    add(1,'17593',30);add(2,'17582',0);add(3,'21051',5);add(4,'17599',5);add(5,'20981',5);add(6,'17596',5);
    const before = JSON.stringify(sql.prepare('SELECT * FROM observations').all());
    const data = await (await worker.fetch(apiRequest('24h'), { DB:db, DASHBOARD_READ_TOKEN:token })).json();
    assert.equal(data.health.total,6);assert.equal(data.health.success,6);
    assert.equal(data.freshness.state,'stale');
    assert.equal(data.courses[0].freshness.state,'retired');
    assert.equal(JSON.stringify(sql.prepare('SELECT * FROM observations').all()),before);
  } finally { sql.close(); }
});

test('browser rendering labels retired history, computes counts, and ages only active freshness', async () => {
  const { db } = mockDb();
  const html = await (await worker.fetch(new Request(base+'/'), {})).text();
  const data = await (await worker.fetch(apiRequest('24h'), { DB:db, DASHBOARD_READ_TOKEN:token })).json();
  Object.assign(data,monitoringSummary(withNewSections([observed('17582',10000),observed('17593',5),{...observed('21051',5),total_available:16,unreserved_available:0,classification:'reserved_only'}]),now));
  data.courses.find(c=>c.crn==='17596').latest.total_available=2;
  data.courses.find(c=>c.crn==='17599').latest.total_available=7;
  data.courses.find(c=>c.crn==='20981').latest.total_available=11;
  data.samples=COURSES.flatMap(c=>[5,1].map(minutes=>({...observed(c.crn,minutes,Date.now()),total_available:({17599:7,20981:11,21051:16}[c.crn]??3)})));
  const strokes=[];
  const elements = new Map();
  const element = () => ({ textContent:'', children:[], hidden:false,
    appendChild(child){this.children.push(child)},replaceChildren(){this.children=[]},
    setAttribute(){},addEventListener(){},getContext(){return { scale(){},clearRect(){},beginPath(){},moveTo(){},lineTo(){},stroke(){strokes.push(this.strokeStyle)},fillText(){} }} });
  const get = id => {if(!elements.has(id))elements.set(id,element());return elements.get(id)};
  let elapsed = 0, requests = 0;
  const context = vm.createContext({ document:{ getElementById:get,createElement:element,querySelectorAll:()=>[] },
    window:{ addEventListener(){},devicePixelRatio:1 },performance:{now:()=>elapsed},
    setInterval(){},Date,console,fetch:async()=>{requests++;return { ok:true,json:async()=>data }} });
  vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],context);
  await vm.runInContext('load()',context);
  assert.match(get('label17582').textContent,/Retired.*history only/);
  assert.match(get('legend17593').textContent,/Active/);
  assert.match(get('legend21051').textContent,/IT 343 DL3.*Active/);
  assert.match(get('label21051').textContent,/IT 343 DL3.*Active/);
  assert.equal(get('it21051').textContent,'16');
  assert.equal(get('it17596').textContent,'2');
  assert.match(get('label17596').textContent,/IT 342 DL1.*17596.*Active/);
  assert.match(get('legend17596').textContent,/IT 342 DL1.*17596/);
  assert.equal(get('it17599').textContent,'7');
  assert.equal(get('it20981').textContent,'11');
  for(const [crn,section] of [['17599','DL1'],['20981','DL2'],['21051','DL3']]){
    assert.ok(get('label'+crn).textContent.includes('IT 343 '+section+' · '+crn));
    assert.ok(get('legend'+crn).textContent.includes('IT 343 '+section+' · '+crn));
  }
  for(const color of ['#6bc7ff','#6dd5ef','#fbbf74','#c4a0ff','#ff9bb5','#7addaa'])assert.ok(strokes.includes(color),'missing chart series '+color);
  assert.match(get('coverage').textContent,/5 active \/ 1 retired/);
  assert.equal(get('latestRows').children.length,6);
  assert.equal(get('latestRows').children[0].children[1].textContent,'Retired · history only');
  assert.equal(get('freshnessState').textContent,'Healthy');
  elapsed=11*60000;vm.runInContext('paintFreshness()',context);
  assert.equal(get('freshnessState').textContent,'Stale');
  assert.equal(requests,1);
  assert.match(get('eventsRows').children[0].children[0].textContent,/No qualifying/);
  data.events=['17599','20981','21051'].map(crn=>({crn,detected_at_utc:new Date(now).toISOString(),previous_available:0,new_available:1,classification:'unreserved_increase'}));
  await vm.runInContext('load()',context);
  assert.equal(get('eventsRows').children.length,3);
  for(const [index,course] of data.courses.filter(c=>c.number==='343').entries()){
    assert.equal(get('eventsRows').children[index].children[1].textContent,course.name+' · '+course.crn);
  }
  assert.equal(requests,2);
  for(const crn of ['17596','17599','20981','21051']){
    const rows=COURSES.filter(c=>c.crn!==crn).map(c=>observed(c.crn,1));
    Object.assign(data,monitoringSummary(rows,now));
    await vm.runInContext('load()',context);
    assert.equal(get('it'+crn).textContent,'—');
    assert.equal(get('freshnessState').textContent,'No active-course records');
  }
});


test('IT343 must have a real observation before active freshness can be healthy', () => {
  assert.equal(monitoringSummary([observed('17593',1)],now).freshness.state,'no_data');
  assert.equal(monitoringSummary(withNewSections([observed('17593',1),observed('21051',16)]),now).freshness.state,'stale');
  assert.equal(monitoringSummary(withNewSections([observed('17593',12),observed('21051',1)]),now).freshness.state,'delayed');
  const result=monitoringSummary(withNewSections([observed('17582',10000),observed('17593',1),observed('21051',1)]),now);
  assert.equal(result.freshness.state,'healthy');
  assert.equal(result.courses.filter(c=>c.status==='active').length,5);
});

test('IT343 missing data is not filled with the reference snapshot or treated as zero', async () => {
  const { db,sql }=sqliteFixture();
  const data=await (await worker.fetch(apiRequest('24h'),{DB:db,DASHBOARD_READ_TOKEN:token})).json();
  const target=data.courses.find(c=>c.crn==='21051');
  assert.equal(target.latest,null);assert.equal(target.status,'active');
  assert.equal(target.freshness.state,'no_data');assert.equal(data.freshness.state,'no_data');
  assert.deepEqual(data.samples,[]);sql.close();
});

test('real SQL returns IT343 DL3 samples and events without losing other course history', async () => {
  const {sql,db,add}=sqliteFixture();
  try {
    add(1,'17582',30);add(2,'17593',2);add(3,'21051',1);add(5,'17599',1);add(6,'20981',1);add(7,'17596',1);
    sql.prepare('UPDATE observations SET total_available=16,unreserved_available=0,classification=? WHERE crn=?').run('reserved_only','21051');
    add(4,'21051',0);
    sql.prepare('UPDATE observations SET total_available=16,unreserved_available=1,classification=? WHERE observation_id=4').run('unreserved');
    sql.prepare('INSERT INTO opening_events VALUES (?, ?, ?, ?, ?)').run(4,new Date().toISOString(),0,1,'unreserved_increase');
    const before=JSON.stringify(sql.prepare('SELECT * FROM observations').all());
    const data=await (await worker.fetch(apiRequest('7d'),{DB:db,DASHBOARD_READ_TOKEN:token})).json();
    assert.equal(data.samples.length,7);assert.equal(data.events[0].crn,'21051');
    const target=data.courses.find(c=>c.crn==='21051');
    assert.equal(target.section,'DL3');assert.equal(target.latest.classification,'unreserved');
    assert.equal(target.latest.unreserved_available,1);
    assert.ok(data.samples.some(s=>s.crn==='21051'&&s.classification==='reserved_only'&&s.unreserved_available===0));assert.equal(data.freshness.state,'healthy');
    assert.equal(JSON.stringify(sql.prepare('SELECT * FROM observations').all()),before);
  } finally {sql.close()}
});


test('each IT343 section independently limits freshness or reports missing data', () => {
  const full=COURSES.map(c=>observed(c.crn,c.status==='retired'?10000:1));
  assert.equal(monitoringSummary(full,now).freshness.state,'healthy');
  for(const crn of ['17596','17599','20981','21051']){
    const missing=monitoringSummary(full.filter(o=>o.crn!==crn),now);
    assert.equal(missing.freshness.state,'no_data');
    assert.equal(missing.courses.find(c=>c.crn===crn).latest,null);
    for(const [minutes,state] of [[12,'delayed'],[20,'stale'],[-5,'clock_error']]){
      const result=monitoringSummary(full.map(o=>o.crn===crn?observed(crn,minutes):o),now);
      assert.equal(result.freshness.state,state,crn+' must independently limit freshness');
    }
  }
});

test('HTML has unique CRN-based cards and legends for all six configured identities', async () => {
  const html=await (await worker.fetch(new Request(base+'/'),{})).text();
  const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(match=>match[1]);
  assert.equal(new Set(ids).size,ids.length);
  for(const course of COURSES){
    for(const prefix of ['label','it','legend'])assert.ok(ids.includes(prefix+course.crn));
  }
  assert.doesNotMatch(html,/const suffix=course\.number/);
});

test('real SQL separates all three IT343 latest rows, samples and opening events', async () => {
  const {sql,db,add}=sqliteFixture();
  try{
    add(1,'17582',100);add(2,'17593',1);add(9,'17596',1);
    let id=3;
    for(const [crn,total] of [['17599',7],['20981',11],['21051',16]]){
      for(const minutes of [5,1]){
        add(id,crn,minutes);
        sql.prepare('UPDATE observations SET total_available=? WHERE observation_id=?').run(total,id);
        if(minutes===1)sql.prepare('INSERT INTO opening_events VALUES (?,?,?,?,?)').run(id,new Date().toISOString(),0,total,'unreserved_increase');
        id++;
      }
    }
    const before=JSON.stringify(sql.prepare('SELECT * FROM observations').all());
    for(const range of ['24h','7d']){
      const data=await (await worker.fetch(apiRequest(range),{DB:db,DASHBOARD_READ_TOKEN:token})).json();
      assert.equal(data.courses.length,6);assert.equal(data.samples.length,9);
      assert.deepEqual(new Set(data.events.map(e=>e.crn)),new Set(['17599','20981','21051']));
      for(const [crn,total,section] of [['17599',7,'DL1'],['20981',11,'DL2'],['21051',16,'DL3']]){
        const course=data.courses.find(c=>c.crn===crn);
        assert.equal(course.section,section);assert.equal(course.latest.total_available,total);
        assert.equal(data.samples.filter(s=>s.crn===crn).length,2);
      }
      assert.equal(data.freshness.state,'healthy');
    }
    assert.equal(JSON.stringify(sql.prepare('SELECT * FROM observations').all()),before);
  }finally{sql.close()}
});


test('seven-day query retains seven days for all six configured identities', async () => {
  const {sql,db,add}=sqliteFixture();
  try{
    sql.exec('BEGIN');let id=1;
    for(const course of COURSES){
      for(let sample=0;sample<2016;sample++)add(id++,course.crn,1+sample*5);
    }
    sql.exec('COMMIT');
    const data=await (await worker.fetch(apiRequest('7d'),{DB:db,DASHBOARD_READ_TOKEN:token})).json();
    assert.equal(data.samples.length,12096);
    for(const course of COURSES.filter(c=>c.status==='active')){
      assert.equal(data.samples.filter(s=>s.crn===course.crn).length,2016);
    }
    assert.equal(data.freshness.state,'healthy');
  }finally{sql.close()}
});


test('IT342 needs its own observation and keeps an independent SQL history', async () => {
  const {sql,db,add}=sqliteFixture();
  try{
    let id=1;for(const c of COURSES.filter(c=>c.crn!=='17596'))add(id++,c.crn,2);
    const before=JSON.stringify(sql.prepare('SELECT * FROM observations').all());
    let data=await (await worker.fetch(apiRequest('7d'),{DB:db,DASHBOARD_READ_TOKEN:token})).json();
    assert.equal(data.freshness.state,'no_data');assert.equal(data.courses.find(c=>c.crn==='17596').latest,null);
    add(id,'17596',1);sql.prepare('INSERT INTO opening_events VALUES(?,?,?,?,?)').run(id,new Date().toISOString(),0,1,'unreserved_increase');
    data=await (await worker.fetch(apiRequest('7d'),{DB:db,DASHBOARD_READ_TOKEN:token})).json();
    const c=data.courses.find(c=>c.crn==='17596');assert.equal(c.number,'342');assert.equal(c.section,'DL1');assert.equal(c.status,'active');
    assert.equal(data.freshness.state,'healthy');assert.equal(data.events[0].crn,'17596');
    assert.equal(JSON.stringify(sql.prepare('SELECT * FROM observations WHERE crn != ?').all('17596')),before);
  }finally{sql.close()}
});


function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

async function requestFixture() {
  const html = await (await worker.fetch(new Request(base + '/'), {})).text();
  const { db } = mockDb();
  const data = await (await worker.fetch(apiRequest('24h'), { DB: db, DASHBOARD_READ_TOKEN: token })).json();
  const elements = new Map(), requests = [], alerts = [];
  const element = () => ({ textContent: '', hidden: false, children: [], attributes: {}, listeners: {},
    appendChild(child) { this.children.push(child); }, replaceChildren() { this.children = []; },
    setAttribute(name, value) { this.attributes[name] = value; },
    addEventListener(name, fn) { this.listeners[name] = fn; },
    getContext() { return { scale() {}, clearRect() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {}, fillText() {} }; } });
  const get = id => { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); };
  const buttons = ['24h', '7d'].map(range => Object.assign(element(), { dataset: { range } }));
  const context = vm.createContext({ document: { getElementById: get, createElement: element, querySelectorAll: () => buttons },
    window: { addEventListener() {}, devicePixelRatio: 1 }, performance: { now: () => 0 },
    Date, console, setInterval() {}, alert: message => alerts.push(message),
    fetch: url => { const response = deferred(); requests.push({ url, ...response }); return response.promise; } });
  vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1], context);
  return { html, context, get, requests, alerts,
    login() { get('token').value = token; return get('loginForm').listeners.submit({ preventDefault() {} }); },
    range(value) { return buttons.find(btn => btn.dataset.range === value).listeners.click(); },
    refresh() { return get('refreshHistory').listeners.click(); },
    response(total) { return { ok: true, json: async () => ({ ...data, health: { ...data.health, total } }) }; } };
}

test('slower old range responses cannot overwrite newer data, freshness or status', async () => {
  const f = await requestFixture();
  const old = f.login(), latest = f.range('7d');
  assert.match(f.requests[0].url, /range=24h$/);
  assert.match(f.requests[1].url, /range=7d$/);
  f.requests[1].resolve(f.response(77)); await latest;
  const status = f.get('status').textContent, freshness = f.get('freshnessState').textContent;
  f.requests[0].resolve(f.response(11)); await old;
  assert.equal(f.get('checks').textContent, 77);
  assert.equal(f.get('status').textContent, status);
  assert.equal(f.get('freshnessState').textContent, freshness);
  assert.equal(vm.runInContext('activeRange', f.context), '7d');
  assert.equal(f.get('dashboard').hidden, false);
});

test('range changes also reject older responses whose JSON body resolves last', async () => {
  const f = await requestFixture(), body = deferred();
  let readingBody = false;
  const old = f.login();
  f.requests[0].resolve({ ok: true, json() { readingBody = true; return body.promise; } });
  await new Promise(setImmediate);
  assert.equal(readingBody, true);
  const latest = f.range('7d');
  f.requests[1].resolve(f.response(77)); await latest;
  const status = f.get('status').textContent;
  body.resolve(await f.response(11).json()); await old;
  assert.equal(f.get('checks').textContent, 77);
  assert.equal(f.get('status').textContent, status);
});

test('stale HTTP, network and JSON errors cannot log out or overwrite a newer range', async () => {
  for (const failure of ['401', '503', 'network', 'json']) {
    const f = await requestFixture(), body = deferred();
    const old = f.login();
    if (failure === 'json') {
      f.requests[0].resolve({ ok: true, json: () => body.promise });
      await new Promise(setImmediate);
    }
    const latest = f.range('7d');
    f.requests[1].resolve(f.response(77)); await latest;
    const status = f.get('status').textContent;
    if (failure === 'network') f.requests[0].reject(new Error('old network failure'));
    else if (failure === 'json') body.reject(new Error('old invalid JSON'));
    else f.requests[0].resolve({ ok: false, status: Number(failure) });
    await old;
    assert.equal(f.get('checks').textContent, 77, failure);
    assert.equal(f.get('status').textContent, status, failure);
    assert.equal(f.get('status').attributes.role, 'status', failure);
    assert.equal(f.get('dashboard').hidden, false, failure);
    assert.equal(f.get('login').hidden, true, failure);
    assert.equal(vm.runInContext('readToken', f.context), token, failure);
    assert.deepEqual(f.alerts, [], failure);
  }
});

test('the newest error remains visible when an older success arrives later', async () => {
  const f = await requestFixture();
  const old = f.login(), latest = f.range('7d');
  f.requests[1].resolve({ ok: false, status: 503 }); await latest;
  assert.match(f.get('status').textContent, /unavailable \(503\)/);
  assert.equal(f.get('status').attributes.role, 'alert');
  const message = f.get('status').textContent;
  f.requests[0].resolve(f.response(11)); await old;
  assert.equal(f.get('checks').textContent, '');
  assert.equal(f.get('status').textContent, message);
  assert.equal(f.get('status').attributes.role, 'alert');
});

test('request ordering also protects refreshes within the same range', async () => {
  const f = await requestFixture();
  const initial = f.login(); f.requests[0].resolve(f.response(1)); await initial;
  const old = f.refresh(), latest = f.refresh();
  assert.equal(f.requests[1].url, f.requests[2].url);
  f.requests[2].resolve(f.response(77)); await latest;
  const status = f.get('status').textContent;
  f.requests[1].reject(new Error('stale refresh error')); await old;
  assert.equal(f.get('checks').textContent, 77);
  assert.equal(f.get('status').textContent, status);
  assert.equal(f.get('status').attributes.role, 'status');
});

test('current authentication and refresh errors are still reported', async () => {
  const f = await requestFixture();
  const login = f.login(); f.requests[0].resolve({ ok: false, status: 401 }); await login;
  assert.deepEqual(f.alerts, ['Invalid dashboard read token.']);
  assert.equal(vm.runInContext('readToken', f.context), '');
  assert.equal(f.get('login').hidden, false);
  assert.equal(f.get('dashboard').hidden, true);
  const retry = f.login(); f.requests[1].resolve(f.response(1)); await retry;
  const refresh = f.refresh(); f.requests[2].reject(new Error('current network error')); await refresh;
  assert.equal(f.get('status').textContent, 'current network error');
  assert.equal(f.get('status').attributes.role, 'alert');
});

test('cards, legends and chart identities use the configured course order and colors', async () => {
  const f = await requestFixture();
  const chartCourses = vm.runInContext('JSON.stringify(chartCourses)', f.context);
  assert.deepEqual(JSON.parse(chartCourses), COURSES.map(({ crn, color }) => ({ crn, color })));
  const cards = [...f.html.matchAll(/id="label([0-9]+)"/g)].map(match => match[1]);
  const legends = [...f.html.matchAll(/class="dot" style="background:(#[a-f0-9]+)"><\/span><span id="legend([0-9]+)"/g)].map(match => ({ color: match[1], crn: match[2] }));
  assert.deepEqual(cards, COURSES.map(course => course.crn));
  assert.deepEqual(legends, COURSES.map(({ crn, color }) => ({ crn, color })));
  assert.deepEqual(COURSES.map(course => course.color), ['#6bc7ff', '#fbbf74', '#6dd5ef', '#c4a0ff', '#ff9bb5', '#7addaa']);
});

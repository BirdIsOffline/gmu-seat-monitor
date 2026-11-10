// Read-only, token-gated dashboard. Deploy as a separate Worker; never reuse the ingestion token.
const IDS = ['17582', '17593'];
const RANGES = Object.freeze({ '24h': 24 * 60 * 60 * 1000, '7d': 7 * 24 * 60 * 60 * 1000 });
const PAGE = String.raw`<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>GMU Seat Monitor · History</title>
<style>
:root{color-scheme:dark;font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#090d18;color:#f5f7ff}
*{box-sizing:border-box}body{margin:0;min-height:100vh}main{max-width:1100px;margin:0 auto;padding:32px 22px 60px}
h1{font-size:clamp(1.6rem,3vw,2.3rem);margin:6px 0}p,.muted{color:#a3adbf}h2{font-size:1.1rem;margin:0 0 14px}
header{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:28px}
.eyebrow{color:#80c7ff;font-size:.76rem;letter-spacing:.17em;font-weight:800;text-transform:uppercase}
.panel{border:1px solid #28364e;border-radius:15px;background:#111827;padding:20px;margin-bottom:18px}
.controls{display:flex;align-items:center;gap:10px;flex-wrap:wrap}button,input{font:inherit;border-radius:8px}input{background:#0c1323;border:1px solid #43516b;padding:12px;color:#fff;min-width:240px;max-width:100%}
button{background:#255fdb;color:white;border:0;padding:11px 16px;cursor:pointer}button:hover{background:#3579f7}button[aria-pressed="true"]{background:#fff;color:#0b1426}button:disabled{opacity:.5;cursor:wait}
#rangeButtons{display:flex;gap:8px}#status{min-height:22px;font-size:.88rem;color:#bdd4ff}#status[role="alert"]{color:#ffb4b4}
.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.cadence-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin-bottom:12px}.metric{border:1px solid #28364e;border-radius:12px;padding:16px;background:#111827}
.metric .label{color:#a3adbf;font-size:.81rem}.metric strong{font-size:1.6rem;display:block;margin-top:7px;font-variant-numeric:tabular-nums}
.key{display:flex;gap:16px;flex-wrap:wrap;font-size:.82rem;color:#aeb9ce}.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;background:#6bc7ff}
.dot.second{background:#fbbf74}.chart{position:relative;overflow:hidden}canvas{width:100%;height:300px;display:block}
table{width:100%;border-collapse:collapse;text-align:left;font-size:.88rem}th,td{padding:11px 7px;border-bottom:1px solid #26334a}th{color:#aeb9ce;font-weight:600}
.note{color:#c6d1df;font-size:.85rem;line-height:1.5}.scroll{overflow-x:auto}
@media(max-width:700px){.metrics,.cadence-grid{grid-template-columns:repeat(2,minmax(0,1fr))}main{padding:18px 14px 45px}canvas{height:240px}table{font-size:.75rem}}
</style>
</head>
<body><main>
<header><div><div class="eyebrow">George Mason University · Spring 2027</div><h1>Seat availability history</h1><p>Read-only historical samples · Not an official registration service</p></div>
<div id="rangeButtons" hidden><button type="button" data-range="24h" aria-pressed="true">24 hours</button><button type="button" data-range="7d" aria-pressed="false">7 days</button></div></header>
<section id="login" class="panel"><h2>View private dashboard</h2><p>Enter the separate dashboard read token. It is held only in this tab's memory, not saved or sent anywhere except this dashboard endpoint.</p>
<form id="loginForm" class="controls"><label for="token" class="muted">Read token</label><input type="password" id="token" autocomplete="off" required minlength="43"><button type="submit">View history</button></form></section>
<div id="dashboard" hidden>
<p id="status" aria-live="polite"></p>
<section class="metrics" aria-label="Dashboard summary">
<div class="metric"><div class="label">Stored checks (selected window)</div><strong id="checks">—</strong></div>
<div class="metric"><div class="label">Recorded check success</div><strong id="rate">—</strong></div>
<div class="metric"><div class="label">IT 304 · Total seats</div><strong id="it304">—</strong></div>
<div class="metric"><div class="label">IT 341 · Total seats</div><strong id="it341">—</strong></div></section>
<section class="panel" style="margin-top:18px" aria-label="Recorded check cadence">
<h2>Recorded check cadence</h2>
<div class="cadence-grid">
<div class="metric"><div class="label">Measured intervals</div><strong id="gapIntervals">—</strong></div>
<div class="metric"><div class="label">Average gap</div><strong id="gapAverage">—</strong></div>
<div class="metric"><div class="label">Longest gap</div><strong id="gapLongest">—</strong></div>
<div class="metric"><div class="label">Gaps over 10 min</div><strong id="gap10">—</strong></div>
<div class="metric"><div class="label">Gaps over 15 min</div><strong id="gap15">—</strong></div>
</div>
<p class="note">Calculated only between consecutive checks stored in the selected window. Does not measure missed triggers before the first record or after the last record, and does not prove every scheduled run occurred. Durations are in minutes.</p></section>
<section class="panel"><h2>Total seats reported over time</h2><div class="key"><span><span class="dot"></span>IT 304 DL1 · 17582</span><span><span class="dot second"></span>IT 341 DL1 · 17593</span></div>
<div class="chart"><canvas id="chart" role="img" aria-label="Line chart of reported total available seats for both courses"></canvas></div>
<p class="note">These are periodic snapshots, not exact seat-opening times. A line does not guarantee seats remained available between checks. Gaps over 15 minutes break the line. An unknown unreserved count must never be interpreted as seats available for general registration.</p></section>
<section class="panel"><h2>Latest course snapshots</h2><div class="scroll"><table><thead><tr><th>Course</th><th>Sample time (local)</th><th>Total</th><th>Unreserved</th><th>Classification</th></tr></thead><tbody id="latestRows"><tr><td colspan="5">No data loaded.</td></tr></tbody></table></div></section>
<section class="panel"><h2>Observed seat increases</h2><div class="scroll"><table><thead><tr><th>Sample time (local)</th><th>Course</th><th>Previous</th><th>New potential</th><th>Type</th></tr></thead><tbody id="eventsRows"><tr><td colspan="5">No data loaded.</td></tr></tbody></table></div><p class="note">Events describe sampled increases in potential availability; they do not cause Bark alerts or prove eligibility.</p></section>
<p class="note" id="coverage"></p></div>
</main>
<script>
'use strict';
let readToken = '';
let activeRange = '24h';
let samples = [];
function byId(id){return document.getElementById(id)}
function pretty(v){return v === null || v === undefined ? 'Unknown' : String(v)}
function fmtTime(value){if(!value)return '—';const d=new Date(value);return Number.isFinite(d.getTime())?d.toLocaleString():value}
function row(cells){const tr=document.createElement('tr');for(const s of cells){const td=document.createElement('td');td.textContent=s;tr.appendChild(td)}return tr}
function setStatus(s,error){const el=byId('status');el.textContent=s;el.setAttribute('role',error?'alert':'status')}
async function load(){
 setStatus('Loading recorded samples...',false);
 const resp=await fetch('/api/dashboard?range='+encodeURIComponent(activeRange),{headers:{Authorization:'Bearer '+readToken},cache:'no-store'});
 if(!resp.ok){throw new Error(resp.status===401?'Invalid dashboard read token.':'Dashboard data is unavailable ('+resp.status+').')}
 const data=await resp.json();samples=data.samples;
 byId('checks').textContent=data.health.total;
 byId('rate').textContent=data.health.total?((100*data.health.success/data.health.total).toFixed(1)+'%'):'—';
 const gaps=data.cadence;
 byId('gapIntervals').textContent=gaps.measured_intervals;
 byId('gapAverage').textContent=gaps.average_minutes===null?'—':gaps.average_minutes.toFixed(2);
 byId('gapLongest').textContent=gaps.longest_gap_minutes===null?'—':gaps.longest_gap_minutes.toFixed(2);
 byId('gap10').textContent=gaps.gaps_over_10_minutes;
 byId('gap15').textContent=gaps.gaps_over_15_minutes;
 const latest={};for(const s of samples)latest[s.crn]=s;
 for(const crn of ['17582','17593']){const s=latest[crn];byId(crn==='17582'?'it304':'it341').textContent=s?pretty(s.total_available):'—'}
 const tbody=byId('latestRows');tbody.replaceChildren();
 for(const [crn,label] of [['17582','IT 304 DL1'],['17593','IT 341 DL1']]){
 const s=latest[crn];tbody.appendChild(s?row([label,fmtTime(s.observed_at_utc),pretty(s.total_available),pretty(s.unreserved_available),s.classification]):row([label,'No samples','—','—','—']))}
 const eventsBody=byId('eventsRows');eventsBody.replaceChildren();
 if(!data.events.length) eventsBody.appendChild(row(['No qualifying seat increases recorded in this window.','','','','']));
 for(const e of data.events){eventsBody.appendChild(row([fmtTime(e.detected_at_utc),e.crn,e.previous_available,e.new_available,e.classification]))}
 byId('coverage').textContent='Coverage: '+data.health.total+' stored checks ('+data.health.failed+' failed), '+samples.length+' returned course observations. Last recorded check: '+fmtTime(data.health.latest)+'. Missing scheduled runs are not included in this success rate.';
 drawChart();setStatus('Historical samples loaded. Last refreshed '+new Date().toLocaleTimeString()+'.',false);
}
function drawChart(){
 const canvas=byId('chart');const dpr=Math.min(window.devicePixelRatio||1,2);const width=canvas.clientWidth||700;const height=canvas.clientHeight||300;
 canvas.width=Math.floor(width*dpr);canvas.height=Math.floor(height*dpr);
 const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);ctx.clearRect(0,0,width,height);
 const left=43,right=12,top=18,bottom=34,plotW=width-left-right,plotH=height-top-bottom;
 const now=Date.now(),start=now-(activeRange==='24h'?86400000:604800000);
 const values=samples.filter(s=>s.total_available!==null).map(s=>s.total_available);
 const maxVal=Math.max(1,...values);const ymax=Math.max(3,Math.ceil(maxVal));
 ctx.font='12px system-ui';ctx.lineWidth=1;ctx.strokeStyle='#30405b';ctx.fillStyle='#b5bfce';
 for(let i=0;i<=3;i++){const y=top+plotH*i/3;ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(width-right,y);ctx.stroke();ctx.fillText(String(Math.round(ymax*(3-i)/3)),5,y+4)}
 for(let i=0;i<=4;i++){const x=left+plotW*i/4;const stamp=new Date(start+(now-start)*i/4);ctx.fillText(activeRange==='24h'?stamp.toLocaleTimeString([],{hour:'numeric',minute:'2-digit'}):stamp.toLocaleDateString([],{month:'short',day:'numeric'}),Math.max(2,x-27),height-9)}
 for(const [crn,color] of [['17582','#6bc7ff'],['17593','#fbbf74']]){
 const points=samples.filter(s=>s.crn===crn).sort((a,b)=>a.observed_at_utc.localeCompare(b.observed_at_utc));
 ctx.lineWidth=2.5;ctx.strokeStyle=color;let previous=null;
 for(const p of points){const ms=new Date(p.observed_at_utc).getTime();if(!Number.isFinite(ms)||ms<start||ms>now||p.total_available===null){previous=null;continue}
 const x=left+(ms-start)/(now-start)*plotW;const y=top+plotH*(1-p.total_available/ymax);
 if(previous!==null && ms-previous.ms<=900000){ctx.beginPath();ctx.moveTo(previous.x,previous.y);ctx.lineTo(x,y);ctx.stroke()}
 previous={x,y,ms};
 }
 }
 if(!samples.length){ctx.fillStyle='#b5bfce';ctx.fillText('No observations in this time window.',left+20,top+40)}
}
byId('loginForm').addEventListener('submit',async e=>{
 e.preventDefault();readToken=byId('token').value;byId('token').value='';
 byId('login').hidden=true;byId('dashboard').hidden=false;byId('rangeButtons').hidden=false;
 try{await load()}
 catch(err){readToken='';byId('login').hidden=false;byId('dashboard').hidden=true;byId('rangeButtons').hidden=true;alert(err.message)}
});
for(const btn of document.querySelectorAll('button[data-range]'))btn.addEventListener('click',async()=>{
 activeRange=btn.dataset.range;for(const b of document.querySelectorAll('button[data-range]'))b.setAttribute('aria-pressed',String(b===btn));
 try{await load()}catch(err){setStatus(err.message,true)}
});
window.addEventListener('resize',()=>{if(!byId('dashboard').hidden)drawChart()});
</script>
</body></html>`;

function reply(code, data) {
  return Response.json(data, { status: code, headers: {
    'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
  } });
}

async function authorized(request, configuredToken) {
  if (typeof configuredToken !== 'string' || !/^[A-Za-z0-9_-]{43,128}$/.test(configuredToken)) return null;
  const supplied = request.headers.get('Authorization') || '';
  if (!/^Bearer [A-Za-z0-9_-]{43,128}$/.test(supplied)) return false;
  const digest = async s => new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s)));
  const [a, b] = await Promise.all([digest(supplied.slice(7)), digest(configuredToken)]);
  let diff = 0;for(let i=0;i<b.length;i++)diff|=a[i]^b[i];
  return diff === 0;
}

function utcCutoff(ms) {
  return new Date(Date.now() - ms).toISOString().replace('Z', '000+00:00');
}

async function dashboard(db, range) {
  const since = utcCutoff(RANGES[range]);
  // Fixed SQL and bound parameters only. No D1 write statements and no arbitrary SQL endpoint.
  const [healthQuery, sampleQuery, eventQuery, cadenceQuery] = await Promise.all([
    db.prepare(`SELECT COUNT(*) AS total,
      COALESCE(SUM(CASE WHEN status='success' THEN 1 ELSE 0 END),0) AS success,
      COALESCE(SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END),0) AS failed,
      MAX(executed_at_utc) AS latest FROM checks WHERE executed_at_utc >= ?`).bind(since).all(),
    db.prepare(`SELECT crn, observed_at_utc, total_available, unreserved_available, classification
      FROM observations WHERE crn IN (?, ?) AND observed_at_utc >= ?
      ORDER BY observed_at_utc ASC, crn ASC LIMIT 5000`).bind(...IDS, since).all(),
    db.prepare(`SELECT o.crn, e.detected_at_utc, e.previous_available, e.new_available, e.classification
      FROM opening_events e JOIN observations o ON e.observation_id=o.observation_id
      WHERE e.detected_at_utc >= ? AND o.crn IN (?, ?)
      ORDER BY e.detected_at_utc DESC LIMIT 50`).bind(since, ...IDS).all(),
    db.prepare(`WITH ordered_checks AS (
        SELECT executed_at_utc,
          LAG(executed_at_utc) OVER (ORDER BY executed_at_utc) AS previous_check
        FROM checks WHERE executed_at_utc >= ?
      ), intervals AS (
        SELECT (julianday(executed_at_utc) - julianday(previous_check)) * 1440.0 AS minutes
        FROM ordered_checks WHERE previous_check IS NOT NULL
      )
      SELECT COUNT(*) AS measured_intervals,
        ROUND(AVG(minutes), 2) AS average_minutes,
        ROUND(MAX(minutes), 2) AS longest_gap_minutes,
        COALESCE(SUM(CASE WHEN minutes > 10 THEN 1 ELSE 0 END), 0) AS gaps_over_10_minutes,
        COALESCE(SUM(CASE WHEN minutes > 15 THEN 1 ELSE 0 END), 0) AS gaps_over_15_minutes
      FROM intervals`).bind(since).all(),
  ]);
  return { range, health: healthQuery.results[0] ?? { total: 0, success: 0, failed: 0, latest: null },
    samples: sampleQuery.results, events: eventQuery.results,
    cadence: cadenceQuery.results[0] ?? { measured_intervals: 0, average_minutes: null,
      longest_gap_minutes: null, gaps_over_10_minutes: 0, gaps_over_15_minutes: 0 } };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method !== 'GET' || url.searchParams.size && url.pathname !== '/api/dashboard') return reply(404, { code: 'NOT_FOUND' });
    if (url.pathname === '/' && url.search === '') {
      return new Response(PAGE, { headers: { 'Content-Type': 'text/html; charset=utf-8',
        'Cache-Control': 'no-store', 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer',
        'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'" } });
    }
    if (url.pathname !== '/api/dashboard' || url.searchParams.size !== 1 || !['24h','7d'].includes(url.searchParams.get('range'))) return reply(404, { code: 'NOT_FOUND' });
    const auth = await authorized(request, env.DASHBOARD_READ_TOKEN);
    if (auth === null || !env.DB) return reply(503, { code: 'UNAVAILABLE' });
    if (!auth) return reply(401, { code: 'UNAUTHORIZED' });
    try { return reply(200, await dashboard(env.DB, url.searchParams.get('range'))); }
    catch { return reply(503, { code: 'UNAVAILABLE' }); }
  },
};

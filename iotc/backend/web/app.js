// iotc dashboard (Architecture §11.9). Generated App asset; the program data comes from
// config.json, live data from the WebUI socket. All text is set with textContent, never parsed as HTML.
'use strict';

const $ = (id) => document.getElementById(id);
const COLORS = ['#2563eb', '#16a34a', '#d97706', '#9333ea'];
const HISTORY_MS = 60000;
let CFG = null;
let ui = null;
let lastRx = 0;
let lastState = null;
const hist = [];      // [t, code0, code1, ...]
const actHist = [];   // [t, cur0, cur1, ...]
const sliderBusy = new Set();

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = String(text);
  return e;
}

function valueOf(s, code) {
  return s.kind === 'DIGITAL' ? code : s.lo + code * (s.hi - s.lo) / s.adc_max;
}

function fmtValue(s, code) {
  if (s.kind === 'DIGITAL') return String(code);
  const v = valueOf(s, code);
  const digits = Math.abs(s.hi - s.lo) >= 100 ? 1 : 2;
  return v.toFixed(digits) + (s.unit ? ' ' + s.unit : '');
}

function lampColor(pin) {
  if (/^LED\d+R$/.test(pin)) return '#ef4444';
  if (/^LED\d+G$/.test(pin)) return '#22c55e';
  if (/^LED\d+B$/.test(pin)) return '#3b82f6';
  return '#f59e0b';
}

function secs(t) { return (t / 1000).toFixed(1) + ' s'; }

// ---------------------------------------------------------------- build the static page
function build() {
  $('title').textContent = CFG.program + '  ·  board ' + CFG.board + ', target ' + CFG.target +
    ', input ' + CFG.input;
  document.title = 'iotc · ' + CFG.program;
  const list = $('sensor-list');
  CFG.sensors.forEach((s, i) => {
    const box = el('div', 'sensor');
    const head = el('div', 'sensor-head');
    head.appendChild(el('span', 'sensor-name', s.name));
    const val = el('span', 'sensor-value', '–');
    val.id = 'sv' + i;
    head.appendChild(val);
    box.appendChild(head);
    const code = el('div', 'sensor-code', '');
    code.id = 'sc' + i;
    box.appendChild(code);
    if (CFG.input === 'sim') {
      const r = el('input');
      r.type = 'range'; r.min = 0; r.max = s.adc_max; r.step = 1; r.id = 'sl' + i;
      r.setAttribute('aria-label', s.name);
      r.value = CFG.sim_init[i];
      let pending = null;
      r.addEventListener('pointerdown', () => sliderBusy.add(i));
      r.addEventListener('pointerup', () => sliderBusy.delete(i));
      r.addEventListener('input', () => {
        $('sv' + i).textContent = fmtValue(s, Number(r.value));
        if (pending) return;
        pending = setTimeout(() => {
          pending = null;
          if (ui) ui.send_message('sim_set', { sensor: i, code: Number(r.value) });
        }, 60);
      });
      box.appendChild(r);
    }
    const g = el('canvas', 'gauge');
    g.id = 'g' + i; g.width = 600; g.height = 68;
    box.appendChild(g);
    list.appendChild(box);
  });

  const acts = $('act-list');
  CFG.actuators.forEach((a, i) => {
    const box = el('div', 'act');
    box.id = 'act' + i;
    const lamp = el('span', 'lamp');
    lamp.id = 'lamp' + i;
    lamp.style.setProperty('--lamp', lampColor(a.pin));
    box.appendChild(lamp);
    const txt = el('div');
    txt.appendChild(el('div', 'sensor-name', a.name + ' (' + a.pin + ')'));
    const why = el('div', 'why', 'OFF');
    why.id = 'why' + i;
    txt.appendChild(why);
    box.appendChild(txt);
    acts.appendChild(box);
  });

  const tb = $('ptable').querySelector('tbody');
  CFG.placement.forEach((p) => {
    const tr = el('tr');
    tr.dataset.rule = p.rule;
    if (p.runs_on === 'Linux') tr.classList.add('linuxrow');
    tr.appendChild(el('td', '', p.rule));
    tr.appendChild(el('td', 'where ' + (p.runs_on === 'MCU' ? 'mcu' : 'linux'), p.runs_on));
    tr.appendChild(el('td', '', p.reason));
    tb.appendChild(tr);
  });

  const sl = $('slist');
  CFG.safety.forEach((s) => {
    const li = el('li');
    li.appendChild(el('span', 'mark ' + s.status, s.status === 'proven' ? '✓' : '⚠'));
    li.appendChild(el('span', '', s.property));
    s.detail.forEach((d) => li.appendChild(el('span', 'detail', d)));
    sl.appendChild(li);
  });

  $('report').textContent = CFG.report;
  const legend = $('legend');
  CFG.sensors.slice(0, COLORS.length).forEach((s, i) => {
    const sp = el('span', '', s.name + (s.unit ? ' (' + s.unit + ')' : ''));
    sp.style.setProperty('--c', COLORS[i]);
    legend.appendChild(sp);
  });
  CFG.actuators.forEach((a) => {
    const sp = el('span', '', a.name + ' ON');
    sp.style.setProperty('--c', lampColor(a.pin));
    legend.appendChild(sp);
  });
  if (CFG.demo_controls) $('controls').hidden = false;
}

// ---------------------------------------------------------------- live updates
function apply(st) {
  if (!st) return;
  lastRx = Date.now();
  lastState = st;
  if (st.codes && st.codes.length && (!hist.length || st.t > hist[hist.length - 1][0])) {
    hist.push([st.t].concat(st.codes));
    actHist.push([st.t].concat(st.cur));
    while (hist.length && hist[hist.length - 1][0] - hist[0][0] > HISTORY_MS) hist.shift();
    while (actHist.length && actHist[actHist.length - 1][0] - actHist[0][0] > HISTORY_MS) actHist.shift();
  }
  CFG.sensors.forEach((s, i) => {
    const c = st.codes[i];
    if (c === undefined) return;
    $('sv' + i).textContent = fmtValue(s, c);
    $('sc' + i).textContent = 'ADC code ' + c + ' / ' + s.adc_max;
    const sl = $('sl' + i);
    if (sl && !sliderBusy.has(i) && document.activeElement !== sl && st.sim) sl.value = st.sim[i];
    drawGauge(i, c);
  });
  CFG.actuators.forEach((a, i) => {
    const on = !!st.cur[i];
    $('lamp' + i).classList.toggle('on', on);
  });
  const link = $('link');
  if (st.paused) {
    link.className = 'pill paused';
    link.textContent = 'Linux rule engine PAUSED (' + st.paused_left + ' s)';
  } else if (st.link_up) {
    link.className = 'pill up';
    link.textContent = 'Linux link UP';
  } else {
    link.className = 'pill down';
    link.textContent = 'FAILSAFE: Linux link DOWN';
  }
  $('hb').textContent = 'heartbeat #' + st.seq + (st.beat_age_ms === null ? '' : ' · ' + st.beat_age_ms + ' ms ago') +
    ' · board time ' + secs(st.t);
  CFG.placement.forEach((p, i) => {
    const tr = $('ptable').querySelector('tbody').children[i];
    if (i < st.firing.length) tr.classList.toggle('firing', !!st.firing[i]);
  });
  const dl = $('sdl');
  dl.textContent = '';
  const rows = [
    ['max pass', st.stats.max_pass_us + ' µs (deadline ' + (CFG.deadline_ms ? CFG.deadline_ms + ' ms' : 'none') + ')'],
    ['REALTIME overruns', st.stats.overruns],
    ['input faults', st.stats.faults],
    ['Bridge round trip', st.rtt.n ? st.rtt.median + ' ms median, ' + st.rtt.max + ' ms max (' + st.rtt.n + ')' : 'measuring…'],
    ['Linux mask', '0x' + st.mask.toString(16)],
  ];
  rows.forEach(([k, v]) => { dl.appendChild(el('dt', '', k)); dl.appendChild(el('dd', '', v)); });
  drawMatrix(st.matrix);
  drawChart();
}

function partnerOf(detail, act) {
  const m = /^never#(\d+)$/.exec(detail);
  if (!m) return null;
  const n = CFG.nevers[Number(m[1]) - 1];
  if (!n) return null;
  return n.a === act ? n.b + ' is ' + n.s2 : n.a + ' is ' + n.s1;
}

function sentence(e) {
  if (e.kind === 'EV') {
    const what = e.act + ' ' + e.state;
    if (e.result === 'refused') {
      const p = partnerOf(e.detail, e.act);
      return [what + ' refused by ' + e.detail.toUpperCase() + (p ? ' (' + p + ')' : '') + ' · rule ' + e.src, 'refused'];
    }
    if (e.result === 'forced') return [what + ' forced by ' + e.src, 'forced'];
    if (e.result === 'failsafe') return [what + ' · FAILSAFE (Linux link down)', 'failsafe'];
    const rule = CFG.placement.find((p) => p.rule === e.src);
    return [what + ' · rule ' + e.src + (rule ? ' (' + rule.runs_on + ')' : ''), 'applied'];
  }
  if (e.kind === 'NT') return ['NOTIFY ' + e.rule + ': ' + e.text, 'nt'];
  if (e.kind === 'LK') return [e.up ? 'Linux link UP' : 'Linux link DOWN: FAILSAFE states applied', e.up ? 'up' : 'down'];
  return [e.text || e.line, ''];
}

function addLog(e, quiet) {
  const [text, cls] = sentence(e);
  const li = el('li');
  li.appendChild(el('span', 't', secs(e.t)));
  li.appendChild(el('span', cls, text));
  const ol = $('loglist');
  ol.insertBefore(li, ol.firstChild);
  while (ol.children.length > 200) ol.removeChild(ol.lastChild);
  if (quiet) return;
  if (e.kind === 'EV') {
    const i = CFG.actuators.findIndex((a) => a.name === e.act);
    if (i >= 0) {
      $('why' + i).textContent = e.state + ' · ' + e.result + ' (' + e.src + ')';
      if (e.result === 'refused') {
        const box = $('act' + i);
        box.classList.remove('flash'); void box.offsetWidth; box.classList.add('flash');
      }
    }
  }
  if (e.kind === 'NT') toast(e.text);
}

function toast(text) {
  const t = $('toast');
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, 4000);
}

// ---------------------------------------------------------------- drawing
function drawGauge(i, code) {
  const s = CFG.sensors[i];
  const c = $('g' + i);
  const ctx = c.getContext('2d');
  const W = c.width, H = c.height;
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = '#e5e7eb';
  ctx.fillRect(0, 22, W, 20);
  ctx.fillStyle = COLORS[i % COLORS.length];
  ctx.fillRect(0, 22, W * code / s.adc_max, 20);
  ctx.font = '16px system-ui, sans-serif';
  s.ticks.forEach((tk) => {
    const x = W * tk.code / s.adc_max;
    ctx.fillStyle = '#111827';
    ctx.fillRect(x - 1, 14, 3, 36);
  });
  ctx.fillStyle = '#5e6b78';
  ctx.fillText(fmtValue(s, 0), 2, 66);
  const hiTxt = fmtValue(s, s.adc_max);
  ctx.fillText(hiTxt, W - ctx.measureText(hiTxt).width - 2, 66);
}

function drawMatrix(px) {
  if (!px) return;
  const c = $('matrix');
  const ctx = c.getContext('2d');
  ctx.fillStyle = '#0b1220';
  ctx.fillRect(0, 0, c.width, c.height);
  const step = 22;
  for (let r = 0; r < 8; r++) {
    for (let col = 0; col < 13; col++) {
      const v = px[r * 13 + col];
      ctx.beginPath();
      ctx.arc(col * step + 11, r * step + 11, 8, 0, Math.PI * 2);
      ctx.fillStyle = v ? 'rgba(96,165,250,' + (0.25 + 0.75 * v / 7) + ')' : '#1e293b';
      ctx.fill();
    }
  }
}

function drawChart() {
  const c = $('chart');
  const dpr = window.devicePixelRatio || 1;
  const W = c.clientWidth, H = 230;
  if (c.width !== Math.round(W * dpr)) { c.width = Math.round(W * dpr); c.height = Math.round(H * dpr); }
  const ctx = c.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  if (!hist.length) return;
  const tEnd = hist[hist.length - 1][0];
  const tStart = tEnd - HISTORY_MS;
  const bandH = 8, plotH = H - 18 - bandH * CFG.actuators.length;
  const X = (t) => (t - tStart) / HISTORY_MS * W;
  CFG.sensors.slice(0, COLORS.length).forEach((s, i) => {
    const Y = (code) => 6 + (1 - code / s.adc_max) * (plotH - 12);
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = COLORS[i] + '88';
    ctx.lineWidth = 1;
    s.ticks.forEach((tk) => { ctx.beginPath(); ctx.moveTo(0, Y(tk.code)); ctx.lineTo(W, Y(tk.code)); ctx.stroke(); });
    ctx.setLineDash([]);
    ctx.strokeStyle = COLORS[i];
    ctx.lineWidth = 2;
    ctx.beginPath();
    hist.forEach((h, k) => { const x = X(h[0]), y = Y(h[1 + i]); if (k) ctx.lineTo(x, y); else ctx.moveTo(x, y); });
    ctx.stroke();
  });
  CFG.actuators.forEach((a, j) => {
    const y = plotH + 8 + j * bandH;
    ctx.fillStyle = '#eef2f7';
    ctx.fillRect(0, y, W, bandH - 2);
    ctx.fillStyle = lampColor(a.pin);
    for (let k = 0; k < actHist.length; k++) {
      if (!actHist[k][1 + j]) continue;
      const x0 = X(actHist[k][0]);
      const x1 = k + 1 < actHist.length ? X(actHist[k + 1][0]) : W;
      ctx.fillRect(x0, y, Math.max(1, x1 - x0), bandH - 2);
    }
  });
}

// ---------------------------------------------------------------- wiring
function connect() {
  ui = new WebUI();
  ui.on_message('snapshot', (d) => {
    hist.length = 0; actHist.length = 0;
    (d.history || []).forEach((h) => hist.push(h));
    $('loglist').textContent = '';
    (d.log || []).forEach((e) => addLog(e, true));
    apply(d.state);
  });
  ui.on_message('state', apply);
  ui.on_message('event', (e) => addLog(e, false));
  $('pause').addEventListener('click', () => ui.send_message('pause', { seconds: 10 }));
  $('resume').addEventListener('click', () => ui.send_message('resume', {}));
}

function watchdog() {
  $('banner').hidden = !(lastRx && Date.now() - lastRx > 1500);
}

async function main() {
  const r = await fetch('config.json', { cache: 'no-store' });
  CFG = await r.json();
  build();
  connect();
  setInterval(watchdog, 500);
  window.addEventListener('resize', drawChart);
}

main().catch((err) => { $('title').textContent = 'dashboard error: ' + err; });

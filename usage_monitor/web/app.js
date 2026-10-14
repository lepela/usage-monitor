// 1920×480 키오스크. 서버가 SSE로 스냅샷을 밀어 주고, 화면은 그것만 그린다(브라우저 폴링 없음).
'use strict';

const $ = id => document.getElementById(id);
const SERIES = {
  cache: [['cache_read', '--s-cache-read'], ['cache_creation', '--s-cache-creation']],
  io: [['input', '--s-input'], ['output', '--s-output']],
};
const HEALTH_NAMES = { otel: 'OTel', statusline: 'statusline', get_usage: 'get_usage' };
const HEALTH_TEXT = { live: '실시간', recent: '30분 내', idle: '유휴', missing: '수집 끊김 의심' };
const HEARTBEAT_TIMEOUT_MS = 25000;
const SINCE_OBS_SHOW_MICROS = 100000;      // 관측 뒤 추정 비용이 $0.10 이상일 때만 표시한다
const SHIFT_EVERY_MS = 5 * 60 * 1000;
const SHIFTS = [[0, 0], [1, 0], [2, 1], [1, 2], [0, 1], [2, 2], [1, 1], [0, 2], [2, 0]];

let snap = null;
let lastMessageAt = 0;
let lastHeartbeatAt = Date.now();

// ── 형식 ───────────────────────────────────────────
const pad = n => String(n).padStart(2, '0');
function clock(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000), now = new Date();
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return d.toDateString() === now.toDateString() ? hm : `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${hm}`;
}
function span(sec) {
  sec = Math.max(0, Math.round(sec));
  const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60);
  if (d) return `${d}일 ${h}시간`;
  if (h) return `${h}시간 ${m}분`;
  return `${m}분`;
}
function compact(n) {
  if (n == null) return '—';
  const trim = (v, d) => v.toFixed(d).replace(/\.0$/, '');     // 2.0M → 2M
  if (n >= 1e6) return trim(n / 1e6, n >= 1e7 ? 0 : 1) + 'M';
  if (n >= 1e3) return trim(n / 1e3, n >= 1e4 ? 0 : 1) + 'K';
  return String(n);
}
const usd = micros => '$' + (micros / 1e6).toFixed(2);
// claude-opus-5-5 → Opus 5.5, claude-haiku-4-5-20251001 → Haiku 4.5. 표시 이름(Opus 5)은 그대로 둔다
function modelName(m) {
  if (!m || !m.startsWith('claude-')) return m;
  const [, base, suffix = ''] = m.match(/^claude-(.+?)(\[.*\])?$/);
  const parts = base.replace(/-\d{8}$/, '').split('-');
  const family = parts.shift();
  return `${family[0].toUpperCase()}${family.slice(1)} ${parts.join('.')}${suffix ? ' ' + suffix : ''}`.trim();
}
const nowSec = () => Date.now() / 1000;

// ── 좌: 한도 ───────────────────────────────────────
function severity(pct) {
  return pct >= 90 ? 'critical' : pct >= 70 ? 'warning' : 'good';
}

function etaText(l) {
  switch (l.verdict) {
    case 'no_usage': return ['사용 없음', false];
    case 'safe': return ['리셋 전 소진 안 함', false];
    case 'exhaust_before_reset': return [`${clock(l.eta_ts)} 소진 예상`, true];
    case 'exhausted': return ['한도 도달', true];
    case 'reset': return ['리셋됨 · 새 관측 대기', false];
    default: return ['속도 산출 불가', false];
  }
}

function methodText(l) {
  if (l.verdict === 'reset') return `리셋 전 마지막 값 ${l.pct}%`;
  if (!l.method || l.rate_pct_per_hour == null) return '';
  const name = l.method === 'trend' ? '추세' : '창 평균';
  return `${name} · 시간당 ${l.rate_pct_per_hour.toFixed(1)}%p`;
}

function renderLimit(el, l) {
  const pctEl = el.querySelector('.pct');
  const bar = el.querySelector('.bar > div');
  if (!l) {
    pctEl.textContent = '—';
    el.querySelector('.eta-main').textContent = '';
    el.querySelector('.method').textContent = '';
    el.querySelector('.obs').textContent = '관측 없음';
    el.querySelector('.reset').textContent = '';
    bar.style.width = '0';
    return;
  }
  // 창이 리셋된 뒤에는 이전 값이 현재 값이 아니다. 숫자와 막대를 비우고 이전 값은 문구로만 남긴다
  const stale = l.verdict === 'reset';
  const html = stale ? '—' : `${l.pct}<small>%</small>`;
  if (pctEl.innerHTML !== html) {
    const first = pctEl.textContent === '—' || stale;   // 첫 로딩과 리셋 표시는 값이 바뀐 것이 아니므로 강조하지 않는다
    pctEl.innerHTML = html;
    if (!first) {
      pctEl.classList.add('flash');
      setTimeout(() => pctEl.classList.remove('flash'), 60);
    }
  }
  bar.className = 'sev-' + severity(l.pct);
  bar.style.width = stale ? '0' : Math.min(100, l.pct) + '%';
  const [main, alert] = etaText(l);
  const mainEl = el.querySelector('.eta-main');
  mainEl.innerHTML = (alert ? '<span class="icon-alert"></span>' : '') + main;
  mainEl.classList.toggle('alert', alert);
  el.querySelector('.method').textContent = methodText(l);
  const ago = nowSec() - l.last_obs_ts;
  // 관측 뒤에 쓴 추정 비용. 한도 값이 실제보다 뒤처진 정도를 가늠하게 한다
  const spent = l.since_obs ? l.since_obs.cost_micros_est : 0;
  // 칸이 좁아 둘을 함께 쓰지 못한다. 비용이 있으면 경과 시간 대신 비용을 보인다
  el.querySelector('.obs').textContent = `관측 ${clock(l.last_obs_ts)}` +
    (spent >= SINCE_OBS_SHOW_MICROS ? ` · +${usd(spent)}` : ago >= 600 ? ` (${span(ago)} 전)` : '');
  el.querySelector('.reset').textContent = l.verdict === 'reset' ? '' :
    `리셋 ${clock(l.resets_at)} · ${span(l.resets_at - nowSec())} 남음`;
}

// ── 중앙: 누적 막대 ─────────────────────────────────
function niceMax(v) {
  if (v <= 0) return 1;
  const e = Math.pow(10, Math.floor(Math.log10(v)));
  // 절반 눈금도 소수 한 자리로 떨어지는 단계만 쓴다(2.5의 절반 1.25는 1.3으로 반올림돼 틀리게 보인다)
  return [1, 2, 4, 6, 8, 10].map(m => m * e).find(m => m >= v);
}

// 위쪽 끝만 4px 둥근 막대(기준선 쪽은 각진 끝)
function topRounded(x, y, w, h, r) {
  r = Math.min(r, h, w / 2);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

function renderChart(svg, burn, keys) {
  if (!svg.getClientRects().length) return;      // 숨겨진 차트(위젯의 작은 구성)는 그리지 않는다
  const W = svg.clientWidth || svg.parentNode.clientWidth;
  const H = svg.clientHeight || +svg.getAttribute('height');
  // 치수는 차트 글자 크기(u)에 비례한다. 키오스크는 u=20px, 배경화면은 화면 크기에 따라 달라진다
  const u = parseFloat(getComputedStyle(svg).fontSize) || 20;
  const right = u * 3.2, top = u * 0.3, bottom = 2;
  const plotW = W - right, plotH = H - top - bottom;
  const n = burn.minutes;
  const totals = Array.from({ length: n }, (_, i) => keys.reduce((s, [k]) => s + burn.series[k][i], 0));
  const max = niceMax(Math.max(...totals));
  const slot = plotW / n, bw = Math.min(u * 1.2, slot * 0.7);
  const radius = u * 0.2;
  const css = getComputedStyle(document.documentElement);
  const y = v => top + plotH - (v / max) * plotH;
  let out = '';
  for (const frac of [0.5, 1]) {
    const gy = y(max * frac);
    out += `<line x1="0" x2="${plotW}" y1="${gy}" y2="${gy}" stroke="var(--grid)" stroke-width="1"/>`;
    out += `<text x="${W - u * 0.2}" y="${gy + u * 0.35}" text-anchor="end">${compact(max * frac)}</text>`;
  }
  out += `<line x1="0" x2="${plotW}" y1="${top + plotH}" y2="${top + plotH}" stroke="var(--baseline)" stroke-width="1"/>`;
  for (let i = 0; i < n; i++) {
    if (!totals[i]) continue;
    const x = i * slot + (slot - bw) / 2;
    let base = top + plotH;
    const segs = keys.map(([k, v]) => [burn.series[k][i], css.getPropertyValue(v).trim()]).filter(([v]) => v > 0);
    segs.forEach(([v, color], j) => {
      const h = (v / max) * plotH;
      const isTop = j === segs.length - 1;
      const gap = j > 0 ? 2 : 0;                        // 세그먼트 사이 2px 표면 간격
      const hh = Math.max(0, h - gap);
      const yy = base - h;
      out += isTop
        ? `<path d="${topRounded(x, yy, bw, hh, radius)}" fill="${color}"/>`
        : `<rect x="${x}" y="${yy}" width="${bw}" height="${hh}" fill="${color}"/>`;
      base = yy;
    });
  }
  svg.innerHTML = out;
}

function renderAxis(burn) {
  const t0 = burn.start_ts, n = burn.minutes;
  const marks = [0, 15, 30, 45, n - 1].map(i => `<span>${clock(t0 + i * 60)}</span>`);
  $('axis').innerHTML = marks.join('');
}

function renderBurn(s) {
  renderChart($('chart-cache'), s.burn, SERIES.cache);
  renderChart($('chart-io'), s.burn, SERIES.io);
  renderAxis(s.burn);
  const sum = Object.values(s.burn.series).flat().reduce((a, b) => a + b, 0);
  $('burn-total').textContent = `합계 ${compact(sum)}`;
}

// ── 유휴 화면 ───────────────────────────────────────
function renderIdle(s) {
  const last = s.last_activity_ts;
  $('idle-last').textContent = last ? `마지막 활동 ${clock(last)} · ${span(nowSec() - last)} 전` : '활동 기록 없음';
  for (const [w, id] of [['5h', 'idle-r5'], ['7d', 'idle-r7']]) {
    const l = s.limits[w];
    const live = l && l.verdict !== 'reset';
    $(id).textContent = live ? span(l.resets_at - nowSec()) : '—';
    $(id + '-at').textContent = live ? `${clock(l.resets_at)} · 현재 ${l.pct}%` : '';
  }
  const t = s.today;
  $('idle-today').textContent = `${compact(t.input + t.output + t.cache_read + t.cache_creation)} 토큰`;
  $('idle-today-sub').textContent = `요청 ${t.requests} · 추정 ${usd(t.cost_micros_est)}`;
}

// ── 우: 세션·수집 상태 ───────────────────────────────
function renderSide(s) {
  const cur = s.sessions[0];
  $('sess-label').textContent = cur ? `현재 세션 · ${cur.client === 'claude-code-desktop' ? 'Desktop' : 'CLI'}` : '현재 세션';
  // 세션에 모델 정보가 없으면(OTel을 보내지 않는 세션) 마지막 요청의 모델을 쓴다
  const fallback = s.last_request || {};
  const model = cur?.model || fallback.model;
  const effort = cur?.model ? cur.effort : fallback.effort;
  $('model').textContent = modelName(model) || '—';
  $('chips').innerHTML = effort ? `<span>effort ${effort}</span>` : '';
  $('ctx').textContent = cur?.ctx_pct != null ? `${Math.round(cur.ctx_pct)}%` : '—';
  $('cache').textContent = cur?.cache_hit != null ? `${Math.round(cur.cache_hit * 100)}%` : '—';
  $('cost').textContent = usd(s.today.cost_micros_est);
  $('reqs').textContent = String(s.today.requests);
  const extra = Object.values(s.limits).filter(l => l.window.startsWith('7d:') && l.verdict !== 'reset');
  $('extra-windows').textContent = extra.length
    ? '모델별 주간 ' + extra.map(l => `${l.window.slice(3)} ${l.pct}%`).join(' · ') : '';
  const rows = s.health.length ? s.health : [];
  $('health').innerHTML = rows.map(h =>
    `<div><span class="dot ${h.status}"></span>${HEALTH_NAMES[h.source] || h.source} · ${HEALTH_TEXT[h.status]} · ${clock(h.last_seen)}</div>`
  ).join('');
  const hits = s.limit_hits;
  $('hits').innerHTML = hits.count_5h
    ? `<span class="icon-alert"></span>429 한도 응답 ${hits.count_5h}회 · 마지막 ${clock(Math.floor(hits.last_ts / 1000))}` : '';
}

// ── 전체 ───────────────────────────────────────────
function render() {
  if (!snap) return;
  document.body.classList.toggle('idle', snap.mode === 'idle');
  renderLimit($('lim-5h'), snap.limits['5h']);
  renderLimit($('lim-7d'), snap.limits['7d']);
  if (snap.mode === 'idle') renderIdle(snap); else renderBurn(snap);
  renderSide(snap);
}

function connect() {
  const es = new EventSource('/stream');
  es.addEventListener('snapshot', e => {
    snap = JSON.parse(e.data);
    lastMessageAt = lastHeartbeatAt = Date.now();
    document.body.classList.remove('offline');
    render();
  });
  es.addEventListener('heartbeat', () => { lastHeartbeatAt = Date.now(); });
  es.onerror = () => markOffline();   // EventSource가 스스로 재연결한다
}

function markOffline() {
  document.body.classList.add('offline');
  $('offline-text').textContent = '수집기 연결 끊김 · 마지막 수신 ' +
    (lastMessageAt ? new Date(lastMessageAt).toLocaleTimeString('ko-KR', { hour12: false }) : '없음');
}

// 남은 시간 문구는 15초마다 다시 쓴다(서버 스냅샷은 30초마다 온다)
setInterval(() => {
  if (Date.now() - lastHeartbeatAt > HEARTBEAT_TIMEOUT_MS) markOffline();
  render();
}, 15000);

// 번인 방지: 5분마다 전체를 0~2px 이동
let shiftIdx = 0;
setInterval(() => {
  shiftIdx = (shiftIdx + 1) % SHIFTS.length;
  const [dx, dy] = SHIFTS[shiftIdx];
  $('stage').style.transform = `translate(${dx}px, ${dy}px)`;
}, SHIFT_EVERY_MS);

// 창 크기가 바뀌면 차트를 다시 그린다(배경화면은 모니터마다 크기가 다르다)
addEventListener('resize', render);

connect();

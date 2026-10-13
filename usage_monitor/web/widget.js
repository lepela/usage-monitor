// 위젯 창의 구성 전환, 한 줄로 접기, 투명도, 창 조작.
// 창 프로그램(widget_host.py)이 있으면 pywebview.api로 창 크기와 투명도를 맞추고,
// 브라우저에서 그냥 열었을 때는 페이지 안에서만 흉내 낸다.
'use strict';

const SIZES = { mini: [200, 30], s: [300, 132], m: [300, 192], l: [300, 412] };   // widget.css의 구성별 창 크기
const ORDER = ['s', 'm', 'l'];                      // ◱ 버튼이 도는 순서. mini는 – 버튼으로만 오간다
const OPACITY_LEVELS = [100, 85, 70, 55, 40];       // widget_host.py의 OPACITY_LEVELS와 맞춘다

const hostApi = () => (window.pywebview && window.pywebview.api) || null;
const store = {
  get(key, fallback) { try { return localStorage.getItem(key) || fallback; } catch (e) { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch (e) { /* 저장 못 해도 동작한다 */ } },
};

let lastFull = 'm';        // 접기 전의 구성. 펼 때 여기로 돌아간다
let opacity = 100;

function applySize(size, tellHost = true) {
  if (!SIZES[size]) size = 'm';
  if (size !== 'mini') lastFull = size;
  document.body.dataset.size = size;
  document.getElementById('btn-mini').textContent = size === 'mini' ? '▢' : '–';
  document.getElementById('btn-mini').title = size === 'mini' ? '펴기' : '한 줄로 접기';
  store.set('widget-size', size);
  store.set('widget-last-full', lastFull);
  const api = hostApi();
  if (api && tellHost) api.set_size(size, SIZES[size][0], SIZES[size][1]);
  render();          // app.js — 보이게 된 차트를 그린다
}

function showOpacity(percent) {
  opacity = percent;
  document.getElementById('btn-opacity').title = `투명도 (지금 ${percent}%)`;
  if (!hostApi()) document.body.style.opacity = percent / 100;      // 브라우저 미리보기용
}
window.syncOpacity = showOpacity;                                    // 트레이 메뉴에서 바꿨을 때 창 프로그램이 부른다

function initialSize() {
  const q = new URLSearchParams(location.search).get('size');
  return SIZES[q] ? q : store.get('widget-size', 'm');
}

document.getElementById('btn-size').addEventListener('click', () => {
  const cur = document.body.dataset.size;
  applySize(cur === 'mini' ? lastFull : ORDER[(ORDER.indexOf(cur) + 1) % ORDER.length]);
});
document.getElementById('btn-mini').addEventListener('click', () => {
  applySize(document.body.dataset.size === 'mini' ? lastFull : 'mini');
});
document.getElementById('btn-opacity').addEventListener('click', () => {
  const next = OPACITY_LEVELS[(OPACITY_LEVELS.indexOf(opacity) + 1) % OPACITY_LEVELS.length];
  showOpacity(next);
  const api = hostApi();
  if (api) api.set_opacity(next);
});
document.getElementById('btn-close').addEventListener('click', () => {
  const api = hostApi();
  if (api) api.close(); else window.close();
});

lastFull = store.get('widget-last-full', 'm');
if (!ORDER.includes(lastFull)) lastFull = 'm';
applySize(initialSize(), false);
showOpacity(100);
// pywebview.api는 페이지 로드보다 늦게 붙는다. 붙으면 창 크기를 현재 구성에 맞추고 저장된 투명도를 읽어 온다
window.addEventListener('pywebviewready', () => {
  applySize(document.body.dataset.size);
  hostApi().get_state().then(state => showOpacity(state.opacity));
});

// 위젯 창의 구성(s | m | l) 전환과 창 조작. 창 프로그램(widget/host.py)이 있으면 pywebview.api로 크기를 맞추고,
// 브라우저에서 그냥 열었을 때는 구성만 바꾼다.
'use strict';

const SIZES = { s: [300, 132], m: [300, 192], l: [300, 412] };   // widget.css의 구성별 창 크기
const ORDER = ['s', 'm', 'l'];

const hostApi = () => (window.pywebview && window.pywebview.api) || null;

function applySize(size, tellHost = true) {
  if (!SIZES[size]) size = 'm';
  document.body.dataset.size = size;
  try { localStorage.setItem('widget-size', size); } catch (e) { /* 저장 못 해도 동작한다 */ }
  const api = hostApi();
  if (api && tellHost) api.set_size(size, SIZES[size][0], SIZES[size][1]);
  render();          // app.js — 보이게 된 차트를 그린다
}

function initialSize() {
  const q = new URLSearchParams(location.search).get('size');
  if (SIZES[q]) return q;
  try { return localStorage.getItem('widget-size') || 'm'; } catch (e) { return 'm'; }
}

document.getElementById('btn-size').addEventListener('click', () => {
  const cur = document.body.dataset.size;
  applySize(ORDER[(ORDER.indexOf(cur) + 1) % ORDER.length]);
});
document.getElementById('btn-close').addEventListener('click', () => {
  const api = hostApi();
  if (api) api.close(); else window.close();
});

applySize(initialSize(), false);
// pywebview.api는 페이지 로드보다 늦게 붙는다. 붙으면 창 크기를 현재 구성에 맞춘다
window.addEventListener('pywebviewready', () => applySize(document.body.dataset.size));

/* Sns-board 제휴 수익 (주인 10/23 — "토스 쇼핑도 무슨 물건을 샀는지 … 내 링크 타고 들어와서 구매한 내역도 다 통계 탭에서"): 통계 탭 맨 위 칸.
   state.earnings (서버 earn.py) = 합계(오늘·7일·30일) → 원별 줄(쿠팡 자동 · 쇼핑커넥트 · 에이블리·지그재그·토스쇼핑 자동 · 직접 넣은 것) → 최근 구매 → 수익 직접 넣기.
   글·금액은 서버가 다 만들어 둠 — 여기서는 그리기와 '수익 직접 넣기' 시트(저장 earn.save · 지우기 earn.delete)뿐. 처음 통계 탭을 열 때만 app.js 가 불러온다. */
'use strict';
(() => {
const D = document, W = window;
const CSS = '.ew{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:0 0 12px}' +
  '.ec{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 10px 12px;min-width:0}' +
  '.ec .el{display:block;font-size:12px;color:var(--sub)}.ec b{display:block;font-size:17px;font-variant-numeric:tabular-nums;margin:2px 0;word-break:keep-all}' +
  '.ec .es{display:block;font-size:12px;color:var(--muted)}' +
  '.es2 .rw{display:flex;justify-content:space-between;gap:8px;font-size:14px;padding:2px 0}.es2 .rw span:last-child{font-variant-numeric:tabular-nums;text-align:right}' +
  '.es2 h3{font-size:15px;margin:0 0 2px}.es2 .wt{color:var(--muted)}' +
  '.rc{display:flex;justify-content:space-between;gap:10px;padding:8px 0;border-top:1px solid var(--line);font-size:14px}.rc:first-of-type{border-top:0}' +
  '.rc .nm{min-width:0;flex:1}.rc .nm small{display:block;color:var(--muted);font-size:12px}.rc .am{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}' +
  '.rc .am small{display:block;color:var(--sub);font-size:12px}.rc.cx .nm{text-decoration:line-through;color:var(--muted)}' +
  '.er details{margin:6px 0 0}.er summary{cursor:pointer;color:var(--sub);font-size:13px}.er table{width:100%;border-collapse:collapse;font-size:13px;margin:6px 0}' +
  '.er th,.er td{text-align:right;padding:3px 0;font-weight:400}.er th:first-child,.er td:first-child{text-align:left}.er th{color:var(--muted)}' +
  '.mr{display:flex;align-items:center;gap:6px;padding:6px 0;border-top:1px solid var(--line);font-size:13px}.mr span{flex:1;min-width:0}' +
  '.ch{margin:6px 0 0;padding:8px 0 0;border-top:1px dashed var(--line);font-size:13px}.ch b{font-weight:600}.ch .wn{color:var(--red);display:block;margin-top:2px}' +
  '.ch .ok{color:var(--gr);display:block}.rg h3{font-size:15px;margin:14px 0 4px}.rg ul{margin:0;padding-left:18px;font-size:14px}.rg li{margin:3px 0}' +
  '.rg table{width:100%;border-collapse:collapse;font-size:14px;margin:6px 0}.rg td{padding:3px 0}.rg td:last-child{text-align:right}.rg tr.hit td{color:var(--gr);font-weight:600}' +
  '.rg .hs{font-size:13px;color:var(--muted);margin:2px 0}';
const css = () => {
  if (D.getElementById('er-css')) return;
  const s = D.createElement('style'); s.id = 'er-css'; s.textContent = CSS; D.head.append(s);
};
const row = (h, a, b, cls) => h.el('div', {class: 'rw' + (cls ? ' ' + cls : '')}, h.el('span', {}, a), h.el('span', {}, b));

function form(e, h, m) {
  const {el, btn} = h, f = {};
  const inp = (k, ph, o) => f[k] = el('input', Object.assign({class: 'fl', placeholder: ph, 'aria-label': ph}, o));
  f.shop = el('select', {class: 'fl', 'aria-label': '어디서'}, e.manual.shops.map(s => el('option', {value: s}, s)));
  inp('day', '날짜', {type: 'date', max: e.today, value: m ? m.day : e.today});
  inp('product', '상품 이름 (없어도 돼요)', {maxlength: 80, value: m ? m.product : null});
  inp('amount', '금액 (원)', {inputmode: 'numeric', value: m && m.amount ? m.amount : null});
  inp('commission', '내 수익 (원, 없어도 돼요)', {inputmode: 'numeric', value: m && m.commission ? m.commission : null});
  inp('memo', '메모 (없어도 돼요)', {maxlength: 200, value: m ? m.memo : null});
  if (m) f.shop.value = m.shop;
  const box = h.sheet(m ? '수익 고치기' : '수익 직접 넣기', el('div'));
  box.append(el('p', {class: 'note'}, e.note), ...[['어디서', 'shop'], ['날짜', 'day'], ['상품', 'product'], ['금액', 'amount'], ['내 수익', 'commission'], ['메모', 'memo']]
    .map(([l, k]) => [el('label', {class: 'lab'}, l), f[k]]),
  btn('pb', '저장', async ev => {
    const v = {id: m ? m.id : undefined};
    for (const k in f) v[k] = f[k].value;
    if (await h.exec({action: 'earn.save', args: {}}, v, {btn: ev.currentTarget})) h.back();
  }));
}

const chal = (h, c) => c ? h.el('div', {class: 'ch'}, h.el('b', {}, c.text), c.earned ? h.el('span', {class: 'ok'}, c.earned) : null,
  c.warn ? h.el('span', {class: 'wn'}, c.warn) : null) : null;

/* 보상 기준 시트 (10/23) */
function rewards(e, h) {
  const {el} = h;
  const box = h.sheet('쇼핑몰 보상 기준', el('div', {class: 'rg'}));
  box.append(el('p', {class: 'note'}, e.rewards_note));
  for (const r of e.rewards) {
    box.append(el('h3', {}, r.name), el('ul', {}, r.rules.map(t => el('li', {}, t))));
    const c = r.challenge;
    if (!c) continue;
    box.append(chal(h, c));
    if (c.tiers.length) box.append(el('table', {}, el('tr', {}, el('td', {}, '7일 방문'), el('td', {}, '받는 돈')),
      c.tiers.map(t => el('tr', {class: t.hit ? 'hit' : ''}, el('td', {}, t.visits), el('td', {}, t.reward)))));
    if (c.history.length) box.append(el('p', {class: 'hs'}, '지난 판'), ...c.history.map(t => el('p', {class: 'hs'}, t)));
  }
}

function view(e, h) {
  css();
  const {el, btn} = h;
  const out = [el('div', {class: 'top'}, el('h2', {}, e.title), (e.rewards || []).length ? btn('lnk', '보상 기준', () => rewards(e, h)) : null,
    btn('lnk', '수익 직접 넣기', () => form(e, h, null)))];
  out.push(el('div', {class: 'ew'}, e.totals.map(t => el('div', {class: 'ec'}, el('span', {class: 'el'}, t.label), el('b', {}, t.commission),
    el('span', {class: 'es'}, `주문 ${t.orders} · ${t.amount}`)))));
  for (const s of e.sources) {
    out.push(el('article', {class: 'itm es2'}, el('h3', {}, s.label), el('p', {class: 'note', style: 'margin:0 0 6px'}, s.note),
      s.state === 'ok' ? s.cells.map(c => row(h, c.label, `수익 ${c.commission} · ${c.orders}`)) : null, chal(h, s.challenge)));
    if (s.key === 'coupang' && s.state === 'ok') {
      const c = e.coupang;
      out.push(el('details', {class: 'er'}, el('summary', {}, '쿠팡 자세히 (클릭 · 주문 · 주문금액 · 서브아이디)'),
        el('table', {}, el('tr', {}, ['', '클릭', '주문', '주문금액', '수익'].map(x => el('th', {}, x))),
          c.rows.map(r => el('tr', {}, [r.label, r.clicks, r.orders, r.amount, r.commission].map(x => el('td', {}, x))))),
        c.subs.length ? el('table', {}, el('tr', {}, ['서브아이디 (30일)', '클릭', '주문', '수익'].map(x => el('th', {}, x))),
          c.subs.map(r => el('tr', {}, [r.sub, r.clicks, r.orders, r.commission].map(x => el('td', {}, x))))) : null,
        c.sub_note ? el('p', {class: 'note'}, c.sub_note) : null));
    }
  }
  out.push(el('h3', {class: 'h3'}, '최근 구매'));
  out.push(e.recent.length ? el('article', {class: 'itm'}, e.recent.map(r => el('div', {class: 'rc' + (r.cancelled ? ' cx' : '')},
    el('div', {class: 'nm'}, r.name, el('small', {}, r.sub)),
    el('div', {class: 'am'}, r.amount, el('small', {}, `수익 ${r.commission}`)))))
    : el('p', {class: 'empty'}, '아직 구매 내역이 없어요'));
  if (e.manual.rows.length) {
    out.push(el('details', {class: 'er'}, el('summary', {}, `직접 넣은 것 ${e.manual.rows.length}건 — 고치기 · 지우기`),
      e.manual.rows.map(m => el('div', {class: 'mr'}, el('span', {}, m.text), btn('lnk', '고치기', () => form(e, h, m)),
        btn('lnk mute', '지우기', async ev => {
          if (await h.confirmSheet({title: '지울까요?', ok: '지우기', body: m.text})) h.exec({action: 'earn.delete', args: {id: m.id}}, {}, {btn: ev.currentTarget});
        })))));
  }
  return out;
}
W.SBEarn = {view};
})();

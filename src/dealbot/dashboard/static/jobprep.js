/* 취준 (주인 11/22) — /dashboard/jobprep/ 틀: 탭 줄 · 해시 이동 · 대화창 · ⓪ 오늘 · 공용 그리기.
   데이터는 모두 /dashboard/api/jobprep/… (로그인 아래 · no-store) 에서 받는다 — 이 파일에는 데이터가 없다.
   다른 탭 그리기는 처음 열 때 jp_*.js 를 받는다 (meta jp-<이름>). 글은 textContent 로만 넣는다 (HTML 로 끼워 넣지 않음). */
(function () {
  'use strict';
  var BOOT = {};
  try { BOOT = JSON.parse(document.getElementById('jp-boot').textContent || '{}'); } catch (e) { BOOT = {}; }
  var API = '/dashboard/api/jobprep/';
  var LAZY = { jobs: 'jp_jobs', cases: 'jp_jobs', code: 'jp_code', speech: 'jp_talk', eng: 'jp_talk', me: 'jp_misc', acad: 'jp_misc', lib: 'jp_misc', set: 'jp_misc' };
  var TITLES = { today: '오늘', me: '내 소개', acad: '학사·졸업', jobs: '공고', cases: '합격 사례', code: '코딩', lib: '자료함', speech: '스피치·인문학', eng: '영어', set: '설정' };
  var renders = {}, loading = {};
  var main = document.getElementById('jp-main');

  function h(tag, attrs) {
    var el = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) return;
        if (k === 'text') el.textContent = String(v);
        else if (k === 'cls') el.className = v;
        else if (k.slice(0, 2) === 'on') el.addEventListener(k.slice(2), v);
        else el.setAttribute(k, v === true ? '' : String(v));
      });
    }
    for (var i = 2; i < arguments.length; i++) add(el, arguments[i]);
    return el;
  }
  function add(el, c) {
    if (c === null || c === undefined || c === false) return;
    if (Array.isArray(c)) { c.forEach(function (x) { add(el, x); }); return; }
    el.appendChild(typeof c === 'object' ? c : document.createTextNode(String(c)));
  }
  function toast(t) {
    var el = document.getElementById('toast');
    el.textContent = t; el.hidden = false;
    clearTimeout(toast.t); toast.t = setTimeout(function () { el.hidden = true; }, 3200);
  }
  function api(path, body, opt) {
    opt = opt || {};
    var o = { method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', headers: {} };
    if (body !== undefined) {
      o.headers['Content-Type'] = 'application/json'; o.headers['X-Dash-Token'] = BOOT.csrf || '';
      o.body = JSON.stringify(body);
    }
    return fetch(API + path, o).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (r.status === 401) { location.href = '/dashboard/login?next=' + encodeURIComponent('/dashboard/jobprep/'); }
        if (!r.ok) { var err = new Error(d.text || '오류'); err.code = d.code; err.status = r.status; throw err; }
        return d;
      });
    });
  }
  function upload(kind, ref, blob) {
    return fetch(API + 'upload', { method: 'POST', credentials: 'same-origin', body: blob,
      headers: { 'X-Dash-Token': BOOT.csrf || '', 'X-Jp-Kind': kind, 'X-Jp-Ref': String(ref || ''), 'Content-Type': (blob.type || 'application/octet-stream').split(';')[0] } })
      .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.text || '올리지 못했어요'); return d; }); });
  }
  function fail(e) { toast(e && e.message ? e.message : '실패했어요'); }
  function card(title) { var c = h('section', { cls: 'card jp-card' }); if (title) c.appendChild(h('h2', { cls: 'jp-h', text: title })); for (var i = 1; i < arguments.length; i++) add(c, arguments[i]); return c; }
  function btn(text, fn, cls) { return h('button', { type: 'button', cls: 'jp-btn' + (cls ? ' ' + cls : ''), text: text, onclick: fn }); }
  function link(text, href) { return h('a', { href: href, text: text, rel: 'noopener noreferrer', target: href.charAt(0) === '#' ? null : '_blank' }); }
  function when(ts) { if (!ts) return ''; var d = new Date(ts * 1000); return (d.getMonth() + 1) + '/' + d.getDate() + ' ' + String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); }
  function busy(el, t) { el.textContent = ''; el.appendChild(h('p', { cls: 'jp-sub', text: t || '불러오는 중…' })); }
  function copy(text) {
    if (navigator.clipboard) return navigator.clipboard.writeText(text).then(function () { toast('복사했어요'); }, function () { toast('복사하지 못했어요'); });
    var ta = h('textarea'); ta.value = text; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); toast('복사했어요'); } catch (e) { toast('복사하지 못했어요'); }
    ta.remove();
  }
  function bar(done, need, label) {
    var pct = need > 0 ? Math.min(100, Math.round(done * 100 / need)) : 0;
    return h('div', { cls: 'jp-bar' }, h('div', { cls: 'jp-bar-l', text: label }), h('div', { cls: 'jp-bar-t' }, h('div', { cls: 'jp-bar-f', style: 'width:' + pct + '%' })),
      h('div', { cls: 'jp-bar-n', text: need > 0 ? done + '/' + need : String(done) }));
  }
  function addToLib(item) { return api('lib/add', item).then(function () { toast('자료함에 넣었어요'); }, fail); }

  var JP = window.JP = { h: h, api: api, upload: upload, toast: toast, fail: fail, card: card, btn: btn, link: link, when: when, busy: busy, copy: copy, bar: bar,
    addToLib: addToLib, boot: BOOT, ctx: '', reg: function (tab, fn) { renders[tab] = fn; }, rerender: function () { route(); } };

  // ── 탭 이동 ──
  function tabOf() { var m = /^#\/([a-z]+)(?:\/(.*))?$/.exec(location.hash || ''); return m ? [m[1], m[2] || ''] : [localStorage.getItem('jp-tab') || 'today', '']; }
  function load(name) {
    if (loading[name]) return loading[name];
    var meta = document.querySelector('meta[name="jp-' + name + '"]');
    loading[name] = new Promise(function (ok, no) {
      if (!meta) { no(new Error('없는 화면')); return; }
      var s = document.createElement('script'); s.src = meta.content; s.defer = true; s.onload = ok; s.onerror = function () { no(new Error('화면을 받지 못했어요')); };
      document.head.appendChild(s);
    });
    return loading[name];
  }
  function route() {
    var t = tabOf(), tab = TITLES[t[0]] ? t[0] : 'today', arg = t[1];
    localStorage.setItem('jp-tab', tab);
    JP.tab = tab; JP.ctx = '';
    document.getElementById('jp-ttl').textContent = '취준 · ' + TITLES[tab];
    Array.prototype.forEach.call(document.querySelectorAll('.jp-tab'), function (a) {
      if (a.getAttribute('data-tab') === tab) { a.setAttribute('aria-current', 'page'); a.scrollIntoView({ inline: 'center', block: 'nearest' }); } else a.removeAttribute('aria-current');
    });
    main.textContent = '';
    var box = h('div', { cls: 'jp-view' }); main.appendChild(box);
    var run = function () { try { renders[tab](box, arg); } catch (e) { busy(box, '화면을 그리지 못했어요'); } };
    if (renders[tab]) run();
    else { busy(box); load(LAZY[tab]).then(run, function (e) { busy(box, e.message); }); }
  }
  window.addEventListener('hashchange', route);

  // ── ⓪ 오늘 ──
  JP.reg('today', function (box) {
    busy(box);
    api('today').then(function (d) {
      box.textContent = '';
      var w = d.window;
      box.appendChild(card(null, h('div', { cls: 'jp-dday' },
        h('div', null, h('b', { text: d.discharge.d === null ? '-' : 'D-' + d.discharge.d }), h('span', { cls: 'jp-sub', text: ' 전역 ' + d.discharge.date })),
        w ? h('div', null, h('b', { text: w.open ? '지금 열림' : 'D-' + w.d }), h('span', { cls: 'jp-sub', text: ' ' + w.label + (w.guess ? ' (추정)' : '') + ' · ' + w.start + '~' + w.end })) : null)));
      box.appendChild(card('오늘 할 일', h('ul', { cls: 'jp-check' }, d.todo.map(function (t) {
        return h('li', { cls: t.done ? 'done' : '' }, h('a', { href: t.go }, h('span', { cls: 'jp-box', text: t.done ? '✓' : '' }), t.label + (t.n ? ' (' + t.n + ')' : '')));
      })), d.code_week_left ? h('p', { cls: 'jp-sub', text: '이번 주 코딩 ' + d.code_week_left + '개 남음' }) : null));
      if (d.deadlines.length) box.appendChild(card('마감 7일 안', h('ul', { cls: 'jp-list' }, d.deadlines.map(function (j) {
        return h('li', null, h('b', { text: j.deadline + ' ' }), (j.company || '') + ' · ' + j.title + (j.user_status ? ' [' + j.user_status + ']' : ''));
      }))));
      var c = d.changes, rows = [];
      [['new', '새 공고'], ['closed', '닫힘'], ['watch', '지켜보는 회사 바뀜']].forEach(function (k) {
        var x = c[k[0]]; if (!x || !x.n) return;
        rows.push(h('h3', { cls: 'jp-h3', text: k[1] + ' ' + x.n }));
        rows.push(h('ul', { cls: 'jp-list' }, x.items.map(function (i) { return h('li', { text: i.note }); })));
        if (x.more) rows.push(link('더 보기 (' + x.more + ')', '#/jobs'));
      });
      box.appendChild(card('어제 바뀐 것', rows.length ? rows : h('p', { cls: 'jp-sub', text: '지난 24시간 바뀐 것이 없어요' })));
    }, function (e) { busy(box, e.message); });
  });

  // ── 아래 대화창 ──
  var sheet = document.getElementById('jp-sheet');
  function spendLine(s) {
    if (!s) return '';
    var c = s.claude;
    return 'AI(Claude) 오늘 $' + c.today.toFixed(2) + '/$' + c.day_cap + ' · 이번 달 $' + c.month.toFixed(2) + '/$' + c.month_cap;
  }
  function openChat() {
    sheet.hidden = false; sheet.textContent = '';
    var tab = JP.tab, ctx = JP.ctx || '';
    var head = h('div', { cls: 'jp-sheet-h' }, h('b', { text: '물어보기 · ' + TITLES[tab] + (ctx ? ' ' + ctx : '') }), btn('닫기', function () { sheet.hidden = true; }, 'ghost'));
    var spend = h('p', { cls: 'jp-sub' });
    var list = h('div', { cls: 'jp-msgs' });
    var ta = h('textarea', { cls: 'jp-in', rows: 3, placeholder: '무엇이든 물어보세요' });
    function show(m) {
      list.appendChild(h('div', { cls: 'jp-msg ' + (m.role === 'user' ? 'me' : 'ai') }, h('div', { text: m.text }),
        m.role !== 'user' ? h('button', { type: 'button', cls: 'jp-mini', text: '자료함에', onclick: function () { addToLib({ kind: 'card', title: m.text.slice(0, 40), body: m.text, src_tab: tab, tags: [tab] }); } }) : null));
      list.scrollTop = list.scrollHeight;
    }
    function send() {
      var t = ta.value.trim(); if (!t) return;
      show({ role: 'user', text: t }); ta.value = '';
      var wait = h('div', { cls: 'jp-msg ai', text: '…' }); list.appendChild(wait);
      api('chat', { tab: tab, ctx: ctx, text: t }).then(function (d) { wait.remove(); show({ role: 'assistant', text: d.reply }); spend.textContent = spendLine(d.spend); },
        function (e) { wait.remove(); show({ role: 'assistant', text: e.message }); });
    }
    sheet.appendChild(h('div', { cls: 'jp-sheet-in' }, head, spend, list, ta,
      h('div', { cls: 'jp-row' }, btn('보내기', function () { send(); }))));
    api('chat?tab=' + encodeURIComponent(tab) + '&ctx=' + encodeURIComponent(ctx)).then(function (d) { d.messages.forEach(show); spend.textContent = spendLine(d.spend); }, fail);
    ta.focus();
  }
  document.getElementById('jp-chat-open').addEventListener('click', openChat);
  JP.openChat = openChat;
  route();
})();

/* 취준 ⑤ 코딩 (CtCI) — 오늘 몫 · 사다리(개념 → 몸풀기 → 책 문제 → 힌트 3단 → 풀이(잠금) → 한 단계 더) · 채점 · 복무 중 모드.
   책 글은 이 파일에 없다 (로그인 아래 API 로만 · no-store). */
(function () {
  'use strict';
  var JP = window.JP, h = JP.h;

  function pre(t) { return h('pre', { cls: 'jp-pre', text: t || '' }); }
  function para(t) { return h('div', { cls: 'jp-text', text: t || '' }); }

  JP.reg('code', function (box, arg) { if (arg) question(box, decodeURIComponent(arg)); else today(box); });

  function today(box) {
    JP.busy(box);
    JP.api('code/today').then(function (d) {
      box.textContent = '';
      if (!d.ready) { box.appendChild(JP.card('코딩', h('p', { cls: 'jp-sub', text: d.text }))); return; }
      var q = d.quota;
      var svc = h('input', { type: 'checkbox', checked: d.service_mode, onchange: function (ev) { JP.api('code/mode', { service: ev.target.checked }).then(function () { JP.rerender(); }, JP.fail); } });
      box.appendChild(JP.card('오늘 몫', h('p', { text: '하루 ' + q.per_day + '문제 · 이번 주 ' + q.week_done + '/' + q.per_week + ' · 남은 ' + q.remain + '/' + q.total }),
        h('label', { cls: 'jp-row' }, svc, ' 복무 중 모드 (개념 + 종이 풀이 · 자기 채점)'),
        d.sandbox && !d.sandbox.strong ? h('p', { cls: 'jp-warn', text: d.sandbox.mode === 'rlimit' ? '격리: 약함 — 제한(rlimit)만 걸고 돌려요 (시험 설정)' : '격리를 쓸 수 없어 코드를 돌리지 않아요 — 자기 채점으로 (서버 systemd-run 확인 필요)' }) : null));
      box.appendChild(JP.card('새 문제', d.new.map(function (r) {
        return h('div', { cls: 'jp-row' }, JP.link(r.qid + ' ' + r.title, '#/code/' + r.qid), h('span', { cls: 'jp-sub', text: r.kind === 'code' ? ' 코드' : ' 설명' + (r.ladder ? '' : ' · 준비 전') }));
      })));
      if (d.reviews.length) box.appendChild(JP.card('복습', d.reviews.map(function (r) { return h('div', null, JP.link(r.qid + ' ' + r.title + ' (' + r.due + ')', '#/code/' + r.qid)); })));
      box.appendChild(JP.card('장별 진도', d.chapters.map(function (c) { return JP.bar(c.solved, c.n, c.ch + '. ' + c.title); })));
    }, function (e) { JP.busy(box, e.message); });
  }

  function question(box, qid) {
    JP.ctx = qid;
    JP.busy(box);
    JP.api('code/q/' + qid).then(function (d) {
      box.textContent = '';
      box.appendChild(h('p', null, JP.link('← 오늘 몫', '#/code')));
      box.appendChild(JP.card(d.q.qid + ' ' + d.q.title, h('div', { cls: 'jp-sub', text: d.q.ch_title + ' · 책 ' + d.q.q_part + '권 ' + d.q.q_page + '쪽' }), para(d.question)));
      if (d.making) {
        box.appendChild(JP.card('사다리 만드는 중', h('p', { cls: 'jp-sub', text: d.err ? '못 만들었어요: ' + d.err : '처음 한 번만 만들어요 — 5초마다 확인' })));
        if (!d.err) setTimeout(function () { if (JP.ctx === qid && location.hash.indexOf(qid) >= 0) question(box, qid); }, 5000);
        return;
      }
      box.appendChild(h('details', { cls: 'card jp-card', open: true }, h('summary', { text: '① 개념' }), para(d.concept)));
      if (d.kind === 'code') {
        var ws = d.warmups || [], shown = d.warmups_shown || 2;
        var wbox = h('details', { cls: 'card jp-card' }, h('summary', { text: '② 몸풀기 ' + Math.min(shown, ws.length) + (ws.length > shown ? ' (+' + (ws.length - shown) + ')' : '') }));
        ws.forEach(function (w, i) {
          var el = item(qid, 'w' + i, w, false);
          if (i >= shown) { var more = h('details', null, h('summary', { text: '더 보기' }), el); wbox.appendChild(more); } else wbox.appendChild(el);
        });
        box.appendChild(wbox);
        box.appendChild(JP.card('③ 책 문제', item(qid, 'book', d.book, !d.tests_ready)));
      } else {
        var ans = h('textarea', { cls: 'jp-in jp-code', rows: 8, placeholder: '글로 답하기 — 요점 ' + (d.rubric_n || 0) + '개와 견줘요' });
        var out = h('div', { cls: 'jp-text' });
        box.appendChild(JP.card('③ 설명해 보기', ans, JP.btn('견줘 보기', function () {
          out.textContent = '보는 중…';
          JP.api('code/self', { qid: qid, result: 'explain', text: ans.value }).then(function (r) { out.textContent = r.text; }, function (e) { out.textContent = e.message; });
        }), out));
      }
      var hints = h('div');
      function drawHints(list) { hints.textContent = ''; list.forEach(function (t, i) { hints.appendChild(h('p', { cls: 'jp-hint', text: (i + 1) + '. ' + t })); }); }
      drawHints(d.hints);
      box.appendChild(JP.card('④ 힌트 (' + d.hints.length + '/' + d.n_hints + ')', hints, d.hints.length < d.n_hints ? JP.btn('힌트 하나 더', function () {
        JP.api('code/step', { qid: qid, step: 'hint' }).then(function (r) { drawHints(r.hints); }, JP.fail);
      }, 'ghost') : null));
      var sol = h('div');
      function drawSol(r) { sol.textContent = ''; sol.appendChild(para(r.solution)); if (r.ref_solution) sol.appendChild(pre(r.ref_solution)); }
      if (d.saw_solution) drawSol(d);
      box.appendChild(JP.card('⑤ 풀이', sol, d.saw_solution ? null : JP.btn('풀이 열기 (복습 간격이 처음부터)', function () {
        JP.api('code/step', { qid: qid, step: 'solution' }).then(drawSol, JP.fail);
      }, 'ghost'), JP.btn('책 풀이와 비교', function () {
        var o = h('div', { cls: 'jp-text', text: '비교하는 중…' }); sol.appendChild(o);
        JP.api('code/compare', { qid: qid }).then(function (r) { o.textContent = r.text; }, function (e) { o.textContent = e.message; });
      }, 'ghost')));
      box.appendChild(JP.card('⑥ 한 단계 더', para(d.twist)));
      if (d.leetcode && d.leetcode.length) box.appendChild(JP.card('비슷한 문제', d.leetcode.map(function (l) { return h('div', null, JP.link(l.name, l.url)); })));
      box.appendChild(JP.card('복무 중 · 자기 채점', h('div', { cls: 'jp-row' },
        JP.btn('풀었음', function () { JP.api('code/self', { qid: qid, result: 'solved' }).then(function () { JP.toast('기록했어요 — 복습 1·3·7·21일'); }, JP.fail); }),
        JP.btn('막힘', function () { JP.api('code/self', { qid: qid, result: 'stuck' }).then(function () { JP.toast('기록했어요'); }, JP.fail); }, 'ghost'))));
    }, function (e) { JP.busy(box, e.message); });
  }

  function item(qid, key, it, selfOnly) {
    var el = h('div', { cls: 'jp-item' });
    if (key !== 'book') el.appendChild(h('b', { text: it.title }));
    if (it.statement) el.appendChild(para(it.statement));
    el.appendChild(h('div', { cls: 'jp-sub', text: '함수: ' + it.fn + ' · 시험 ' + it.n_tests + '개' }));
    (it.examples || []).forEach(function (c) { el.appendChild(pre(it.fn + '(' + JSON.stringify(c.args).slice(1, -1) + ') → ' + JSON.stringify(c.expected))); });
    if (selfOnly) { el.appendChild(h('p', { cls: 'jp-warn', text: '시험 준비 안 됨 — 자기 채점으로 해 주세요' })); return el; }
    var saved = localStorage.getItem('jp-code:' + qid + ':' + key) || 'def ' + it.fn + '():\n    pass\n';
    var ta = h('textarea', { cls: 'jp-in jp-code', rows: 10, spellcheck: 'false', autocapitalize: 'off', autocomplete: 'off' });
    ta.value = saved;
    ta.addEventListener('input', function () { localStorage.setItem('jp-code:' + qid + ':' + key, ta.value); });
    ta.addEventListener('keydown', function (ev) { if (ev.key === 'Tab') { ev.preventDefault(); var s = ta.selectionStart; ta.value = ta.value.slice(0, s) + '    ' + ta.value.slice(ta.selectionEnd); ta.selectionStart = ta.selectionEnd = s + 4; } });
    var out = h('div', { cls: 'jp-result' });
    el.appendChild(ta);
    el.appendChild(h('div', { cls: 'jp-row' }, JP.btn('채점', function () {
      out.textContent = '돌리는 중…';
      JP.api('code/run', { qid: qid, item: key, code: ta.value }).then(function (r) {
        out.textContent = '';
        out.appendChild(h('b', { cls: r.passed === r.total ? 'ok' : 'bad', text: '통과 ' + r.passed + '/' + r.total + (r.solved ? ' — 풀었어요!' : '') }));
        if (r.fail) out.appendChild(pre('입력: ' + r.fail.args + '\n기대: ' + r.fail.expected + '\n내 값: ' + (r.fail.got || '') + (r.fail.err ? '\n오류: ' + r.fail.err : '')));
        if (r.stdout) out.appendChild(pre('출력:\n' + r.stdout));
        var fb = h('div', { cls: 'jp-text', text: '코치가 보는 중…' }); out.appendChild(fb);
        JP.api('code/feedback', { attempt_id: r.attempt_id }).then(function (f) { fb.textContent = f.text; }, function (e) { fb.textContent = e.message; });
      }, function (e) { out.textContent = e.message; });
    })));
    el.appendChild(out);
    return el;
  }
})();

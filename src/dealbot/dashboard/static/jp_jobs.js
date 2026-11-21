/* 취준 ③ 공고 · ④ 합격 사례 (주인 11/22). 데이터 없음 — 모두 /dashboard/api/jobprep/… */
(function () {
  'use strict';
  var JP = window.JP, h = JP.h;
  var ST = { label: { interest: '관심', applied: '지원함', doc_pass: '서류 합격', interview: '면접', result: '결과' } };
  var F = { cat: '', region: '', season: '' };

  function tri(v, yes, no) { return v === 1 ? yes : v === 0 ? no : '?'; }
  function stars(n) { return n ? '★'.repeat(n) + '☆'.repeat(5 - n) : '☆ 아직'; }
  function jobCard(j, reload) {
    var meta = [j.loc || j.region, j.start ? '시작 ' + j.start : '', j.deadline ? '마감 ' + j.deadline : '', '전환 ' + tri(j.conversion, 'O', 'X'),
      '학사 ' + tri(j.bachelor_ok, 'O', 'X')].filter(Boolean).join(' · ');
    var box = h('article', { cls: 'jp-job' },
      h('div', { cls: 'jp-job-h' }, h('b', { text: j.company }), j.tag ? h('span', { cls: 'jp-tag', text: j.tag }) : null,
        j.status ? h('span', { cls: 'jp-tag on', text: ST.label[j.status] || j.status }) : null),
      h('div', null, JP.link(j.title, j.url)),
      h('div', { cls: 'jp-sub', text: meta }),
      h('div', { cls: 'jp-fit' }, h('span', { text: stars(j.fit) }), j.fit_why ? ' ' + j.fit_why : '', j.cases ? h('a', { href: '#/cases/' + encodeURIComponent(j.company), text: ' · 합격 사례 ' + j.cases + '건' }) : null));
    if (j.state === 'closed') { box.appendChild(h('div', { cls: 'jp-sub', text: '닫힘: ' + j.closed_why })); return box; }
    var row = h('div', { cls: 'jp-row' });
    row.appendChild(JP.btn('관심', function () { mark(j.id, 'interest', reload); }, j.status === 'interest' ? 'on' : 'ghost'));
    row.appendChild(JP.btn('지원함', function () { mark(j.id, 'applied', reload); }, j.status === 'applied' ? 'on' : 'ghost'));
    row.appendChild(JP.btn('관심 없음', function () { notMine(j, reload); }, 'ghost'));
    box.appendChild(row);
    return box;
  }
  function mark(id, status, reload) { JP.api('jobs/mark', { id: id, status: status }).then(reload, JP.fail); }
  function notMine(j, reload) {
    var pick = h('div', { cls: 'jp-row' },
      JP.btn('이 공고만', function () { ex('job', String(j.id)); }, 'ghost'), JP.btn('이 회사', function () { ex('company', String(j.company_id)); }, 'ghost'),
      j.cat ? JP.btn('이 분야', function () { ex('cat', j.cat); }, 'ghost') : null);
    function ex(scope, key) { JP.api('jobs/exclude', { scope: scope, key: key }).then(function () { JP.toast('뺐어요 — 제외 목록에서 되돌릴 수 있어요'); reload(); }, JP.fail); }
    JP.toast('어디까지 뺄까요?');
    var s = document.getElementById('jp-sheet'); s.hidden = false; s.textContent = '';
    s.appendChild(h('div', { cls: 'jp-sheet-in' }, h('b', { text: '관심 없음 — ' + j.company }), pick, JP.btn('닫기', function () { s.hidden = true; }, 'ghost')));
    pick.addEventListener('click', function () { s.hidden = true; });
  }

  JP.reg('jobs', function (box) {
    function reload() { JP.busy(box); draw(); }
    function draw() {
      var q = 'jobs?cat=' + encodeURIComponent(F.cat) + '&region=' + encodeURIComponent(F.region) + '&season=' + encodeURIComponent(F.season);
      JP.api(q).then(function (d) {
        box.textContent = '';
        var chips = h('div', { cls: 'jp-chips' });
        chips.appendChild(JP.btn('전체', function () { F.cat = ''; reload(); }, F.cat ? 'ghost' : 'on'));
        Object.keys(d.cats).forEach(function (k) { chips.appendChild(JP.btn(d.cats[k], function () { F.cat = k; reload(); }, F.cat === k ? 'on' : 'ghost')); });
        var reg = h('select', { cls: 'jp-sel', onchange: function () { F.region = reg.value; reload(); } },
          [['', '모든 지역'], ['KR', '한국'], ['SG', '싱가포르'], ['HK', '홍콩'], ['US', '미국'], ['REMOTE', '원격'], ['UNK', '모름']].map(function (o) { var e = h('option', { value: o[0], text: o[1] }); if (o[0] === F.region) e.selected = true; return e; }));
        box.appendChild(JP.card(null, chips, reg));
        d.groups.forEach(function (g) {
          if (!g.items.length) return;
          box.appendChild(JP.card(g.label + ' · ' + g.items.length, g.items.slice(0, 80).map(function (j) { return jobCard(j, reload); })));
        });
        var board = h('div', { cls: 'jp-board' });
        Object.keys(d.board).forEach(function (k) {
          board.appendChild(h('div', { cls: 'jp-col' }, h('b', { text: ST.label[k] + ' ' + d.board[k].length }), d.board[k].map(function (j) {
            var sel = h('select', { cls: 'jp-sel', onchange: function () { mark(j.id, sel.value, reload); } },
              Object.keys(ST.label).map(function (s) { var o = h('option', { value: s, text: ST.label[s] }); if (s === j.status) o.selected = true; return o; }));
            return h('div', { cls: 'jp-mini-card' }, h('div', { text: j.company + ' · ' + j.title }), j.state === 'closed' ? h('div', { cls: 'jp-sub', text: '닫힘' }) : null, sel);
          })));
        });
        box.appendChild(JP.card('지원 보드', board));
        if (d.auto_out_n) box.appendChild(h('details', { cls: 'card jp-card' }, h('summary', { text: '자동으로 뺀 것 ' + d.auto_out_n + '개 (인턴 아님·학사 불가·졸업 시기 안 맞음)' }),
          d.auto_out.map(function (j) { return jobCard(j, reload); })));
        box.appendChild(h('details', { cls: 'card jp-card' }, h('summary', { text: '닫힘 ' + d.closed.length }), d.closed.map(function (j) { return jobCard(j, reload); })));
        box.appendChild(watchCard(d.watch, reload));
        box.appendChild(h('details', { cls: 'card jp-card' }, h('summary', { text: '제외 목록 ' + d.exclusions.length }), d.exclusions.map(function (e) {
          return h('div', { cls: 'jp-row' }, h('span', { text: e.label + ' (' + ({ job: '공고', company: '회사', cat: '분야' })[e.scope] + ')' }),
            JP.btn('되돌리기', function () { JP.api('jobs/restore', { id: e.id }).then(reload, JP.fail); }, 'ghost'));
        })));
        box.appendChild(addCard(reload));
        var last = d.last || {};
        box.appendChild(h('p', { cls: 'jp-sub', text: last.at ? '마지막 수집 ' + JP.when(last.at) + ' · 보드 ' + Object.keys(last.boards || {}).length + '곳' : '아직 수집 전 (매일 06:00)' }));
      }, function (e) { JP.busy(box, e.message); });
    }
    reload();
  });

  function watchCard(rows, reload) {
    var d = h('details', { cls: 'card jp-card' }, h('summary', { text: '지켜보는 회사 ' + rows.filter(function (r) { return r.on; }).length }));
    rows.forEach(function (r) {
      var url = h('input', { cls: 'jp-in', value: r.url, placeholder: 'https:// 채용 페이지 (공개)' });
      d.appendChild(h('div', { cls: 'jp-watch' }, h('label', null, h('input', { type: 'checkbox', checked: r.on, onchange: function (ev) { JP.api('jobs/watch', { id: r.id, on: ev.target.checked }).then(null, JP.fail); } }), ' ' + r.name),
        r.growth ? h('div', { cls: 'jp-sub', text: r.growth }) : null,
        r.changed_at ? h('div', { cls: 'jp-sub', text: '바뀜 ' + JP.when(r.changed_at) + ' · ' + r.note }) : null,
        h('div', { cls: 'jp-row' }, url, JP.btn('저장', function () { JP.api('jobs/watch', { id: r.id, url: url.value.trim() }).then(function () { JP.toast('저장했어요'); }, JP.fail); }, 'ghost'))));
    });
    var nm = h('input', { cls: 'jp-in', placeholder: '회사 이름' }), u = h('input', { cls: 'jp-in', placeholder: 'https:// 채용 페이지' });
    d.appendChild(h('div', { cls: 'jp-row' }, nm, u, JP.btn('더하기', function () { JP.api('jobs/watch', { name: nm.value.trim(), url: u.value.trim() }).then(reload, JP.fail); })));
    return d;
  }
  function addCard(reload) {
    var u = h('input', { cls: 'jp-in', placeholder: 'https:// 공고 주소' }), c = h('input', { cls: 'jp-in', placeholder: '회사' }),
      t = h('input', { cls: 'jp-in', placeholder: '직무' }), dl = h('input', { cls: 'jp-in', type: 'date' });
    return h('details', { cls: 'card jp-card' }, h('summary', { text: '공고 직접 넣기' }), u, c, t, dl,
      JP.btn('넣기', function () { JP.api('jobs/add', { url: u.value.trim(), company: c.value.trim(), title: t.value.trim(), deadline: dl.value }).then(function () { JP.toast('넣었어요'); reload(); }, JP.fail); }));
  }

  // ── ④ 합격 사례 ──
  JP.reg('cases', function (box, arg) {
    var comp = arg ? decodeURIComponent(arg) : '';
    JP.busy(box);
    JP.api('cases?company=' + encodeURIComponent(comp)).then(function (d) {
      box.textContent = '';
      var tree = h('div');
      Object.keys(d.tree).forEach(function (cat) {
        tree.appendChild(h('details', null, h('summary', { text: (d.cats[cat] || cat) }), Object.keys(d.tree[cat]).map(function (c) {
          return h('div', null, JP.link(c + ' (' + d.tree[cat][c] + ')', '#/cases/' + encodeURIComponent(c)));
        })));
      });
      box.appendChild(JP.card('분야 → 회사', Object.keys(d.tree).length ? tree : h('p', { cls: 'jp-sub', text: '아직 모은 사례가 없어요 (월요일 05:50 · 공개 글만)' })));
      if (comp) box.appendChild(JP.card(comp + (d.similar ? ' — 비슷한 회사 사례' : ''), d.items.map(function (c) {
        return h('div', { cls: 'jp-case' }, h('b', { text: (c.company || '') + ' · ' + (c.role || '') }), d.similar ? h('span', { cls: 'jp-tag', text: '비슷한 회사' }) : null,
          ['school', 'major', 'gpa', 'english', 'exp', 'certs', 'questions', 'prep'].filter(function (k) { return c[k]; }).map(function (k) {
            return h('div', { cls: 'jp-kv' }, h('span', { text: ({ school: '학교', major: '전공', gpa: '학점', english: '영어', exp: '경험', certs: '자격증', questions: '면접 질문', prep: '준비 기간' })[k] }), h('span', { text: c[k] }));
          }), JP.link('출처', c.url));
      }), h('div', { cls: 'jp-row' }, JP.btn('내 스펙과 비교', function () {
        var out = h('div', { cls: 'jp-sub', text: '비교하는 중…' }); box.appendChild(out);
        JP.api('cases/compare', { company: comp, role: '' }).then(function (r) {
          out.textContent = '';
          out.appendChild(h('b', { text: '부족한 것' }));
          out.appendChild(h('ol', null, r.gaps.map(function (g) { return h('li', { text: g.what + ' — ' + g.why + ' → ' + g.todo }); })));
          out.appendChild(h('b', { text: '강점' }));
          out.appendChild(h('ul', null, r.strengths.map(function (s) { return h('li', { text: s }); })));
        }, function (e) { out.textContent = e.message; });
      }))));
      box.appendChild(JP.card('이력서 본보기', d.resumes.map(function (r) { return h('div', null, JP.link(r.label, r.url), r.summary ? h('div', { cls: 'jp-sub', text: r.summary }) : null); })));
      var u = h('input', { cls: 'jp-in', placeholder: 'https:// 공개 합격 후기 글' }), cc = h('input', { cls: 'jp-in', placeholder: '회사', value: comp });
      box.appendChild(h('details', { cls: 'card jp-card' }, h('summary', { text: '사례 직접 넣기' }), u, cc,
        JP.btn('읽기', function () { JP.api('cases/add', { url: u.value.trim(), company: cc.value.trim() }).then(function () { JP.toast('넣었어요'); JP.rerender(); }, JP.fail); })));
    }, function (e) { JP.busy(box, e.message); });
  });
})();

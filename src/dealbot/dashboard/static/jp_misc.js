/* 취준 ① 내 소개 · ② 학사·졸업 · ⑥ 자료함 · ⑨ 설정 (주인 11/22). 데이터 없음 — 모두 /dashboard/api/jobprep/… */
(function () {
  'use strict';
  var JP = window.JP, h = JP.h;
  var GP = { 'A+': 4.3, A0: 4.0, 'A-': 3.7, 'B+': 3.3, B0: 3.0, 'B-': 2.7, 'C+': 2.3, C0: 2.0, 'C-': 1.7, 'D+': 1.3, D0: 1.0, 'D-': 0.7, F: 0 };

  // ── ① 내 소개 ──
  JP.reg('me', function (box) {
    JP.busy(box);
    JP.api('profile').then(function (d) {
      box.textContent = '';
      var ins = {};
      box.appendChild(JP.card('내 소개 (판 ' + d.ver + ')', d.fields.map(function (f) {
        ins[f.k] = h('textarea', { cls: 'jp-in', rows: f.v.length > 60 ? 3 : 1 }); ins[f.k].value = f.v;
        return h('label', { cls: 'jp-field' }, h('span', { text: f.label }), ins[f.k]);
      }), JP.btn('저장', function () {
        var body = {}; Object.keys(ins).forEach(function (k) { body[k] = ins[k].value; });
        JP.api('profile', { fields: body }).then(function (r) { JP.toast(r.changed ? '저장했어요 — 공고 적합도를 천천히 다시 볼게요' : '바뀐 게 없어요'); JP.rerender(); }, JP.fail);
      })));
      box.appendChild(JP.card('내 스펙 요약 (적합도·비교에 들어감)', h('p', { cls: 'jp-text', text: d.summary })));
      var out = h('div');
      function show(i) {
        out.textContent = '';
        [['한 줄 소개', i.one_liner_ko], ['One-liner', i.one_liner_en], ['1분 자기소개', i.pitch_ko], ['1-minute pitch', i.pitch_en]].forEach(function (x) {
          out.appendChild(h('div', { cls: 'jp-item' }, h('b', { text: x[0] }), h('div', { cls: 'jp-text', text: x[1] }), JP.btn('복사', function () { JP.copy(x[1]); }, 'ghost')));
        });
      }
      if (d.intro) show(d.intro);
      box.appendChild(JP.card('소개 만들기', out, JP.btn(d.intro ? '다시 만들기' : '만들기 (AI)', function () {
        out.textContent = '만드는 중…'; JP.api('profile/intro', {}).then(function (r) { show(r.intro); }, function (e) { out.textContent = e.message; });
      }, 'ghost')));
    }, function (e) { JP.busy(box, e.message); });
  });

  // ── ② 학사 ──
  function gpa(cs) { var p = 0, c = 0; cs.forEach(function (x) { var g = GP[String(x.grade || '').toUpperCase()]; if (g === undefined) return; p += g * (+x.credits || 0); c += +x.credits || 0; }); return c ? Math.round(p / c * 100) / 100 : null; }
  JP.reg('acad', function (box) {
    JP.busy(box);
    JP.api('acad').then(function (d) {
      box.textContent = '';
      var picked = [];
      var file = h('input', { type: 'file', accept: 'image/*', multiple: true });
      var draftBox = h('div');
      box.appendChild(JP.card('① 올리기 (성적표·졸업 자가진단 화면)', file, JP.btn('올리고 읽기', function () {
        var fs = Array.prototype.slice.call(file.files || []); if (!fs.length) { JP.toast('사진을 골라 주세요'); return; }
        draftBox.textContent = '올리는 중…';
        Promise.all(fs.map(function (f) { return JP.upload('acad', '', f); })).then(function (rs) {
          picked = rs.map(function (r) { return r.doc_id; });
          draftBox.textContent = '읽는 중…';
          return JP.api('acad/parse', { doc_ids: picked });
        }).then(function (r) { edit(r.draft.courses || []); }, function (e) { draftBox.textContent = e.message; });
      }), draftBox));
      function edit(rows) {
        draftBox.textContent = '';
        var t = h('div', { cls: 'jp-table' });
        function row(c) {
          var st = h('select', { cls: 'jp-sel', 'data-k': 'status' }, [['done', '끝'], ['in_progress', '수강 중'], ['planned', '계획']].map(function (o) {
            var e = h('option', { value: o[0], text: o[1] }); if ((c.status || 'done') === o[0]) e.selected = true; return e; }));
          var ge = h('select', { cls: 'jp-sel', 'data-k': 'ge_area' }, [''].concat(d.ge6).map(function (g) { var e = h('option', { value: g, text: g || '6영역 —' }); if ((c.ge_area || '') === g) e.selected = true; return e; }));
          var r = h('div', { cls: 'jp-trow', 'data-id': c.id === undefined ? '' : c.id }, ['name', 'credits', 'grade', 'area', 'term'].map(function (k) { var i = h('input', { cls: 'jp-in sm', value: c[k] === undefined ? '' : c[k], 'data-k': k }); return i; }),
            st, ge, h('label', { cls: 'jp-sub' }, h('input', { type: 'checkbox', 'data-k': 'military', checked: !!c.military }), '군'),
            h('label', { cls: 'jp-sub' }, h('input', { type: 'checkbox', 'data-k': 'no_retake', checked: !!c.no_retake }), '재수강 안 함'),
            h('input', { type: 'hidden', 'data-k': 'retake', value: c.retake ? '1' : '' }),
            JP.btn('×', function () { r.remove(); }, 'ghost'));
          t.appendChild(r);
        }
        rows.forEach(row);
        draftBox.appendChild(h('p', { cls: 'jp-sub', text: '읽은 표 — 틀린 곳을 고친 뒤 "맞아요" (그 전엔 저장하지 않아요)' }));
        draftBox.appendChild(t);
        draftBox.appendChild(h('div', { cls: 'jp-row' }, JP.btn('줄 더하기', function () { row({}); }, 'ghost'), JP.btn('맞아요', function () {
          var cs = Array.prototype.map.call(t.querySelectorAll('.jp-trow'), function (r) {
            var o = {}; Array.prototype.forEach.call(r.querySelectorAll('input,select'), function (i) { var k = i.getAttribute('data-k'); if (!k) return; o[k] = i.type === 'checkbox' ? (i.checked ? 1 : 0) : i.value; });
            o.credits = +o.credits || 0; o.retake = o.retake ? 1 : 0; var id = r.getAttribute('data-id'); if (id) o.id = +id; return o; });
          JP.api('acad/courses', { courses: cs }).then(function () { JP.toast('저장했어요'); JP.rerender(); }, JP.fail);
        })));
      }
      box.appendChild(JP.card('② 들은 과목 ' + d.courses.length + ' · 평점 ' + (d.gpa === null ? '-' : d.gpa + ' / 4.3'), JP.btn('고치기', function () { edit(d.courses); window.scrollTo(0, 0); }, 'ghost')));
      var u = d.upper, g = d.ge, m = d.military;
      box.appendChild(JP.card('③ 남은 학점 (진하게 = 끝 · 옅게 = 수강 중·계획)', d.bars.map(function (b) { return JP.bar(b.done, b.need, b.area + (b.left !== null ? ' (남은 ' + b.left + ')' : ''), b.pending); }),
        JP.bar(u.done, u.need, '3000·4000단위' + (u.short ? ' (모자람 ' + u.short + ')' : ''), u.pending),
        u.extra ? h('p', { cls: 'jp-warn', text: '3000·4000단위 때문에 ' + u.extra + '학점 더 필요 — 남은 과목은 되도록 3000/4000단위로' }) : null,
        h('p', { text: '실제로 남은 학점 ' + d.real_left + ' (칸별 남은 것 + 3000·4000 모자람)' }),
        d.required_missing.length ? h('div', null, h('b', { text: '아직 안 들은 전공기초·전필' }), d.required_missing.map(function (r) {
          return h('div', { cls: r.note ? 'jp-warn' : 'jp-sub', text: r.code + ' ' + r.name + (r.pending ? ' (예정)' : '') + (r.note ? ' — ' + r.note : '') }); })) : null,
        h('p', { cls: 'jp-sub', text: '소속변경 전 EEE 과목은 전공 인정 · CEE 과목은 전선 아님(일반선택) · 최소 이수 학기 없음 (주인 확인 11/23)' }), reqForm(d)));
      box.appendChild(JP.card('교양 6영역 (' + g.need + '영역 필요 · 끝 ' + g.done + ' · 예정 포함 ' + g.with_pending + (g.planned_unknown ? ' + 영역 미정 계획 ' + g.planned_unknown : '') + ')',
        h('div', { cls: 'jp-chips' }, g.areas.map(function (a) { return h('span', { cls: 'jp-tag' + (a.state === 'done' ? ' on' : ''), text: a.area + (a.state === 'done' ? ' ✓' : a.state ? ' (예정)' : '') }); })),
        g.missing.length ? h('p', { cls: g.ok_if_plan ? 'jp-sub' : 'jp-warn', text: '없는 영역: ' + g.missing.join(' · ') + (g.ok_if_plan ? ' — 계획대로면 ' + g.need + '영역 채움 (계획 과목은 서로 다른 영역으로)' : '') }) : null));
      box.appendChild(JP.card('군강좌 (P/NP · 평점 밖) ' + m.used + ' / ' + m.total + '학점', m.terms.map(function (t) { return h('div', { cls: t.over ? 'jp-warn' : 'jp-sub', text: t.term + ': ' + t.credits + '학점' + (t.over ? ' — 학기당 ' + m.per_term + ' 넘음' : '') }); }),
        m.over ? h('p', { cls: 'jp-warn', text: '군강좌 상한을 넘어요' }) : null));
      box.appendChild(planCard(d));
      box.appendChild(retakeCard(d));
    }, function (e) { JP.busy(box, e.message); });
  });
  function reqForm(d) {
    var ins = {};
    var f = h('details', null, h('summary', { text: '졸업 기준 넣기 (전과생 기준 — 자가진단 화면 숫자)' }), ['교양', '전필', '전선', '기타', '총', 'term_max'].map(function (k) {
      ins[k] = h('input', { cls: 'jp-in sm', type: 'number', value: d.req[k] === undefined ? '' : d.req[k] });
      return h('label', { cls: 'jp-field' }, h('span', { text: k === 'term_max' ? '학기 최대 학점' : k }), ins[k]);
    }), JP.btn('저장', function () { var b = {}; Object.keys(ins).forEach(function (k) { if (ins[k].value !== '') b[k] = +ins[k].value; }); JP.api('acad/req', b).then(function () { JP.rerender(); }, JP.fail); }));
    return f;
  }
  function planCard(d) {
    var c = JP.card('④ 수강 계획 (2027-2 · 2028-1 · 2028-2 · 겨울 계절학기 없음)');
    var out = h('div');
    d.terms.forEach(function (t) {
      var cs = d.plans[t] || [];
      out.appendChild(h('div', { cls: 'jp-item' }, h('b', { text: t + ' · ' + cs.reduce(function (s, x) { return s + (+x.credits || 0); }, 0) + '학점' }), cs.map(function (x) { return h('div', { text: x.name + ' (' + x.credits + ', ' + x.area + ')' }); })));
    });
    c.appendChild(out);
    var sug = h('div');
    c.appendChild(JP.btn('과목 제안 받기 (AI)', function () {
      sug.textContent = '만드는 중…';
      JP.api('acad/plan', { action: 'suggest' }).then(function (r) {
        sug.textContent = '';
        Object.keys(r.suggest).forEach(function (t) { sug.appendChild(h('div', { cls: 'jp-item' }, h('b', { text: t }), r.suggest[t].map(function (x) { return h('div', { text: x.name + ' (' + x.credits + ') — ' + x.why }); }))); });
        r.errors.forEach(function (e) { sug.appendChild(h('p', { cls: 'jp-warn', text: e })); });
        sug.appendChild(JP.btn('이대로 저장', function () { JP.api('acad/plan', { terms: r.suggest, force: true }).then(function () { JP.rerender(); }, JP.fail); }));
      }, function (e) { sug.textContent = e.message; });
    }, 'ghost'));
    c.appendChild(sug);
    return c;
  }
  function retakeCard(d) {
    var cap = GP[d.retake_max] === undefined ? 4.0 : GP[d.retake_max];
    var out = h('p', { cls: 'jp-text' });
    var sels = [];
    var list = h('div');
    d.courses.forEach(function (c) {
      var s = h('select', { cls: 'jp-sel' }, [h('option', { value: '', text: '그대로' })].concat(Object.keys(GP).map(function (g) { return h('option', { value: g, text: g }); })));
      s.addEventListener('change', calc); sels.push([c, s]);
      list.appendChild(h('div', { cls: 'jp-row' }, h('span', { text: c.name + ' ' + c.grade }), s));
    });
    function calc() {
      var now = gpa(d.courses);
      var next = gpa(sels.map(function (p) { if (!p[1].value) return p[0]; var g = Math.min(GP[p[1].value], cap); var k = Object.keys(GP).filter(function (x) { return GP[x] === g; })[0]; return { credits: p[0].credits, grade: k }; }));
      out.textContent = '지금 ' + (now === null ? '-' : now + ' (4.5 환산 ' + (Math.round(now * 4.5 / 4.3 * 100) / 100) + ')') + ' → 재수강 뒤 ' + (next === null ? '-' : next + ' (4.5 환산 ' + (Math.round(next * 4.5 / 4.3 * 100) / 100) + ')');
    }
    calc();
    var r = d.retake, auto = h('div');
    var plus = h('input', { type: 'checkbox', onchange: drawAuto });
    function drawAuto() {
      var x = plus.checked ? r.bplus : r.b0, L = x.load;
      auto.textContent = '';
      auto.appendChild(h('p', { text: '추천 (평점 이득 = 학점 × (' + r.max + ' − 지금) 큰 순) · 위 ' + x.top.length + '과목을 ' + r.max + ' 로 → 평점 ' +
        (x.after === null ? '-' : x.after + ' (4.5 환산 ' + x.after45 + ')') + ' (지금 ' + (r.now === null ? '-' : r.now) + ')' }));
      if (!x.cands.length) auto.appendChild(h('p', { cls: 'jp-sub', text: '후보 없음' }));
      x.cands.forEach(function (c) {
        auto.appendChild(h('div', { cls: 'jp-row' + (x.top.indexOf(c.id) >= 0 ? '' : ' jp-sub') }, h('span', { text: (x.top.indexOf(c.id) >= 0 ? '✓ ' : '') + c.name + ' ' + c.grade + ' · ' + c.credits + '학점 · +' + c.gain }))); 
      });
      if (L.per_term !== null) auto.appendChild(h('p', { cls: L.over ? 'jp-warn' : 'jp-sub', text: '재수강은 학점이 늘지 않아요: 실제로 남은 ' + L.left_credits + '학점 + 재수강 ' + L.retake_credits +
        '학점 ÷ ' + L.terms + '학기 = 학기당 ' + L.per_term + '학점' + (L.over ? ' — ' + L.term_max + '학점 넘음' : '') }));
    }
    drawAuto();
    return JP.card('⑤ 재수강 (' + r.used + '회 씀 · ' + r.left + '회 남음 / ' + r.total + '회 · 최고 ' + r.max + ')',
      h('p', { cls: 'jp-sub' }, r.note + ' · ', JP.link('학교 안내', r.src)),
      h('label', { cls: 'jp-row' }, plus, ' B+ 도 후보에 넣기 (기본 B0 이하)'), auto,
      h('details', null, h('summary', { text: '직접 골라 계산' }), list, out));
  }

  // ── ⑥ 자료함 ──
  JP.reg('lib', function (box, arg) {
    var tag = arg ? decodeURIComponent(arg) : '';
    JP.busy(box);
    JP.api('lib?tag=' + encodeURIComponent(tag)).then(function (d) {
      box.textContent = '';
      var t = h('input', { cls: 'jp-in', placeholder: '제목' }), u = h('input', { cls: 'jp-in', placeholder: 'https:// (있으면)' }),
        b = h('textarea', { cls: 'jp-in', rows: 3, placeholder: '메모' }), tg = h('input', { cls: 'jp-in', placeholder: '꼬리표 (쉼표로)' });
      box.appendChild(JP.card('넣기', t, u, b, tg, JP.btn('넣기', function () {
        JP.api('lib/add', { kind: u.value ? 'link' : 'note', title: t.value, url: u.value.trim(), body: b.value, tags: tg.value.split(',').map(function (x) { return x.trim(); }).filter(Boolean), src_tab: 'lib' }).then(function () { JP.rerender(); }, JP.fail);
      })));
      box.appendChild(h('div', { cls: 'jp-chips' }, JP.link('전체', '#/lib'), d.tags.map(function (x) { return JP.link(x[0] + ' ' + x[1], '#/lib/' + encodeURIComponent(x[0])); })));
      d.items.forEach(function (it) {
        var sum = h('div', { cls: 'jp-sub' });
        box.appendChild(JP.card(null, h('b', { text: it.title }), it.url ? h('div', null, JP.link(it.url, it.url)) : null, it.body ? h('div', { cls: 'jp-text', text: it.body }) : null,
          h('div', { cls: 'jp-sub', text: (it.tags || []).join(' · ') + ' · ' + JP.when(it.at) }),
          h('div', { cls: 'jp-row' }, it.body ? JP.btn('요약', function () { sum.textContent = '…'; JP.api('lib/summary', { id: it.id }).then(function (r) { sum.textContent = r.summary; }, function (e) { sum.textContent = e.message; }); }, 'ghost') : null,
            JP.btn('지우기', function () { JP.api('lib/del', { id: it.id }).then(function () { JP.rerender(); }, JP.fail); }, 'ghost')), sum));
      });
    }, function (e) { JP.busy(box, e.message); });
  });

  // ── ⑨ 설정 ──
  JP.reg('set', function (box) {
    JP.busy(box);
    JP.api('settings').then(function (d) {
      box.textContent = '';
      var s = d.spend, c = d.caps;
      box.appendChild(JP.card('AI 쓴 양 (상한은 설정 파일 값)',
        h('p', { text: 'Claude 오늘 $' + s.claude.today.toFixed(2) + ' / $' + s.claude.day_cap + ' · 이번 달 $' + s.claude.month.toFixed(2) + ' / $' + c.claude_month + (s.claude.key ? '' : ' · 키 없음') }),
        h('p', { cls: 'jp-sub', text: '모델: 빠른 일 ' + d.models.fast + ' · 깊은 일 ' + d.models.good + ' · 사다리 미리 만들기 하루 $' + c.ladder_day }),
        s.gemini.key === 'own' ? h('p', { text: '무료 Gemini 키 사용 중 · 오늘 fast ' + s.gemini.fast + '/' + c.gemini_calls.fast + ' · good ' + s.gemini.good + '/' + c.gemini_calls.good }) : null));
      box.appendChild(JP.card('지원 창 (추정 — 설정 파일)', d.windows.map(function (w) { return h('div', { text: w.label + ': ' + w.start + ' ~ ' + w.end + (w.guess === '1' ? ' (추정)' : '') }); }),
        h('p', { cls: 'jp-sub', text: '전역 ' + d.discharge + ' · 책 끝내기 ' + d.book_finish_by + ' · 재수강 상한 ' + d.retake_max })));
      box.appendChild(JP.card('서버', h('p', { text: '코드 격리: ' + (d.sandbox.strong ? 'systemd (강함)' : d.sandbox.mode === 'off' ? '꺼짐' : d.sandbox.mode === 'rlimit' ? 'rlimit (약함 · 시험 설정)' : '못 씀 — 코드 채점 멈춤 (systemd-run 확인)') }),
        h('p', { text: '발음 모델: ' + (d.pron_ready ? '준비됨' : '준비 중 (tools/jobprep_setup.py)') }), d.busy ? h('p', { cls: 'jp-sub', text: '지금 도는 일: ' + d.busy }) : null));
      var src = Object.keys(d.sources || {}).map(function (k) { var x = d.sources[k]; return h('div', { cls: 'jp-sub', text: k + ': ' + (x.until && x.until * 1000 > Date.now() ? '쉼 (' + x.why + ')' : x.why ? '실패 ' + x.n + '번 (' + x.why + ')' : '정상') }); });
      var last = d.collect_last || {};
      box.appendChild(JP.card('공고 수집', h('p', { text: last.at ? '마지막 ' + JP.when(last.at) : '아직 없음' }),
        Object.keys(last.boards || {}).map(function (k) { var b = last.boards[k]; return h('div', { cls: 'jp-sub', text: k + ': ' + (b.ok ? '새 ' + b.new + ' · 닫힘 ' + b.closed + ' · 규칙 밖 ' + b.rule_out : '실패 ' + b.why) }); }),
        h('details', null, h('summary', { text: '출처 상태' }), src),
        JP.btn('지금 한 번 (하루 2번까지)', function () { JP.api('jobs/collect', {}).then(function (r) { JP.toast('시작했어요 · 오늘 ' + r.left + '번 남음'); }, JP.fail); }, 'ghost')));
      box.appendChild(JP.card('최근 뒤 작업', (d.runs || []).map(function (r) { return h('div', { cls: 'jp-sub', text: JP.when(r.at) + ' ' + r.name + ' (' + r.s + '초) ' + JSON.stringify(r.result).slice(0, 120) }); })));
      box.appendChild(JP.card('제외 목록', d.exclusions.length ? d.exclusions.map(function (e) {
        return h('div', { cls: 'jp-row' }, h('span', { text: e.label }), JP.btn('되돌리기', function () { JP.api('jobs/restore', { id: e.id }).then(function () { JP.rerender(); }, JP.fail); }, 'ghost'));
      }) : h('p', { cls: 'jp-sub', text: '없음' })));
      var svc = h('input', { type: 'checkbox', checked: d.service_mode, onchange: function (ev) { JP.api('code/mode', { service: ev.target.checked }).then(null, JP.fail); } });
      box.appendChild(JP.card('복무 중 모드', h('label', null, svc, ' 코딩은 개념 + 종이 풀이 · 자기 채점 위주')));
      box.appendChild(h('p', null, JP.link('지켜보는 회사·분야 거르기는 공고 탭에서', '#/jobs')));
    }, function (e) { JP.busy(box, e.message); });
  });
})();

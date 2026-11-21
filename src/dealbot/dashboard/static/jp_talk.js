/* 취준 ⑦ 스피치·인문학 · ⑧ 영어 (주인 11/22) — 녹음은 브라우저 MediaRecorder → /dashboard/api/jobprep/upload (로그인 아래). */
(function () {
  'use strict';
  var JP = window.JP, h = JP.h;

  function recorder(kind, ref, onDone, maxS) {
    var state = { rec: null, chunks: [], t: null };
    var b = JP.btn('● 녹음', toggle);
    var audio = h('audio', { controls: true, hidden: true });
    function toggle() {
      if (state.rec) { state.rec.stop(); return; }
      if (!navigator.mediaDevices || !window.MediaRecorder) { JP.toast('이 브라우저는 녹음을 못 해요'); return; }
      navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
        var mime = ['audio/webm', 'audio/mp4', 'audio/ogg'].filter(function (m) { return MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(m); })[0];
        var rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
        state.rec = rec; state.chunks = [];
        rec.ondataavailable = function (e) { if (e.data && e.data.size) state.chunks.push(e.data); };
        rec.onstop = function () {
          clearTimeout(state.t);
          stream.getTracks().forEach(function (t) { t.stop(); });
          var blob = new Blob(state.chunks, { type: (rec.mimeType || mime || 'audio/webm').split(';')[0] });
          state.rec = null; b.textContent = '● 녹음';
          audio.src = URL.createObjectURL(blob); audio.hidden = false;
          b.disabled = true;
          JP.upload(kind, ref, blob).then(function (d) { b.disabled = false; onDone(d); }, function (e) { b.disabled = false; JP.fail(e); });
        };
        rec.start(); b.textContent = '■ 멈춤';
        state.t = setTimeout(function () { if (state.rec) state.rec.stop(); }, (maxS || 15) * 1000);
      }, function () { JP.toast('마이크를 쓸 수 없어요'); });
    }
    return h('div', { cls: 'jp-rec' }, b, audio);
  }
  JP.recorder = recorder;

  function pauseBars(mine, ref) {
    var max = Math.max(mine || 0, ref || 0, 0.1);
    return h('div', { cls: 'jp-pair' },
      JP.h('div', { cls: 'jp-bar' }, h('div', { cls: 'jp-bar-l', text: '원본' }), h('div', { cls: 'jp-bar-t' }, h('div', { cls: 'jp-bar-f', style: 'width:' + Math.round((ref || 0) * 100 / max) + '%' })), h('div', { cls: 'jp-bar-n', text: (ref || 0).toFixed(2) + '초' })),
      JP.h('div', { cls: 'jp-bar' }, h('div', { cls: 'jp-bar-l', text: '나' }), h('div', { cls: 'jp-bar-t' }, h('div', { cls: 'jp-bar-f me', style: 'width:' + Math.round((mine || 0) * 100 / max) + '%' })), h('div', { cls: 'jp-bar-n', text: (mine || 0).toFixed(2) + '초' })));
  }

  // ── ⑦ 스피치 ──
  JP.reg('speech', function (box) {
    JP.busy(box);
    JP.api('speech').then(function (d) {
      box.textContent = '';
      if (d.last && d.last.ok === false) box.appendChild(h('p', { cls: 'jp-warn', text: '이번 주 자동 카드: ' + d.last.why }));
      if (!d.cards.length) box.appendChild(JP.card('스피치 카드', h('p', { cls: 'jp-sub', text: '아직 카드가 없어요 (월요일 05:30 · 지미 카 공식 채널 자막)' })));
      d.cards.forEach(function (c) {
        var res = h('div');
        function show(r) {
          res.textContent = '';
          res.appendChild(h('div', { cls: 'jp-sub', text: '말 빠르기 ' + (r.wps || 0) + ' 낱말/초 (원본 ' + (r.ref_wps || 0) + ') · 쉼 ' + r.pauses.length + '번' }));
          res.appendChild(pauseBars(r.twist_pause_mine, r.ref_pause));
        }
        if (c.takes.length) show(c.takes[0].result);
        box.appendChild(JP.card(null, h('div', { cls: 'jp-quote', text: c.setup }), h('div', { cls: 'jp-quote twist', text: c.twist }),
          h('div', { cls: 'jp-sub', text: 'twist 앞 쉼 ' + (c.pause || 0) + '초 · ' + (c.wps || 0) + ' 낱말/초 ' }, JP.link('영상에서 보기', c.link)),
          recorder('speech', c.id, function (r) { show(r.result); }, 20), res));
      });
      var u = h('input', { cls: 'jp-in', placeholder: '유튜브 주소 (내가 고른 영상)' });
      box.appendChild(h('details', { cls: 'card jp-card' }, h('summary', { text: '영상 주소 붙여 넣기' }), u,
        JP.btn('카드 만들기', function () { JP.api('speech/url', { url: u.value.trim() }).then(function () { JP.rerender(); }, JP.fail); })));
      var hm = d.human;
      if (hm) {
        var said = h('textarea', { cls: 'jp-in', rows: 3, placeholder: '30초로 말한 것 (받아쓰기가 되면 자동으로 채워져요)' });
        var rv = h('div', { cls: 'jp-text' });
        var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
        var listen = SR ? JP.btn('말로 받아쓰기', function () {
          var r = new SR(); r.lang = 'ko-KR'; r.interimResults = false;
          r.onresult = function (ev) { said.value = Array.prototype.map.call(ev.results, function (x) { return x[0].transcript; }).join(' '); };
          r.start(); setTimeout(function () { try { r.stop(); } catch (e) { /* 끝남 */ } }, 32000);
        }, 'ghost') : null;
        box.appendChild(JP.card('오늘 인문학 · ' + hm.title, h('div', { cls: 'jp-text', text: hm.body }), recorder('human', hm.day, function () { JP.toast('녹음했어요'); }, 35),
          listen, said, JP.btn('봐 주기', function () { rv.textContent = '보는 중…'; JP.api('human/say', { text: said.value.trim() }).then(function (r) { rv.textContent = r.review; }, function (e) { rv.textContent = e.message; }); }), rv));
      }
    }, function (e) { JP.busy(box, e.message); });
  });

  // ── ⑧ 영어 ──
  JP.reg('eng', function (box) {
    JP.busy(box);
    Promise.all([JP.api('eng/today'), JP.api('pron/set'), JP.api('pron/history')]).then(function (all) {
      var d = all[0], ps = all[1], hist = all[2];
      box.textContent = '';
      var p = d.persona;
      box.appendChild(JP.card('오늘의 통화', p ? h('p', { text: p.name + ' · ' + p.accent + ' · ' + p.situation }) : h('p', { cls: 'jp-sub', text: '이번 주 상대는 월요일 05:30 에 만들어요 (없어도 규칙 프롬프트는 쓸 수 있어요)' }),
        h('pre', { cls: 'jp-pre', text: d.prompt }), JP.btn('프롬프트 복사', function () { JP.copy(d.prompt); }),
        h('p', { cls: 'jp-sub', text: 'Gemini 앱 Live 에 붙여 넣고 20~30분' })));
      var ex = h('input', { cls: 'jp-in', value: d.expr, placeholder: '오늘 리얼클래스 표현' });
      box.appendChild(JP.card('리얼클래스 표현', ex, JP.btn('저장', function () { JP.api('eng/expr', { text: ex.value.trim() }).then(function () { JP.toast('저장했어요 — 프롬프트에 들어가요'); JP.rerender(); }, JP.fail); })));
      var ta = h('textarea', { cls: 'jp-in', rows: 6, placeholder: '통화 뒤 대화 글 붙여 넣기 (3만 자까지)' });
      var out = h('div');
      box.appendChild(JP.card('통화 뒤 정리', ta, JP.btn('정리하기', function () {
        out.textContent = '정리하는 중…';
        JP.api('eng/after', { text: ta.value }).then(function (r) {
          out.textContent = '';
          r.fixes.forEach(function (f) {
            out.appendChild(h('div', { cls: 'jp-fix' }, h('div', { cls: 'jp-mine', text: f.mine }), h('div', { cls: 'jp-better', text: f.better }), h('div', { cls: 'jp-sub', text: f.why }),
              h('button', { type: 'button', cls: 'jp-mini', text: '자료함에', onclick: function () { JP.addToLib({ kind: 'card', title: f.better.slice(0, 60), body: f.mine + ' → ' + f.better + '\n' + f.why, src_tab: 'eng', tags: ['영어'] }); } })));
          });
          if (r.new_expr.length) out.appendChild(h('p', { text: '새 표현: ' + r.new_expr.join(' · ') }));
          if (r.frequent.length) out.appendChild(h('p', { text: '자주 틀림: ' + r.frequent.join(' · ') }));
          out.appendChild(h('p', { cls: 'jp-sub', text: '오늘 표현 ' + (r.expr_used ? '썼어요' : '안 썼어요') }));
        }, function (e) { out.textContent = e.message; });
      }), out));
      // 발음
      var pbox = JP.card('발음 (5~10문장)');
      if (!ps.ready) pbox.appendChild(h('p', { cls: 'jp-sub', text: '발음 채점 준비 중 (모델을 아직 받지 않았어요)' }));
      if (ps.weak.length) pbox.appendChild(h('p', { cls: 'jp-sub', text: '약한 소리: ' + ps.weak.join(' · ') }));
      ps.sentences.forEach(function (s) {
        var res = h('div', { cls: 'jp-sub' });
        function poll(id, n) {
          JP.api('pron/take/' + id).then(function (t) {
            if (t.state === 'done') {
              res.textContent = '';
              res.appendChild(h('b', { text: '점수 ' + t.score }));
              var g = (t.detail && t.detail.groups) || {};
              res.appendChild(h('div', { text: Object.keys(g).map(function (k) { return k + ' ' + g[k]; }).join(' · ') }));
              if (t.heard) res.appendChild(h('div', { text: '이렇게 들렸어요: ' + t.heard }));
              if (t.detail && t.detail.stress_ok === false) res.appendChild(h('div', { text: '강세(참고): 강세 모음을 조금 더 길게' }));
            } else if (t.state === 'error') res.textContent = t.heard || '채점하지 못했어요';
            else { res.textContent = t.state === 'queued' ? '채점 기다림 (서버 메모리가 넉넉할 때)' : '채점 중…'; if (n < 90) setTimeout(function () { poll(id, n + 1); }, 2000); }
          }, function (e) { res.textContent = e.message; });
        }
        pbox.appendChild(h('div', { cls: 'jp-sent' }, h('div', { text: s.text }), ps.ready ? recorder('pron', s.id, function (d) { poll(d.take_id, 0); }, 15) : null, res));
      });
      box.appendChild(pbox);
      box.appendChild(JP.card('발음 점수 기록', chart(hist.days)));
    }, function (e) { JP.busy(box, e.message); });
  });

  function chart(days) {
    if (!days || days.length < 2) return h('p', { cls: 'jp-sub', text: '기록이 쌓이면 선이 보여요' });
    var W = 320, H = 120, P = 24, n = days.length;
    var pts = days.map(function (d, i) { return [P + i * (W - 2 * P) / (n - 1), H - P - (d.s || 0) * (H - 2 * P) / 100]; });
    var ns = 'http://www.w3.org/2000/svg';
    var svg = document.createElementNS(ns, 'svg'); svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H); svg.setAttribute('class', 'jp-chart'); svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', '날짜별 평균 발음 점수');
    var base = document.createElementNS(ns, 'line'); base.setAttribute('x1', P); base.setAttribute('x2', W - P); base.setAttribute('y1', H - P); base.setAttribute('y2', H - P); base.setAttribute('class', 'ax');
    svg.appendChild(base);
    var line = document.createElementNS(ns, 'polyline'); line.setAttribute('points', pts.map(function (p) { return p.join(','); }).join(' ')); line.setAttribute('class', 'ln');
    svg.appendChild(line);
    var last = days[n - 1], lt = document.createElementNS(ns, 'text');
    lt.setAttribute('x', pts[n - 1][0]); lt.setAttribute('y', pts[n - 1][1] - 6); lt.setAttribute('text-anchor', 'end'); lt.textContent = Math.round(last.s || 0);
    svg.appendChild(lt);
    [days[0], last].forEach(function (d, i) { var t = document.createElementNS(ns, 'text'); t.setAttribute('x', i ? W - P : P); t.setAttribute('y', H - 6); t.setAttribute('text-anchor', i ? 'end' : 'start'); t.setAttribute('class', 'lb'); t.textContent = d.d.slice(5); svg.appendChild(t); });
    return svg;
  }
})();

/* X 도우미 책갈피 (주인 11/14 20:3x 'X도 책갈피처럼 효율 올릴 방법'). 주인 브라우저(사파리·크롬)의 x.com 에서 누르면 지금 보이는 화면만 읽어 대시보드 X 도구를 새 탭으로 연다:
   글 화면(/아이디/status/숫자) → 글·글쓴이·인증 표시·숫자 → 답글 초안 (#/x/reply) · 수익 조건·크리에이터 스튜디오 화면 → '30 / 500', '0 / 50만' 꼴 숫자 → 기록 (#/x/stats)
   · 프로필 → 자주 보는 계정에 넣기 (#/x/target). x.com 은 다른 곳으로 보내기(fetch·폼)를 막아(CSP) 값은 대시보드 주소(디딤돌 /dashboard/x)의 # 뒤에 싣는다.
   X 에서는 아무것도 누르지 않는다 (읽기 + 새 탭 열기만 · 바깥 요청 없음). 무엇을 읽었는지 확인 창에 먼저 보여 준다.
   xstudio.py 가 주석 줄·들여쓰기를 빼고 @@URL@@·@@KEY@@ 를 넣어 javascript: 주소로 만든다 — 줄마다 문장이 끝나게 (// 주석은 줄 맨 앞에만 · 정규식 안에 공백·따옴표 없음). */
(() => {
const D = document, L = location, B = '@@URL@@', K = '@@KEY@@', E = encodeURIComponent;
if (!/(^|\.)(x|twitter)\.com$/.test(L.hostname)) { alert('X 도우미: x.com 화면에서 눌러 주세요'); return; }
const tx = n => {
  if (!n) return '';
  if (n.nodeType === 3) return n.nodeValue || '';
  if (n.nodeName === 'IMG') return n.alt || '';
  if (n.nodeName === 'BR') return '\n';
  let s = '';
  for (const c of n.childNodes || []) s += tx(c);
  return s;
};
const one = (r, qs) => {
  for (const q of qs) { const e = r.querySelector(q); if (e) return e; }
  return null;
};
// 1,234 · 1.2만 · 3K · 4.5M · 2천 → 숫자 (못 읽으면 null)
const U = {'만': 1e4, '천': 1e3, '억': 1e8, k: 1e3, m: 1e6, b: 1e9};
const num = s => {
  const m = /([0-9][0-9.,]*)\s*(만|천|억|[KkMmBb](?![A-Za-z]))?/.exec(String(s || ''));
  if (!m) return null;
  const v = parseFloat(m[1].replace(/,/g, '')) * (m[2] ? U[m[2].toLowerCase()] || 1 : 1);
  return isFinite(v) ? Math.round(v) : null;
};
const open_ = (a, o, msg) => {
  let q = '';
  for (const k in o) if (o[k] != null && o[k] !== '') q += (q ? '&' : '') + k + '=' + E(o[k]);
  if (!confirm('X 도우미\n\n' + msg + '\n\n확인을 누르면 대시보드가 열려요')) return;
  const u = B + 'x#/x/' + a + '?' + q;
  if (!window.open(u, '_blank')) L.href = u;
};
const path = L.pathname;
// 1) 글 하나를 연 화면
const pm = /^\/([A-Za-z0-9_]{1,15})\/status(?:es)?\/([0-9]{5,20})/.exec(path);
if (pm) {
  const arts = [...D.querySelectorAll('article[data-testid="tweet"],article')];
  const art = arts.find(x => x.querySelector('a[href*="/status/' + pm[2] + '"]')) || arts.find(x => x.getAttribute('tabindex') === '-1') || arts[0] || D;
  const te = one(art, ['[data-testid="tweetText"]', 'div[lang]']);
  const text = tx(te).replace(/\n{3,}/g, '\n\n').trim().slice(0, 1500);
  const ver = !!one(art, ['[data-testid="User-Name"] [data-testid="icon-verified"]', '[data-testid="User-Name"] svg[aria-label]']);
  const cnt = qs => { const e = one(art, qs); return e ? num(e.getAttribute('aria-label') || tx(e)) : null; };
  const c = [cnt(['[data-testid="reply"]']), cnt(['[data-testid="retweet"]', '[data-testid="unretweet"]']), cnt(['[data-testid="like"]', '[data-testid="unlike"]']), cnt(['a[href$="/analytics"]'])];
  const names = ['답글', '재게시', '마음', '조회'];
  const line = c.map((v, i) => (v == null ? '' : names[i] + ' ' + v.toLocaleString())).filter(Boolean).join(' · ');
  const msg = '@' + pm[1] + (ver ? ' (인증 계정)' : '') + ' 글 ' + (text ? text.length + '자\n「' + text.slice(0, 90) + (text.length > 90 ? '…' : '') + '」' : '— 내용은 못 읽었어요 (주소만 보내요)') + (line ? '\n' + line : '') + '\n\n→ 답글 초안 3개 (인용 글 쓰기도 그 화면에서)';
  open_('reply', {u: L.origin + '/' + pm[1] + '/status/' + pm[2], t: text, h: pm[1], v: ver ? 1 : '', c: c.some(v => v != null) ? c.map(v => (v == null ? '' : v)).join(',') : ''}, msg);
  return;
}
// 2) 수익 조건 · 크리에이터 스튜디오 · 애널리틱스 — 보이는 글에서 'A / B' 꼴과 이름표 옆 숫자
const body = String((D.body && D.body.innerText) || '').split(String.fromCharCode(160)).join(' ');
const st = {};
const pair = /([0-9][0-9.,]*\s?(?:만|천|억|[KkMm])?)\s*\/\s*([0-9][0-9.,]*\s?(?:만|천|억|[KkMm])?)/g;
let m;
while ((m = pair.exec(body))) {
  const a = num(m[1]), b = num(m[2]);
  if (a == null) continue;
  if (b === 500 && st.vf == null) st.vf = a;
  if ((b === 500000 || b === 5000000) && st.im == null) st.im = a;
}
const lines = body.split('\n').map(x => x.trim()).filter(Boolean);
const near = re => {
  for (let i = 0; i < lines.length; i++) {
    if (!re.test(lines[i])) continue;
    for (const j of [i + 1, i - 1]) if (lines[j] && /^[0-9][0-9.,]*\s?(만|천|억|[KkMm])?$/.test(lines[j])) return num(lines[j]);
  }
  return null;
};
if (st.vf != null || st.im != null) {
  st.ai = near(/^(노출수?|Impressions)$/i);
  st.ae = near(/^(참여수?|Engagements)$/i);
  const rows = [st.vf != null ? '인증 팔로워 ' + st.vf.toLocaleString() + ' / 500' : '인증 팔로워 — 못 읽음 (지난 값 그대로)', st.im != null ? '90일 인증 노출 ' + st.im.toLocaleString() + ' / 50만' : '90일 노출 — 못 읽음 (지난 값 그대로)'];
  if (st.ai != null) rows.push('화면 노출 ' + st.ai.toLocaleString());
  if (st.ae != null) rows.push('화면 참여 ' + st.ae.toLocaleString());
  open_('stats', Object.assign({k: K}, st), rows.join('\n') + '\n\n→ 오늘 진행 숫자로 기록 (숫자가 다르면 취소)');
  return;
}
// 3) 프로필
const fm = /^\/([A-Za-z0-9_]{1,15})\/?$/.exec(path);
const skip = /^(home|explore|search|notifications|messages|settings|compose|i|jobs|premium|communities|lists|bookmarks)$/i;
if (fm && !skip.test(fm[1]) && one(D, ['[data-testid="UserName"]', '[data-testid="UserProfileHeader_Items"]', '[data-testid="primaryColumn"] a[href$="/followers"]'])) {
  const fe = one(D, ['a[href$="/verified_followers"]', 'a[href$="/followers"]']);
  const fo = fe ? num(tx(fe)) : null;
  const ver = !!one(D, ['[data-testid="UserName"] [data-testid="icon-verified"]', '[data-testid="UserName"] svg[aria-label]']);
  open_('target', {k: K, h: fm[1], fo: fo, v: ver ? 1 : ''}, '@' + fm[1] + (ver ? ' (인증 계정)' : '') + (fo != null ? ' · 팔로워 ' + fo.toLocaleString() : '') + '\n\n→ 자주 보는 계정에 넣기 (내 계정이면 팔로워 수 기록)');
  return;
}
alert('X 도우미: 이 화면에서 읽을 것을 못 찾았어요.\n\n· 글 하나를 연 화면 → 답글·인용 초안\n· 수익 조건(크리에이터 스튜디오) 화면 → 진행 숫자 기록\n· 큰 계정 프로필 → 자주 보는 계정에 넣기');
})();

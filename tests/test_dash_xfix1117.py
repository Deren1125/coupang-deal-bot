"""주인 11/17 아침 신고 (dashboard/xtrend.py · xstudio.py · llm_json.py).

1) '지금 뜨는 주제'에 외국어 주제가 섞임 → 한국어 주제만 순위에 (한글 비율), 외국 화제는 한국 기사 제목으로 · 기사도 없으면 '해외' 칸.
2) '내 주제로 글 쓰기' 초안이 안 뜸 (AI 주간 한도) → 실패를 화면에 남김 (이유·풀리는 시각·다시 단추) · 몫을 돌려줌 · 한도 동안 AI 안 부름 ·
   만든 초안은 그날 kv 에 남아 새로 고쳐도 보임. CLI 한도 문구를 한도로 알아봄.
"""

from __future__ import annotations

import json
import re

import pytest

from dealbot.dashboard import xstudio as xs
from dealbot.dashboard import xtrend as xr
from dealbot.llm_json import LIMIT_TAG, LLMError, limit_text
from test_dash_xtoday import at
from test_dash_xtrend import NOW, RSS, FakeGet, desk, fx, post


def test_korean_share_and_korean_title_for_foreign_topics() -> None:
    assert xr.is_korean("수능 연기") and xr.is_korean("#엔하이픈") and xr.is_korean("BTS 진")
    assert not xr.is_korean("Chelsea") and not xr.is_korean("大谷翔平") and not xr.is_korean("#JIMIN")
    news = [{"title": "Chelsea beat United"}, {"title": "[단독] 첼시, 맨유 꺾고 선두 탈환 - 연합뉴스"}]
    assert xr.ko_title(news) == "첼시, 맨유 꺾고 선두 탈환"
    assert xr.ko_title([{"title": "손흥민·이강인 나란히 골 (종합)"}]) == "손흥민·이강인 나란히 골"
    assert xr.ko_title([{"title": "Only English"}]) == "" and xr.label({"kw": "Chelsea", "news": news}) == ("첼시, 맨유 꺾고 선두 탈환", "Chelsea")
    assert xr.label({"kw": "수능", "news": []}) == ("수능", "") and xr.label({"kw": "NBA", "news": []}) == ("", "NBA")


async def test_foreign_topics_leave_the_ranking_or_show_a_korean_title() -> None:
    rss = RSS.replace("</channel>", """<item><title>Chelsea</title><ht:approx_traffic>50,000+</ht:approx_traffic>
<ht:news_item><ht:news_item_title>첼시, 맨유 꺾고 선두 탈환 - 가나일보</ht:news_item_title><ht:news_item_url>https://news.example/c1</ht:news_item_url></ht:news_item></item>
<item><title>Dodgers</title><ht:approx_traffic>90,000+</ht:approx_traffic>
<ht:news_item><ht:news_item_title>Dodgers win again</ht:news_item_title><ht:news_item_url>https://news.example/d1</ht:news_item_url></ht:news_item></item>
</channel>""")
    sample = fx(post(1, "aaa", "#JIMIN birthday", views=900000), post(2, "bbb", "happy #JIMIN day", views=800000), post(3, "ccc", "#JIMIN 생일", views=5))
    d, _g, _a, _n, _t = desk(get=FakeGet(rss=(200, rss), sample=(200, sample)))
    await d.refresh()
    v = d.view()
    kws = [r["kw"] for r in v["rows"]]
    assert "Dodgers" not in kws and "Chelsea" not in kws and "#JIMIN" not in kws  # 외국어 이름은 순위에 안 보임
    row = next(r for r in v["rows"] if r["orig"] == "Chelsea")
    assert row["kw"] == "첼시, 맨유 꺾고 선두 탈환" and [r["rank"] for r in v["rows"]] == list(range(1, len(v["rows"]) + 1))
    assert {a["kw"] for a in v["abroad"]} >= {"Dodgers", "#JIMIN"} and all(a["x"].startswith("https://x.com/search?q=") for a in v["abroad"])
    assert all(r["orig"] == "" for r in v["rows"] if r["kw"] in ("수능 연기", "용인반도체클러스터"))
    st = d.state(NOW)
    assert all(r.get("rank") == 0 for r in st["rows"] if r["kw"] in ("Dodgers",))  # 순위 기록(오른 정도)에도 안 들어감
    assert "Dodgers" not in json.dumps(st["hist"][-1]["rank"]) and xr.row_id("Dodgers") not in st["hist"][-1]["rank"]
    # 한국 기사 제목으로 바뀐 주제도 그대로 글 쓰기 (참고 = 그 기사)
    out = await d.draft({"topic": row["kw"], "trend": row["id"], "memo": ""})
    assert out["mine"][0]["topic"] == row["kw"] and "기사 제목: 첼시" in d.studio._ask_fn.calls[-1][1]


class LimitAsk:
    def __init__(self, text: str = "You've hit your weekly limit · resets 9pm (Asia/Seoul)") -> None:
        self.calls = 0
        self.text = text

    async def __call__(self, system: str, user: str, schema: dict) -> dict:
        self.calls += 1
        raise xs.ai_error(LLMError(f"{LIMIT_TAG} ({limit_text(self.text)})"), NOW)


async def test_ai_limit_is_shown_with_reset_time_kept_and_retried_only_after_reset() -> None:
    ask = LimitAsk()
    d, _g, _a, now, _t = desk(ask=ask)
    with pytest.raises(xs.StudioError) as e:
        await d.draft({"topic": "수능 연기 논란", "memo": "손해"})
    assert (e.value.status, e.value.code) == (429, "ai_limit") and "21:00 이후에 다시" in e.value.text
    assert e.value.body()["until"] == int(at("21:00", "2026-11-16"))
    v = d.view()
    assert v["left"] == xs.DRAFT_PER_DAY  # AI 가 못 답한 한 번은 몫을 돌려줌
    assert v["fail"]["code"] == "ai_limit" and v["fail"]["topic"] == "수능 연기 논란" and "21:00" in v["fail"]["text"] and v["fail"]["wait_s"] == 7 * 3600
    assert v["run"] is None and v["mine"] == []
    # 새로 고쳐도 (다른 기기·새 화면) 실패가 남고, 한도가 풀리기 전엔 AI 를 다시 부르지 않음
    with pytest.raises(xs.StudioError) as e:
        await d.draft({"topic": "다른 주제", "memo": ""})
    assert e.value.code == "ai_limit" and ask.calls == 1
    now[0] = at("21:01", "2026-11-16")
    d.studio._ask_fn = desk()[2]  # 한도가 풀림 → 부름
    out = await d.draft({"topic": "수능 연기 논란", "memo": "손해"})
    assert out["mine"][0]["topic"] == "수능 연기 논란" and out["fail"] is None  # 초안이 생기면 실패 칸은 사라짐
    assert d.view()["mine"][0]["id"] == out["made"]  # 그날 kv — 새로 고쳐도 보임


def test_cli_limit_words_and_reset_times() -> None:
    now = at("14:00", "2026-11-16")
    for msg, want in (("resets 9pm (Asia/Seoul)", at("21:00", "2026-11-16")), ("resets 9:30am", at("09:30", "2026-11-17")),
                      ("resets Nov 17, 9pm (Asia/Seoul)", at("21:00", "2026-11-17")), ("resets at 21:10", at("21:10", "2026-11-16")),
                      ("resets in 2h 30m", now + 9000), ("no time here", 0.0)):
        assert xs.limit_until(msg, now) == want, msg
    assert limit_text("Claude AI usage limit reached · resets 9pm (Asia/Seoul)") == "resets 9pm (Asia/Seoul)"
    err = xs.ai_error(LLMError("claude CLI 한도 (풀리는 때 모름)"), now)
    assert err.code == "ai_limit" and "한도가 풀린 뒤" in err.text
    assert xs.ai_error(LLMError("claude CLI 시간 초과 (90초)"), now).code == "ai_timeout"
    late = xs.limit_error(at("09:00", "2026-11-17"), now)
    assert "내일 09:00 이후" in late.text


async def test_cli_json_limit_payload_becomes_limit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from dealbot import llm_json

    class Proc:
        returncode = 1

        def __init__(self, out: bytes) -> None:
            self.out = out

        async def communicate(self, _inp: bytes) -> tuple[bytes, bytes]:
            return self.out, b""

        async def wait(self) -> int:
            return 1

    outs = [json.dumps({"type": "result", "is_error": True, "result": "You've hit your weekly limit · resets 9pm (Asia/Seoul)"}).encode(),
            b"Claude AI usage limit reached|1794920400"]

    async def spawn(*a, **k):  # noqa: ANN002, ANN003
        return Proc(outs.pop(0))

    monkeypatch.setattr(llm_json.asyncio, "create_subprocess_exec", spawn)
    asker = llm_json.JsonAsker(None, cli_bin="/bin/true", cli_model="opus")
    for want in ("resets 9pm", "풀리는 때 모름"):
        with pytest.raises(LLMError) as e:
            await asker.ask("s", "u", {"type": "object"})
        assert str(e.value).startswith(LIMIT_TAG) and want in str(e.value)


# ---------------------------------------------------------------- 화면 (node)
import subprocess  # noqa: E402

from test_dash_xtoday import NODE, STATIC, needs_node  # noqa: E402
from test_dash_xtrend import SHIM  # noqa: E402

XR_TAIL = r"""
(async () => {
  const out = {};
  eval(fs.readFileSync(src, 'utf8'));
  await tick();
  const scr = () => main.children[0];
  out.text = text(scr());
  out.alert = text(byId(main, 'xrfail') || mk('x'));
  click(find(scr(), n => n.tagName === 'BUTTON' && text(n).startsWith('해외 화제')));
  out.abroad = text(scr()).includes('Dodgers');
  const retry = find(byId(main, 'xrfail'), n => n.tagName === 'BUTTON' && text(n) === '다시 해 보기');
  out.retryOff = 'disabled' in retry.attrs;
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


@needs_node
async def test_js_shows_failure_with_reason_abroad_fold_and_my_drafts_header() -> None:
    d, _g, _a, _n, _t = desk()
    v = d.view()
    v.update(fail={"at": "13:50", "code": "ai_limit", "text": "AI 사용량 한도가 다 찼어요 — 21:00 이후에 다시 눌러 주세요", "topic": "수능 연기 논란", "wait_s": 25200},
             abroad=[{"kw": "Dodgers", "views": 0, "traffic": 90000, "x": "https://x.com/search?q=Dodgers"}],
             rows=[{"id": "abcdef12", "kw": "첼시, 맨유 꺾고 선두 탈환", "orig": "Chelsea", "rank": 1, "move": None, "src": ["google"], "seen": False, "n": 0, "views": 0,
                    "replies": 0, "likes": 0, "traffic": 50000, "since": "", "x": "https://x.com/search?q=Chelsea", "posts": [], "news": []}])
    shim = SHIM.split("(async () => {")[0] + XR_TAIL
    res = subprocess.run([NODE, "-e", shim, str(STATIC / "xtrend.js"), "x", json.dumps(v, ensure_ascii=False), "#/sns/xr/w"], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert "✗ 13:50 · 「수능 연기 논란」 초안을 못 썼어요 — AI 사용량 한도가 다 찼어요 — 21:00 이후에 다시 눌러 주세요 (420분 남음)" in out["alert"]
    assert out["retryOff"] is True  # 한도가 풀리기 전엔 다시 단추가 꺼져 있음
    assert "내 초안 · 오늘 0묶음" in out["text"] and "만든 초안은 여기 남아요" in out["text"]
    assert "해외 화제 「Chelsea」" in out["text"] and "Dodgers" not in out["text"].split("해외 화제 1개")[0] and out["abroad"] is True


KW_SHIM = r"""
const [src, runsJson, hash] = process.argv.slice(1);
const fs = require('fs');
function mk(tag) {
  const n = {nodeType: 1, tagName: tag.toUpperCase(), children: [], attrs: {}, parentNode: null, _text: '',
    get className() { return this.attrs.class || ''; }, set className(v) { this.attrs.class = v; },
    get classList() { const s = this; return {contains: c => s.className.split(' ').includes(c)}; },
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    append(...xs) { for (const x of xs) { const c = typeof x === 'string' ? Object.assign(mk('#text'), {_text: x}) : x; c.parentNode = this; this.children.push(c); } },
    after(x) { if (x.parentNode) x.remove(); const p = this.parentNode; x.parentNode = p; p.children.splice(p.children.indexOf(this) + 1, 0, x); },
    remove() { const p = this.parentNode; if (p) { p.children.splice(p.children.indexOf(this), 1); this.parentNode = null; } },
    replaceWith(x) { this.after(x); this.remove(); },
    get nextElementSibling() { const p = this.parentNode; return p ? p.children[p.children.indexOf(this) + 1] || null : null; },
    set textContent(v) { this._text = String(v); this.children = []; }, get textContent() { return this._text + this.children.map(c => c.textContent).join(''); },
    querySelectorAll(sel) { return qsa(this, sel); }, querySelector(sel) { return qsa(this, sel)[0] || null; }};
  return n;
}
const cls = (n, c) => n.className.split(' ').includes(c);
const simple = (n, s) => s.split('.').every((p, i) => (i === 0 ? !p || n.tagName === p.toUpperCase() : cls(n, p)));
function qsa(root, sel) {
  const parts = sel.trim().split(/\s+/), out = [];
  const walk = (n, anc) => { for (const c of n.children) { if (c.tagName === '#TEXT') continue;
    const ok = simple(c, parts[parts.length - 1]) && (parts.length === 1 || anc.some(a => simple(a, parts[0])));
    if (ok) out.push(c); walk(c, anc.concat([c])); } };
  walk(root, []); return out;
}
const main = mk('main'), head = mk('head');
const row = (kw) => { const r = mk('div'); r.className = 'row tr'; const b = mk('button'); b.className = 'grow tw'; const t = mk('span'); t.className = 't'; t.textContent = kw; b.append(t); r.append(b); return r; };
const box = mk('div'); box.className = 'box'; box.append(row('양의지'), row('두산 베어스'), row('없는 키워드'));
const rz = mk('div'); rz.className = 'bn rz'; for (const k of ['카리나', '윈터']) { const b = mk('button'); b.textContent = k; rz.append(b); }
main.append(box, rz);
global.document = {readyState: 'complete', head, visibilityState: 'visible', createElement: mk, addEventListener() {}, getElementById: id => (id === 'main' ? main : null)};
global.location = {hash};
global.window = global; global.addEventListener = () => {}; global.requestAnimationFrame = f => { f(); return 1; };
global.MutationObserver = class { observe() {} };
global.setTimeout = () => 0; global.clearTimeout = () => {};
const calls = [];
global.fetch = async u => { calls.push(u); return {ok: true, json: async () => ({runs: JSON.parse(runsJson)})}; };
(async () => {
  eval(fs.readFileSync(src, 'utf8'));
  for (let i = 0; i < 6; i++) await new Promise(r => setImmediate(r));
  window.SBKwRun.paint(); window.SBKwRun.paint();  // 여러 번 그려도 한 줄
  const lines = qsa(main, 'p.kws').map(n => [n.className, n.textContent, (n.children.find(c => c.tagName === 'A') || {attrs: {}}).attrs.href || '']);
  console.log(JSON.stringify({lines, calls, order: box.children.map(c => c.className)}));
})().catch(e => { console.error(e); process.exit(1); });
"""


class FakeStore:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def recent(self, limit: int = 20) -> list[dict]:
        return sorted(self.rows, key=lambda r: -r["ts"])[:limit]


def arow(ts: float, action: str, kw: str, status: str, code: str = "", text: str = "", blog: str | None = None, post: dict | None = None) -> dict:
    res = {"code": code, "text": text, **({"post": post} if post else {})}
    args = {"keyword": kw, **({"blog": blog} if blog else {})}
    return {"id": f"a_{int(ts)}", "ts": ts, "action": action, "blog": blog or ("issue" if action == "blog.issue" else None), "args": json.dumps(args, ensure_ascii=False),
            "status": status, "result": json.dumps(res, ensure_ascii=False), "target": ""}


def test_keyword_runs_show_the_real_reason_latest_per_keyword_today_only() -> None:
    from dealbot.dashboard import kwrun

    now = at("09:00", "2026-11-17")
    rows = [arow(at("08:03", "2026-11-17"), "blog.channel_topic", "양의지", "rejected", "not_found", "「양의지」 기사로는 규칙에 맞는 이야기를 못 찾았어요 (도박·사건·경기 전 글 빼고)", "hotdeal"),
            arow(at("08:03", "2026-11-17"), "blog.issue", "카리나", "error", "failed", "AI 사용량이 다 차서 08:13 이후에 다시 눌러 주세요"),
            arow(at("08:13", "2026-11-17"), "blog.issue", "카리나", "error", "ai_limit", "AI 사용량이 다 차서 08:23 이후에 다시 눌러 주세요 (Claude 한도는 21:00 에 풀려요)"),
            arow(at("08:20", "2026-11-17"), "blog.issue", "윈터", "running", "", "요청 받았어요"),
            arow(at("08:30", "2026-11-17"), "blog.channel_topic", "두산 베어스", "ok", "done", "「두산 베어스」 글을 오늘 18:00 빈 자리에 넣었어요 — 할 일 붙여넣기에 떠요", "hotdeal"),
            arow(at("08:40", "2026-11-17"), "blog.issue", "장원영", "ok", "done", "새 글을 임시저장했어요", post={"blog": "issue", "pid": 412, "status": "saved"}),
            arow(at("08:41", "2026-11-17"), "blog.issue", "안유진", "unknown", "", ""),
            arow(at("23:00", "2026-11-16"), "blog.issue", "어제", "ok", "done", "썼어요"),
            arow(at("08:50", "2026-11-17"), "blog.pick", "x", "ok", "done", "")]
    got = {r["kw"]: r for r in kwrun.runs(FakeStore(rows), now)}
    assert set(got) == {"양의지", "카리나", "윈터", "두산 베어스", "장원영", "안유진"}  # 오늘 것 · 키워드 일만
    assert got["카리나"]["state"] == "fail" and "Claude 한도는 21:00" in got["카리나"]["text"]  # 마지막 것
    assert got["양의지"]["state"] == "fail" and "규칙에 맞는 이야기를 못 찾았어요" in got["양의지"]["text"]  # 'not_found 뜻말' 아님
    assert got["윈터"] == {**got["윈터"], "state": "run", "text": "쓰는 중이에요 (몇 분 걸려요)"}
    assert got["두산 베어스"]["go"] == "#/todo" and got["장원영"]["go"] == "#/i/post-issue-412" and got["안유진"]["state"] == "fail"
    assert "20분 넘게" in got["안유진"]["text"]


@needs_node
def test_kwrun_js_puts_one_result_line_beside_each_keyword_button() -> None:
    from dealbot.dashboard import kwrun

    now = at("09:00", "2026-11-17")
    rows = [arow(at("08:03", "2026-11-17"), "blog.channel_topic", "양의지", "rejected", "not_found", "기사로는 규칙에 맞는 이야기를 못 찾았어요", "hotdeal"),
            arow(at("08:30", "2026-11-17"), "blog.channel_topic", "두산 베어스", "ok", "done", "글을 오늘 18:00 빈 자리에 넣었어요", "hotdeal"),
            arow(at("08:13", "2026-11-17"), "blog.issue", "카리나", "error", "ai_limit", "AI 사용량이 다 차서 08:23 이후에 다시 눌러 주세요 (Claude 한도는 21:00 에 풀려요)"),
            arow(at("08:20", "2026-11-17"), "blog.channel_topic", "양의지", "ok", "done", "다른 블로그", "finance")]
    runs = [{k: v for k, v in r.items() if k != "ts"} for r in kwrun.runs(FakeStore(rows), now)]
    js = re.sub(r"/\*.*?\*/", "", (STATIC / "kwrun.js").read_text(encoding="utf-8"), flags=re.S)
    assert "innerHTML" not in js and ".click()" not in js and "http" not in js.replace("/dashboard/api/kwruns", "")
    res = subprocess.run([NODE, "-e", KW_SHIM, str(STATIC / "kwrun.js"), json.dumps(runs, ensure_ascii=False), "#/blog/hotdeal"], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["calls"] == ["/dashboard/api/kwruns"] and out["order"] == ["row tr", "kws fail", "row tr", "kws ok", "row tr"]
    assert out["lines"][0] == ["kws fail", "✗ 08:03 「양의지」 기사로는 규칙에 맞는 이야기를 못 찾았어요", ""]  # 다른 블로그(finance) 결과는 안 붙음
    assert out["lines"][1] == ["kws ok", "✓ 08:30 「두산 베어스」 글을 오늘 18:00 빈 자리에 넣었어요보러 가기 ›", "#/todo"]
    assert out["lines"][2][0] == "kws fail" and "「카리나」 AI 사용량이 다 차서" in out["lines"][2][1] and "21:00" in out["lines"][2][1]
    assert len(out["lines"]) == 3  # 막 뜨는 중 칸엔 카리나만 · 여러 번 그려도 한 줄



"""대시보드 '오늘 할 일' (주인 10/27 — 블로그 반자동: 발행·예약·이웃·SNS 를 주인이 하루 5번 10~15분씩 손으로. "깔끔하고 부담 없이").

1) 기록 파일 status.routine 을 엄격히 거름 (blogauto._routine): 틀린 id·http 주소·없는 시간대는 빠지고, 글자는 잘리고, 분은 1~15.
2) routine.view: 시간대마다 줄·체크 수·남은 분 · 지금 시간대(대시보드 시간대로 지난 마지막 칸, 09:00 전이면 첫 칸) · 두 번째 출처 자리.
3) routine.done {id, done}: 봇 DB 에 그 목록 날짜별로 · 다른 날 목록엔 안 묻어남 · 틀린 args 는 bad_args.
4) GET /dashboard/api/routine (로그인 아래 · no-store) · routine.js (셸이 defer · 크기 예산은 test_dash_shell · 바깥 주소·innerHTML 없음).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dash_helpers import fixture, get_json, post_json, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import routine
from dealbot.dashboard.blogauto import _routine, parse_export
from test_dash_state import write_export

STATIC = Path(__file__).resolve().parents[1] / "src" / "dealbot" / "dashboard" / "static"
SLOTS = [{"key": "s1", "at": "09:00", "label": "아침"}, {"key": "s2", "at": "12:30", "label": "점심"},
         {"key": "s3", "at": "15:00", "label": "오후"}, {"key": "s4", "at": "18:00", "label": "저녁"},
         {"key": "s5", "at": "21:00", "label": "밤"}]


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def item(i: str, slot: str = "s1", **kw: object) -> dict:
    return {"id": i, "slot": slot, "group": "이웃", "blog": "finance", "title": f"할 일 {i}", "note": "", "min": 3, "copy": [],
            "links": [], **kw}


def tz_now(bot: DealBot) -> datetime:
    return datetime.now(ZoneInfo(bot.settings.app.timezone))


def at(bot: DealBot, day: str, hm: str) -> float:
    return datetime.fromisoformat(f"{day}T{hm}:00").replace(tzinfo=ZoneInfo(bot.settings.app.timezone)).timestamp()


def with_routine(tmp_path: Path, rt: object) -> None:
    st = fixture("export_v2.json")["status"]
    write_export(tmp_path / "blogauto.json", status={**st, "routine": rt})


def sample(day: str) -> dict:
    return {"day": day, "slots": SLOTS, "overflow": 2, "items": [
        item("pub:finance:1", "s1", group="발행", title="아카이브 글 발행", min=5,
             copy=[{"label": "제목 복사", "text": "제목 한 줄\n둘째 줄"}], links=[{"label": "글쓰기", "url": "https://blog.naver.com/x"}]),
        item("nb:issue:a", "s1", blog="issue", min=4),
        item("sns:x:1", "s3", group="SNS", blog="", min=2),
    ]}


@pytest.fixture(autouse=True)
def _no_live_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    """서버에서 돌려도 실제 threads-auto(127.0.0.1:8710) 목록이 섞이지 않게"""
    from dealbot.dashboard import threads_proxy

    def off(*a: object) -> None:
        raise ConnectionError("test")
    monkeypatch.setattr(threads_proxy, "_call", off)


# ---------------------------------------------------------------- 1) 거름
def test_routine_parser_drops_bad_rows_and_clips_text() -> None:
    assert _routine(None) is None and _routine({"day": "2026-10-27"}) is None  # 예전 Blog-Auto (칸 없음)
    assert _routine({"day": "2026-13-40", "items": []}) is None and _routine({"day": "어제", "items": []}) is None
    long = "가" * 500
    got = _routine({"day": "2026-10-27", "slots": SLOTS[:4] + [{"key": "s9", "at": "23:00"}, {"key": "s1", "at": "07:00"}],
                    "overflow": -3, "items": [
                        item("ok-1", title=long, note=long, min=99,
                             copy=[{"label": long, "text": "줄1\n" + "나다 " * 600}, {"label": "빈 글", "text": "  "}, "nope"] + [
                                 {"label": f"c{i}", "text": "t"} for i in range(6)],
                             links=[{"label": "나쁨", "url": "http://evil.example/"}, {"label": "자바", "url": "javascript:alert(1)"},
                                    {"label": "좋음", "url": "https://m.blog.naver.com/a"}, {"label": "따옴", "url": 'https://a.b/"x'}]),
                        item("BAD ID"), item("x" * 81), item("ok-1", title="같은 id 두 번째"), item("s5-row", "s5"),
                        item("no-title", title="  "), item("bad-group", group="쇼핑"), item("evil-blog", blog="../x", min=0),
                        item("ok-2", min=True), {"id": "ok-3"}, "줄 아님"]})
    assert got is not None and got["day"] == "2026-10-27" and got["overflow"] == 0
    assert [s["key"] for s in got["slots"]] == ["s1", "s2", "s3", "s4"] and got["slots"][0]["at"] == "09:00"  # s9·두 번째 s1 은 뺌
    ids = [i["id"] for i in got["items"]]
    assert ids == ["ok-1", "evil-blog", "ok-2"]  # s5 칸이 없으니 s5-row 도 뺌
    one = got["items"][0]
    assert len(one["title"]) == 80 and one["title"].endswith("…") and len(one["note"]) == 160 and one["min"] == 15
    assert len(one["copy"]) == 2 and len(one["copy"][0]["label"]) == 20 and one["copy"][1] == {"label": "c0", "text": "t"}  # 앞 4개만 · 빈 글·줄 아님은 뺌
    assert one["copy"][0]["text"].startswith("줄1\n") and len(one["copy"][0]["text"]) <= 1000
    assert one["links"] == [{"label": "좋음", "url": "https://m.blog.naver.com/a"}]
    assert got["items"][1]["blog"] == "" and got["items"][1]["min"] == 1 and got["items"][2]["min"] == 5
    # 시간대가 하나도 맞지 않으면 기본 5칸 (아침·점심·오후·저녁·밤)
    d = _routine({"day": "2026-10-27", "slots": "x", "items": [item("a", "s5")]})
    assert [s["label"] for s in d["slots"]] == ["아침", "점심", "오후", "저녁", "밤"] and d["items"][0]["slot"] == "s5"


def test_parse_export_puts_routine_next_to_naver_visits(tmp_path: Path) -> None:
    data = fixture("export_v2.json")
    assert parse_export(data, "Asia/Seoul").v2["status"]["routine"] is None
    data["status"]["routine"] = sample("2026-10-27")
    got = parse_export(data, "Asia/Seoul").v2["status"]["routine"]
    assert [i["id"] for i in got["items"]] == ["pub:finance:1", "nb:issue:a", "sns:x:1"] and got["overflow"] == 2


# ---------------------------------------------------------------- 2) 보기
def test_view_groups_by_slot_marks_current_and_counts(bot: DealBot, tmp_path: Path) -> None:
    assert routine.view(bot) == {"day": None, "today": False, "slots": [], "overflow": 0, "done": 0, "total": 0,
                                 "ably": None, "rising": [], "rests": []}  # 목록 없음 → 칸 숨김 (에이블리 링크 부탁도 없음 — test_dash_ably_requests)
    day = tz_now(bot).date().isoformat()
    with_routine(tmp_path, sample(day))
    v = routine.view(bot, now=at(bot, day, "13:10"))
    assert v["day"] == day and v["today"] is True and v["total"] == 3 and v["done"] == 0 and v["overflow"] == 2
    assert [s["key"] for s in v["slots"]] == ["s1", "s2", "s3", "s4", "s5"]
    assert [s["key"] for s in v["slots"] if s["current"]] == ["s2"]  # 12:30 이 지난 마지막 칸
    s1 = v["slots"][0]
    assert s1["total"] == 2 and s1["min_left"] == 9 and s1["items"][0]["tag"] == "발행 · 아카이브"
    assert s1["items"][1]["tag"] == "이웃 · 데런픽" and v["slots"][2]["items"][0]["tag"] == "SNS"
    assert s1["items"][0]["copy"][0]["text"] == "제목 한 줄\n둘째 줄"
    assert [s["key"] for s in routine.view(bot, now=at(bot, day, "07:00"))["slots"] if s["current"]] == ["s1"]  # 09:00 전 → 첫 칸
    assert [s["key"] for s in routine.view(bot, now=at(bot, day, "21:00"))["slots"] if s["current"]] == ["s5"]
    routine.set_done(bot.db, day, "nb:issue:a", True)
    v = routine.view(bot, now=at(bot, day, "09:30"))
    s1 = v["slots"][0]
    assert v["done"] == 1 and s1["done"] == 1 and s1["min_left"] == 5 and s1["items"][1]["done"] is True
    tomorrow = (tz_now(bot) + timedelta(days=1)).date().isoformat()
    assert routine.view(bot, now=at(bot, tomorrow, "10:00"))["today"] is False  # 어제 목록이면 표시


def test_second_source_merges_by_one_function(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """스레드 세션 몫(threads-auto /api/routine) — 꺼져 있으면 [] · 오늘 것만 · 같은 모양으로 걸러 합침."""
    from dealbot.dashboard import threads_proxy

    def off(*a: object) -> None:
        raise ConnectionError("no key")
    monkeypatch.setattr(threads_proxy, "_call", off)
    assert routine.threads_items(bot) == [] and routine.threads_items in routine.EXTRA_SOURCES
    today_ = tz_now(bot).date().isoformat()
    row = {"id": "th:x:thback:0", "slot": "s1", "group": "SNS", "title": "스레드 맞팔 1", "copy": [], "links": []}
    monkeypatch.setattr(threads_proxy, "_call", lambda *a: (200, json.dumps({"day": today_, "items": [row]}).encode(), "application/json"))
    assert routine.threads_items(bot) == [row]
    monkeypatch.setattr(threads_proxy, "_call", lambda *a: (200, json.dumps({"day": "2000-01-01", "items": [row]}).encode(), ""))
    assert routine.threads_items(bot) == []  # 어제 목록은 안 씀
    day = tz_now(bot).date().isoformat()
    with_routine(tmp_path, sample(day))

    def extra(_bot: object) -> list[dict]:
        return [item("th:1", "s4", group="SNS", blog=""), item("nb:issue:a", "s4"), item("BAD"), item("th:2", "s9")]

    def boom(_bot: object) -> list[dict]:
        raise RuntimeError("threads-auto 꺼짐")

    monkeypatch.setattr(routine, "EXTRA_SOURCES", (extra, boom))
    v = routine.view(bot, now=at(bot, day, "18:30"))
    assert v["total"] == 4 and [i["id"] for i in v["slots"][3]["items"]] == ["th:1"]  # 같은 id·틀린 줄·없는 칸은 뺌, 터진 출처는 건너뜀
    write_export(tmp_path / "blogauto.json")  # 블로그 목록이 없어도 다른 출처 줄은 보임 (오늘 날짜 · 기본 5칸)
    v = routine.view(bot)
    assert v["day"] == tz_now(bot).date().isoformat() and v["total"] == 2 and v["slots"][3]["total"] == 2


# ---------------------------------------------------------------- 3) 체크
async def test_routine_done_action_toggles_and_keeps_days_apart(bot: DealBot, tmp_path: Path) -> None:
    day = tz_now(bot).date().isoformat()
    with_routine(tmp_path, sample(day))
    h = session_headers(bot)
    status, got, _ = await post_json(bot, "/dashboard/api/action", {"action": "routine.done", "args": {"id": "nb:issue:a", "done": True}}, h)
    assert status == 200 and got["status"] == "ok" and got["code"] == "done" and got["text"] == "체크했어요"
    assert json.loads(bot.db.kv_get(f"routine_done:{day}")) == ["nb:issue:a"]
    status, v, headers = await get_json(bot, "/dashboard/api/routine", h)
    assert status == 200 and headers["Cache-Control"] == "no-store" and v["done"] == 1 and v["total"] == 3
    status, got, _ = await post_json(bot, "/dashboard/api/action", {"action": "routine.done", "args": {"id": "nb:issue:a", "done": False}}, h)
    assert status == 200 and got["text"] == "체크를 풀었어요" and json.loads(bot.db.kv_get(f"routine_done:{day}")) == []
    for bad in ({"id": "BAD ID", "done": True}, {"id": "a", "done": "yes"}, {"id": 3, "done": True}, {"done": True}, {"id": "x" * 81, "done": True}):
        status, got, _ = await post_json(bot, "/dashboard/api/action", {"action": "routine.done", "args": bad}, h)
        assert status == 400 and got["code"] == "bad_args", bad
    # 다른 날 목록: 어제 체크는 안 묻어나고, 이틀 넘은 기록은 지움
    old = (tz_now(bot) - timedelta(days=3)).date().isoformat()
    bot.db.kv_set(f"routine_done:{old}", json.dumps(["nb:issue:a"]))
    routine.set_done(bot.db, day, "sns:x:1", True)
    assert bot.db.kv_get(f"routine_done:{old}") is None
    tomorrow = (tz_now(bot) + timedelta(days=1)).date().isoformat()
    with_routine(tmp_path, sample(tomorrow))
    assert routine.view(bot)["done"] == 0
    status, got, _ = await post_json(bot, "/dashboard/api/action", {"action": "routine.done", "args": {"id": "sns:x:1", "done": True}}, h)
    assert status == 200 and json.loads(bot.db.kv_get(f"routine_done:{tomorrow}")) == ["sns:x:1"]  # 그 목록의 날짜로 적음
    assert json.loads(bot.db.kv_get(f"routine_done:{day}")) == ["sns:x:1"]  # 전날 것은 남김


async def test_routine_api_needs_login(bot: DealBot) -> None:
    anon = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.7"}
    assert (await bot.web.dispatch("GET", "/dashboard/api/routine", anon))[0] == 401
    status, v, _ = await get_json(bot, "/dashboard/api/routine", session_headers(bot))
    assert status == 200 and v["total"] == 0 and v["slots"] == []


# ---------------------------------------------------------------- 4) routine.js
def test_routine_js_is_local_safe_and_korean() -> None:
    js = (STATIC / "routine.js").read_text(encoding="utf-8")
    assert "http://" not in js and "https://" not in js and "innerHTML" not in js and "eval(" not in js and "new Function" not in js
    assert "/dashboard/api/routine" in js and "X-Dash-Token" in js and "MutationObserver" in js and "routine.done" in js
    assert "clipboard" in js and "writeText(" in js and "type: 'checkbox'" not in js  # 체크는 단추 (app.js 그리기를 막지 않게)
    assert js.count("'input'") == 1 and "placeholder: '여기에 링크 붙여넣기'" in js  # 입력 칸은 에이블리 링크 부탁 하나뿐 (10/28)
    for word in ("오늘 할 일", "복사했어요", "다시 보기", "지금"):
        assert word in js, word
    node = shutil.which("node")
    if node:
        assert subprocess.run([node, "--check", str(STATIC / "routine.js")], capture_output=True).returncode == 0


# 작은 가짜 DOM (jsdom 없이): 할 일 탭에 붙는지 · 지금 칸만 펼쳤는지 · 체크가 X-Dash-Token 으로 routine.done 을 보내고 3/8 이 바로 바뀌는지 ·
# 복사 단추 · 다시 그려도 같은 칸 · 다른 탭엔 안 붙는지 · 목록이 없으면 숨는지
DOM_SHIM = r"""
const calls = [], out = {}, clip = [];
let observer = null;
class T { constructor(s) { this.data = s; this.parentNode = null; } get nodeType() { return 3; } get textContent() { return this.data; } }
class E {
  constructor(t) { this.tagName = t.toUpperCase(); this.children = []; this.attrs = {}; this.ls = {}; this.parentNode = null; this.className = ''; this._t = ''; this.hidden = false; this.style = {}; }
  get nodeType() { return 1; }
  get firstChild() { return this.children[0] || null; }
  set textContent(v) { this._t = String(v); this.children = []; }
  get textContent() { return this._t + this.children.map(c => c.textContent).join(''); }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener(n, f) { (this.ls[n] = this.ls[n] || []).push(f); }
  _add(c, front) { if (!c.nodeType) c = new T(String(c)); if (c.parentNode) c.parentNode.children = c.parentNode.children.filter(x => x !== c); c.parentNode = this; front ? this.children.unshift(c) : this.children.push(c); }
  append(...cs) { cs.forEach(c => this._add(c, false)); this._n(); }
  prepend(...cs) { cs.reverse().forEach(c => this._add(c, true)); this._n(); }
  replaceChildren(...cs) { this.children.forEach(c => { c.parentNode = null; }); this.children = []; cs.forEach(c => this._add(c, false)); this._n(); }
  _n() { if (observer && observer.t === this) queueMicrotask(observer.cb); }
  all(f, acc = []) { for (const c of this.children) if (c.nodeType === 1) { if (f(c)) acc.push(c); c.all(f, acc); } return acc; }
  click() { (this.ls.click || []).forEach(f => f({currentTarget: this})); }
}
const ids = {}, mk = (t, id) => { const e = new E(t); ids[id] = e; return e; };
const main = mk('main', 'main'), boot = mk('script', 'boot'), toast = mk('div', 'toast'), rf = mk('button', 'rf');
boot.textContent = JSON.stringify({me: {csrf: 'tok123'}});
main.append(new E('p'));
global.location = {hash: '#/todo'};
global.window = {addEventListener() {}, navigator: {clipboard: {writeText: async t => { clip.push(t); }}}};
global.MutationObserver = class { constructor(cb) { this.cb = cb; } observe(t) { this.t = t; observer = this; } };
global.document = {readyState: 'complete', visibilityState: 'visible', head: new E('head'), getElementById: i => ids[i] || null,
  createElement: t => new E(t), addEventListener() {}};
const VIEW = JSON.parse(process.argv[2]);
global.fetch = async (url, opt) => { calls.push({url, opt: opt || {}}); return url === '/dashboard/api/routine'
  ? {status: 200, json: async () => VIEW} : {status: 200, json: async () => ({status: 'ok', code: 'done', text: '체크했어요'})}; };
const tick = () => new Promise(r => setTimeout(r, 5));
const h2 = b => b.all(c => c.tagName === 'H2')[0].textContent;
(async () => {
  require(process.argv[1]);
  await tick(); await tick();
  const box = () => main.children.find(c => c.attrs.id === 'rtp');
  out.first = main.firstChild.attrs.id; out.hidden = box().hidden;
  out.head = h2(box());
  out.heads = box().all(c => c.className.startsWith('sl')).map(c => [c.textContent, c.attrs['aria-expanded']]);
  out.items = box().all(c => c.className.startsWith('it')).map(c => c.attrs['data-id']);
  out.bar = box().all(c => c.tagName === 'I')[0].style.width;
  box().all(c => c.tagName === 'BUTTON' && c.textContent === '제목 복사')[0].click();
  await tick();
  out.clip = clip; out.copyToast = toast.textContent;
  out.links = box().all(c => c.tagName === 'A').map(a => [a.attrs.href, a.attrs.target, a.attrs.rel]);
  box().all(c => c.className === 'cb')[1].click();
  out.optimistic = h2(box());
  await tick(); await tick();
  const post = calls.find(c => c.url === '/dashboard/api/action');
  out.post = {token: post.opt.headers['X-Dash-Token'], body: JSON.parse(post.opt.body)};
  out.after = h2(box()); out.checked = box().all(c => c.className === 'cb').map(c => c.attrs['aria-checked']);
  box().all(c => c.className.startsWith('sl'))[2].click();  // 접힌 칸 펼치기
  out.opened = box().all(c => c.className.startsWith('it')).map(c => c.attrs['data-id']);
  main.replaceChildren(new E('div'), new E('div'));  // app.js 가 할 일 탭을 다시 그림
  await tick();
  out.again = main.firstChild.attrs.id; out.keep = h2(box());
  location.hash = '#/connect';
  main.replaceChildren(new E('div'));
  await tick();
  out.connect = main.children.some(c => c.attrs.id === 'rtp');
  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


def test_routine_js_mounts_on_the_todo_tab_and_checks(bot: DealBot, tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 없음")
    day = tz_now(bot).date().isoformat()
    with_routine(tmp_path, sample(day))
    view = routine.view(bot, now=at(bot, day, "10:00"))
    out = subprocess.run([node, "-e", DOM_SHIM, str(STATIC / "routine.js"), json.dumps(view, ensure_ascii=False)],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["first"] == "rtp" and got["hidden"] is False and got["head"] == "오늘 할 일 · 0/3" and got["bar"] == "0%"
    assert got["heads"][0] == ["▾ 아침 09:00" + "0/2 · 약 9분 · 지금", "true"]  # 지금 칸만 펼침
    assert [x[1] for x in got["heads"]] == ["true", "false", "false", "false", "false"] and got["heads"][1][0].endswith("없음")
    assert got["items"] == ["pub:finance:1", "nb:issue:a"]
    assert got["clip"] == ["제목 한 줄\n둘째 줄"] and got["copyToast"] == "복사했어요"
    assert got["links"] == [["https://blog.naver.com/x", "_blank", "noopener noreferrer"]]
    assert got["optimistic"] == "오늘 할 일 · 1/3" and got["after"] == "오늘 할 일 · 1/3" and got["checked"] == ["false", "true"]
    assert got["post"] == {"token": "tok123", "body": {"action": "routine.done", "args": {"id": "nb:issue:a", "done": True},
                                                       "cid": got["post"]["body"]["cid"], "ctoken": None}}
    assert got["opened"] == ["pub:finance:1", "nb:issue:a", "sns:x:1"]
    assert got["again"] == "rtp" and got["keep"] == "오늘 할 일 · 1/3" and got["connect"] is False
    # 목록이 없으면 칸 전체를 숨김
    empty = routine.view(bot, now=at(bot, day, "10:00")) | {"slots": [], "total": 0}
    out = subprocess.run([node, "-e", DOM_SHIM.split("  out.head = ")[0] + "  console.log(JSON.stringify(out)); process.exit(0);\n})();",
                          str(STATIC / "routine.js"), json.dumps(empty, ensure_ascii=False)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == {"first": "rtp", "hidden": True}


def test_best_times_pass_through_and_bad_rows_drop(bot: DealBot, tmp_path: Path) -> None:
    """블로그별 예약 추천 시각 (주인 10/29 — Blog-Auto routine.best_times) — 블로그마다 한 줄, 시각은 HH:MM 또는 '바로'만."""
    times = [{"blog": "hotdeal", "name": "핫딜", "times": ["바로"], "note": "딜이 끝나기 전에"},
             {"blog": "finance", "name": "아카이브", "times": ["07:30", "25:00", "<b>", 3, "21:00"], "note": "출근길"},
             {"blog": "finance", "name": "또", "times": ["09:00"]}, {"blog": "x", "times": ["09:00"]},
             {"blog": "issue", "name": "데런픽", "times": []}]
    got = _routine({"day": "2026-10-29", "items": [], "times": times})["times"]
    assert got == [{"blog": "hotdeal", "name": "핫딜", "times": ["바로"], "note": "딜이 끝나기 전에"},
                   {"blog": "finance", "name": "아카이브", "times": ["07:30", "21:00"], "note": "출근길"}]
    assert _routine({"day": "2026-10-29", "items": []})["times"] == []  # 예전 Blog-Auto
    day = tz_now(bot).date().isoformat()
    with_routine(tmp_path, {**sample(day), "times": times})
    assert [t["blog"] for t in routine.view(bot)["times"]] == ["hotdeal", "finance"]
    js = (Path(__file__).resolve().parents[1] / "src/dealbot/dashboard/static/routine.js").read_text(encoding="utf-8")
    assert "블로그별 예약 추천 시각" in js and "data.times" in js

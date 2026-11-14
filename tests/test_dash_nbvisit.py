"""이웃 글 들르기 ↔ 오늘 할 일 줄 (주인 11/14 '이웃 글 들르기 공감·댓글도 책갈피로 — 세팅'). dashboard/nbvisit.py.

공감·댓글 책갈피는 '이웃 관리'(seoi.js) 하나 — 여기서는 둘째 책갈피를 만들지 않고 그 책갈피와 오늘 할 일의 이웃 글 들르기 줄을 잇는다:
1) post_of·rows·find: 네이버 블로그 글 주소만 · 그 블로그의 nb-visit 줄만.
2) tick: 오늘 목록의 그 블로그 줄만 체크 (routine.done 과 같은 kv) · 블로그마다 하루 5번 · 이미 체크된 줄·어제 목록·없는 글은 아무것도 안 함.
3) POST /dashboard/api/seoi_act (a=visit) 가 성공하면 줄도 체크 — 열쇠가 틀리면 아무것도 안 함 · 슬픈 글(공감 안 함)은 체크 안 함.
4) 할 일 탭: routine.view()["nb"] · routine.js '이웃 글 들르기 시작 (n)' (새 탭 · 누를 때마다 한 편 · 주소 끝 #bmb=<블로그>).
5) 공감·댓글 책갈피는 여전히 '이웃 관리' 하나 — nbvisit 책갈피 파일·설치 칸은 없음. 이 모듈은 AI·네이버 접속 없음.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from dash_helpers import push_settings
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import nbvisit, routine, seoi
from test_dash_routine import DOM_SHIM, SLOTS, at, item, tz_now, with_routine
from test_dash_seoi import DRAFT, FakeDrafter, _post, visit_form

STATIC = Path(__file__).resolve().parents[1] / "src" / "dealbot" / "dashboard" / "static"
POST_NO = 223456789012


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


@pytest.fixture(autouse=True)
def _no_live_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    from dealbot.dashboard import threads_proxy

    def off(*a: object) -> None:
        raise ConnectionError("test")
    monkeypatch.setattr(threads_proxy, "_call", off)
    routine._TH_CACHE.clear()


def visit_row(blog: str, nid: str, no: int, *, mobile: bool = True, **kw: Any) -> dict[str, Any]:
    """Blog-Auto routine.neighbor_items 가 만드는 공감·댓글 줄."""
    host = "m.blog.naver.com" if mobile else "blog.naver.com"
    return item(f"nb-visit:{blog}:{nid}:{no}", "s3", blog=blog, title=f"공감·댓글 · {nid} 의 새 글", min=3,
                copy=[{"label": "댓글 초안", "text": "잘 읽었어요"}], links=[{"label": "글 열기", "url": f"https://{host}/{nid}/{no}"}], **kw)


def day_of(bot: DealBot) -> str:
    return tz_now(bot).date().isoformat()


def list_for(tmp_path: Path, day: str, rows: list[dict[str, Any]]) -> None:
    with_routine(tmp_path, {"day": day, "slots": SLOTS, "overflow": 0, "items": rows})


# ---------------------------------------------------------------- 1) 주소 · 줄 찾기
def test_only_naver_blog_post_addresses_are_read() -> None:
    assert nbvisit.post_of("https://blog.naver.com/Neighbor1/223456789012") == ("neighbor1", "223456789012")
    assert nbvisit.post_of("https://m.blog.naver.com/neighbor1/223456789012/") == ("neighbor1", "223456789012")
    assert nbvisit.post_of("https://m.blog.naver.com/PostView.naver?blogId=neighbor1&logNo=223456789012") == ("neighbor1", "223456789012")
    for bad in ("http://blog.naver.com/neighbor1/223456789012", "https://blog.naver.com.evil.example/neighbor1/223456789012",
                "https://evil.example/neighbor1/223456789012", "https://blog.naver.com/neighbor1", "https://blog.naver.com:8443/neighbor1/223456789012",
                "https://user@blog.naver.com/neighbor1/223456789012", "https://cafe.naver.com/neighbor1/223456789012",
                "javascript:alert(1)", "", None, 7, "https://blog.naver.com/" + "a" * 600):
        assert nbvisit.post_of(bad) is None, bad


def test_rows_and_find_use_only_that_blogs_visit_rows() -> None:
    items = [visit_row("issue", "neighbor1", POST_NO), visit_row("hotdeal", "fan2", 223000000002, mobile=False),
             item("nb-add:issue:neighbor1", "s4", blog="issue"), item("nb-visit:issue:bad", "s3", blog="issue"),  # 글 번호 없는 줄은 아님
             {"id": "nb-visit:issue:odd:223000000009", "links": [{"url": "https://evil.example/x/223000000009"}], "title": "t"}]
    rs = nbvisit.rows(items)
    assert [(r["blog"], r["nid"], r["no"]) for r in rs] == [("issue", "neighbor1", str(POST_NO)), ("hotdeal", "fan2", "223000000002"),
                                                             ("issue", "odd", "223000000009")]
    assert rs[0]["title"] == "neighbor1 의 새 글" and rs[2]["url"] == ""  # 네이버 글 주소가 아니면 열 주소로 안 씀
    assert nbvisit.find(items, "issue", f"https://blog.naver.com/neighbor1/{POST_NO}") == f"nb-visit:issue:neighbor1:{POST_NO}"
    assert nbvisit.find(items, "issue", f"https://m.blog.naver.com/PostView.naver?blogId=NEIGHBOR1&logNo={POST_NO}") == f"nb-visit:issue:neighbor1:{POST_NO}"
    assert nbvisit.find(items, "hotdeal", f"https://blog.naver.com/neighbor1/{POST_NO}") is None  # 다른 블로그 계정으로 들른 글
    assert nbvisit.find(items, "issue", "https://blog.naver.com/neighbor1/223000000777") is None  # 줄에 없는 글
    assert nbvisit.find(items, "issue", "https://evil.example/neighbor1/223456789012") is None and nbvisit.find(None, "issue", "x") is None


# ---------------------------------------------------------------- 2) 체크
def test_tick_marks_todays_row_once_and_stops_at_five_per_blog(bot: DealBot, tmp_path: Path) -> None:
    day = day_of(bot)
    now = at(bot, day, "15:10")
    assert nbvisit.tick(bot, "issue", f"https://blog.naver.com/nbr1/{POST_NO}", now) == {"ok": False, "why": "off"}  # 목록 없음
    rows = [visit_row("issue", f"nbr{i}", POST_NO + i) for i in range(7)] + [visit_row("hotdeal", "fan1", 223000000001)]
    list_for(tmp_path, day, rows)
    rid = f"nb-visit:issue:nbr0:{POST_NO}"
    assert nbvisit.tick(bot, "issue", f"https://blog.naver.com/nbr0/{POST_NO}", now) == {"ok": True, "id": rid, "n": 1}
    assert routine.done_ids(bot.db, day) == {rid}  # routine.done 과 같은 곳에 적힘
    assert nbvisit.tick(bot, "issue", f"https://m.blog.naver.com/nbr0/{POST_NO}", now) == {"ok": False, "why": "had", "id": rid}  # 다시 세지 않음
    assert nbvisit.tick(bot, "hotdeal", f"https://blog.naver.com/nbr1/{POST_NO + 1}", now)["why"] == "row"  # 그 블로그 줄이 아님
    assert nbvisit.tick(bot, "issue", "https://blog.naver.com/zzz/223000000999", now)["why"] == "row"
    assert nbvisit.tick(bot, "nope", f"https://blog.naver.com/nbr1/{POST_NO + 1}", now)["why"] == "row"
    for i in range(1, 5):
        assert nbvisit.tick(bot, "issue", f"https://blog.naver.com/nbr{i}/{POST_NO + i}", now)["n"] == i + 1
    sixth = nbvisit.tick(bot, "issue", f"https://blog.naver.com/nbr5/{POST_NO + 5}", now)
    assert sixth == {"ok": False, "why": "cap", "id": f"nb-visit:issue:nbr5:{POST_NO + 5}"} and nbvisit.DAY_CAP == 5  # 블로그마다 하루 5번
    assert len(routine.done_ids(bot.db, day)) == 5
    assert nbvisit.tick(bot, "hotdeal", "https://blog.naver.com/fan1/223000000001", now)["n"] == 1  # 다른 블로그는 따로 셈
    v = routine.view(bot, now=now)
    assert v["done"] == 6 and v["nb"]["cap"] == 5 and v["nb"]["by"]["issue"]["n"] == 5 and v["nb"]["by"]["issue"]["left"] == 2
    assert v["nb"]["by"]["issue"]["next"] == {"id": f"nb-visit:issue:nbr5:{POST_NO + 5}", "url": f"https://m.blog.naver.com/nbr5/{POST_NO + 5}#bmb=issue",
                                              "title": "nbr5 의 새 글"}
    assert v["nb"]["by"]["hotdeal"] == {"left": 0, "n": 1, "next": None} and "finance" not in v["nb"]["by"]
    # 주인이 손으로 체크한 줄은 세지 않음 (상한은 책갈피가 체크한 것만)
    assert json.loads(bot.db.kv_get(nbvisit.DAY_KEY + day))["issue"] == [f"nb-visit:issue:nbr{i}:{POST_NO + i}" for i in range(5)]


def test_tick_ignores_yesterdays_list(bot: DealBot, tmp_path: Path) -> None:
    yesterday = (tz_now(bot) - timedelta(days=1)).date().isoformat()
    list_for(tmp_path, yesterday, [visit_row("issue", "nbr0", POST_NO)])
    assert nbvisit.tick(bot, "issue", f"https://blog.naver.com/nbr0/{POST_NO}", tz_now(bot).timestamp()) == {"ok": False, "why": "off"}
    assert routine.done_ids(bot.db, yesterday) == set()
    assert routine.view(bot)["nb"]["by"]["issue"]["left"] == 1  # 줄은 보이되 (어제 목록 안내와 함께) 책갈피로는 체크 안 함


# ---------------------------------------------------------------- 3) 책갈피가 들르면 줄도 체크
async def test_bookmark_visit_ticks_the_row_and_bad_key_does_nothing(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    day = day_of(bot)
    rid = f"nb-visit:finance:neighbor1:{POST_NO}"
    list_for(tmp_path, day, [visit_row("finance", "neighbor1", POST_NO), visit_row("finance", "nbr9", 223000000009)])
    d = FakeDrafter()
    monkeypatch.setattr(seoi, "get_drafter", lambda: d)
    res = await _post(bot, {**visit_form(bot.db, "finance"), "k": "wrong"}, origin="https://m.blog.naver.com")
    assert res[0] == 403 and routine.done_ids(bot.db, day) == set()  # 열쇠가 틀리면 줄도 그대로
    res = await _post(bot, visit_form(bot.db, "finance"), origin="https://m.blog.naver.com")
    body = json.loads(res[1])
    assert res[0] == 200 and body["comment"] == DRAFT and body["row"] == rid
    assert body["text"] == "오늘 이웃 들르기 1/40 · 드라마 1 · 할 일 줄 체크 ✓"  # 책갈피 끝 알림에 그대로 보임
    assert routine.done_ids(bot.db, day) == {rid}
    # 같은 글을 10분 안에 다시 (댓글 화면에서 이어 하기) — 다시 체크·세지 않음
    res = await _post(bot, visit_form(bot.db, "finance"), origin="https://m.blog.naver.com")
    body = json.loads(res[1])
    assert res[0] == 200 and body["again"] is True and "row" not in body and "할 일 줄" not in body["text"]
    assert json.loads(bot.db.kv_get(nbvisit.DAY_KEY + day))["finance"] == [rid]
    # 오늘 할 일에 없는 글(이웃새글에서 연 글)도 책갈피는 그대로 되고 줄은 건드리지 않음
    bot.db.kv_set("seoi:pace:visit", "{}")
    res = await _post(bot, visit_form(bot.db, "finance", 223000000555), origin="https://m.blog.naver.com")
    body = json.loads(res[1])
    assert res[0] == 200 and body["ok"] is True and "row" not in body and routine.done_ids(bot.db, day) == {rid}


async def test_sad_post_is_not_ticked_and_a_broken_tick_never_breaks_the_bookmark(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    day = day_of(bot)
    list_for(tmp_path, day, [visit_row("issue", "neighbor1", POST_NO), visit_row("issue", "neighbor1", POST_NO + 1)])
    monkeypatch.setattr(seoi, "get_drafter", lambda: FakeDrafter())
    sad = visit_form(bot.db, "issue", t="부고 알립니다", x="삼가 고인의 명복을 빕니다. 빈소는 서울 병원 장례식장입니다. " * 3)
    res = await _post(bot, sad, origin="https://m.blog.naver.com")
    body = json.loads(res[1])
    assert res[0] == 200 and body["like"] is False and "row" not in body and routine.done_ids(bot.db, day) == set()  # 공감·댓글을 주인이 판단할 글
    bot.db.kv_set("seoi:pace:visit", "{}")

    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("kv down")
    monkeypatch.setattr(nbvisit, "tick", boom)
    res = await _post(bot, visit_form(bot.db, "issue", POST_NO + 1), origin="https://m.blog.naver.com")
    body = json.loads(res[1])
    assert res[0] == 200 and body["comment"] == DRAFT and "row" not in body  # 체크만 안 됨 (주인이 줄을 직접 체크)


# ---------------------------------------------------------------- 4) 화면
NB_FLOW = r"""
  await tick(); await tick();
  const part = window.SBRoutine.part('issue', '데런픽', [], []);
  const box = part[0];
  const a = box.all(c => c.tagName === 'A' && /이웃 글 들르기 시작/.test(c.textContent));
  out.launch = a.map(x => [x.textContent, x.attrs.href, x.attrs.target, x.attrs.rel]);
  out.hint = box.all(c => c.tagName === 'SPAN' && /이웃 관리/.test(c.textContent)).map(x => x.textContent);
  out.heads = box.all(c => c.className === 'sg').map(x => x.textContent);
  out.other = window.SBRoutine.part('hotdeal', '라커룸', [], []).flatMap(b => b.all(c => c.tagName === 'A' && /들르기 시작/.test(c.textContent))).length;
  out.fetches = calls.map(c => c.url);
  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


def test_todo_tab_offers_one_post_per_tap(bot: DealBot, tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 없음")
    day = day_of(bot)
    now = at(bot, day, "15:10")
    list_for(tmp_path, day, [visit_row("issue", "nbr0", POST_NO), visit_row("issue", "nbr1", POST_NO + 1), visit_row("issue", "nbr2", POST_NO + 2),
                             visit_row("hotdeal", "fan1", 223000000001)])
    nbvisit.tick(bot, "issue", f"https://blog.naver.com/nbr0/{POST_NO}", now)
    nbvisit.tick(bot, "hotdeal", "https://blog.naver.com/fan1/223000000001", now)
    view = routine.view(bot, now=now)
    shim = DOM_SHIM.replace("localStorage: {getItem: () => 'slot'}", "localStorage: {getItem: () => 'blog'}, dispatchEvent() {}, SBHub: {}")
    assert "SBHub: {}" in shim
    flow = shim.split("  await tick(); await tick();\n  const box = () =>")[0] + NB_FLOW
    out = subprocess.run([node, "-e", flow, str(STATIC / "routine.js"), json.dumps(view, ensure_ascii=False)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    # 아직 안 한 첫 글 하나 — 새 탭 · 누를 때마다 한 편 (목록을 저절로 돌지 않음) · 책갈피가 어느 블로그인지 알게 #bmb
    assert got["launch"] == [["이웃 글 들르기 시작 (2) ↗", f"https://m.blog.naver.com/nbr1/{POST_NO + 1}#bmb=issue", "_blank", "noopener noreferrer"]]
    assert len(got["hint"]) == 1 and "등록은 직접" in got["hint"][0] and "줄은 저절로 체크" in got["hint"][0] and "오늘 이웃 들르기" in got["hint"][0]
    assert any(h.startswith("이웃 글 들르기 (공감·댓글)") for h in got["heads"])
    assert got["other"] == 0  # 다 한 블로그에는 단추 없음
    assert all(u.startswith("/dashboard/api/") for u in got["fetches"])  # 대시보드 화면은 네이버를 부르지 않음 (새 탭은 주인이 누를 때)
    js = (STATIC / "routine.js").read_text(encoding="utf-8")
    assert "location.href" not in js and "window.open" not in js and "W.open(" not in js  # 스스로 다음 글로 넘어가지 않음


# ---------------------------------------------------------------- 5) 책갈피는 하나 · AI 없음
def test_no_second_bookmarklet_and_no_ai_in_this_module() -> None:
    assert not (STATIC / "nbvisit.js").exists()  # 공감·댓글 책갈피는 '이웃 관리'(seoi.js) 하나
    lg = (STATIC / "logins.js").read_text(encoding="utf-8")
    assert "{api: 'seoi', t: '이웃 관리 버튼 설치 (서이추·이웃 들르기)'}" in lg and "nbvisit" not in lg and "nb_visit" not in lg
    src = Path(nbvisit.__file__).read_text(encoding="utf-8")
    for word in ("llm", "claude", "anthropic", "JsonAsker", "http.get", "httpx", "urlopen", "requests", "playwright", "subprocess"):
        assert word not in src.split('"""', 2)[2], word  # 코드에 AI·바깥 접속 없음 (설명 글 제외)
    js = (STATIC / "seoi.js").read_text(encoding="utf-8")
    assert "g.text" in js  # 책갈피 끝 알림이 대시보드가 준 글(… · 할 일 줄 체크 ✓)을 그대로 보임

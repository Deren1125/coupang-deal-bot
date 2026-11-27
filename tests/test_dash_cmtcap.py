"""독자 댓글 모으기 (주인 11/27 — '댓글 모아 글 방향 보완' · 서버는 네이버에 접속하지 않음 · 자동 답글 없음).

1) cmtcap.save: 열쇠(통계 것) · 네이버 화면만 · 우리 블로그만 · 칸은 댓글 번호·글·제목·시각·글·답글 여부만 (아이디·별명 버림) ·
   7일 넘은 것·이상한 칸 버림 · 다시 오면 덮어쓰기(처음 본 때는 그대로 · 한 번 답했으면 답한 것) · 하루 횟수 · 진단 g · 오래된 것 지움.
2) POST /dashboard/api/comments_capture: 로그인 없이 열쇠로만 · CORS 는 네이버만 · 한 번에 책갈피 안에 댓글 길이 들어감 (v5).
3) cmtcap.js 가짜 모바일 블로그 (진짜 크롬 · playwright 가 있을 때만): 공개 글 목록 → 공개 댓글창 JSONP → 블로그마다 한 번 보냄 ·
   주인 댓글·지운 것·비밀 댓글·7일 넘은 것 빼고 · 주인 답글 여부 · 못 읽으면 진단만 · 하루 한 번.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode
from zoneinfo import ZoneInfo

import pytest

from dash_helpers import push_settings
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import cmtcap, kkumi, one, seoi, statscap
from test_dash_kkumi import _playwright_env
from test_dash_seoi import NODE, needs_node
from test_dash_statscap import FakeDb

TZ = ZoneInfo("Asia/Seoul")
NOW = 1_795_700_000.0  # 2026-11-26 KST 저녁
DASH = "https://dash.example"


def iso(t: float) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(t, TZ).strftime("%Y-%m-%dT%H:%M:%S%z")


def cform(db: FakeDb, items: list[dict], **kw: str) -> dict[str, str]:
    return {"k": statscap.key(db), "u": "https://m.blog.naver.com/FeedList.naver", "b": "derenpick", "c": json.dumps(items, ensure_ascii=False), "g": "", **kw}


def cmt(no: str, text: str, ago_h: float = 3, **kw) -> dict:  # type: ignore[no-untyped-def]
    return {"no": no, "p": "224000000001", "t": "에이티즈 3년 연속 대상", "at": iso(NOW - ago_h * 3600), "x": text, "lv": 1, "r": 0, **kw}


# ---------------------------------------------------------------- 1) 저장
def test_save_keeps_only_text_time_post_and_reply_flag() -> None:
    db = FakeDb()
    items = [cmt("101", "날짜가 틀렸어요 12월 3일이 아니라 4일이에요", nick="별명", profileUserId="someone", userName="홍길동"),
             cmt("102", "연락은 010-1234-5678 로", r=1), cmt("103", "옛날 댓글", ago_h=24 * 9), {"no": "x1", "p": "1", "at": iso(NOW), "x": "번호 아님"},
             cmt("104", "<b>다음엔 콘서트 일정</b>도 정리해 주세요", lv=2)]
    got = cmtcap.save(db, cform(db, items), now=NOW, tz=TZ)
    assert got == {"ok": True, "label": "데런픽 댓글 3개 (새 3)", "blog": "issue", "n": 3, "new": 3}
    rec = json.loads(db.kv[f"{cmtcap.PREFIX}issue:101"])
    assert set(rec) == {"blog", "no", "post", "title", "at", "text", "lv", "replied", "seen", "last"}
    assert "별명" not in json.dumps(rec, ensure_ascii=False) and "someone" not in json.dumps(rec) and "홍길동" not in json.dumps(rec, ensure_ascii=False)
    assert rec["post"] == "224000000001" and rec["replied"] == 0 and rec["lv"] == 1
    assert json.loads(db.kv[f"{cmtcap.PREFIX}issue:102"])["text"] == "연락은 [전화] 로" and json.loads(db.kv[f"{cmtcap.PREFIX}issue:102"])["replied"] == 1
    r4 = json.loads(db.kv[f"{cmtcap.PREFIX}issue:104"])
    assert "<" not in r4["text"] and r4["lv"] == 2
    assert f"{cmtcap.PREFIX}issue:103" not in db.kv
    last = json.loads(db.kv[f"{cmtcap.LAST_PREFIX}issue"])
    assert (last["n"], last["new"], last["posts"]) == (3, 3, 1) and "diag" not in last
    # 다시 옴: 처음 본 때는 그대로 · 한 번 답했으면 답한 것으로 · 새 것만 new
    later = NOW + 3600
    got = cmtcap.save(db, cform(db, [cmt("102", "연락은", r=0), cmt("105", "고마워요")]), now=later, tz=TZ)
    assert (got["n"], got["new"]) == (2, 1)
    r2 = json.loads(db.kv[f"{cmtcap.PREFIX}issue:102"])
    assert r2["replied"] == 1 and r2["seen"] == int(NOW) and r2["last"] == int(later)


def test_save_checks_key_where_blog_day_cap_and_keeps_diag() -> None:
    db = FakeDb()
    for bad, code in (({"k": "wrong"}, "key"), ({"u": "https://evil.example/x"}, "where"), ({"b": "someoneelse"}, "blog")):
        with pytest.raises(statscap.CaptureError) as e:
            cmtcap.save(db, cform(db, [], **bad), now=NOW, tz=TZ)
        assert e.value.code == code
    got = cmtcap.save(db, cform(db, [], b="demiyum", g="list 403 error"), now=NOW, tz=TZ)
    assert got["blog"] == "hotdeal" and got["n"] == 0
    assert json.loads(db.kv[f"{cmtcap.LAST_PREFIX}hotdeal"])["diag"] == "list 403 error"
    for _ in range(cmtcap.DAY_MAX - 1):
        cmtcap.save(db, cform(db, [], b="demiyum"), now=NOW, tz=TZ)
    with pytest.raises(statscap.CaptureError) as e:
        cmtcap.save(db, cform(db, [], b="demiyum"), now=NOW, tz=TZ)
    assert e.value.code == "day_max"
    cmtcap.save(db, cform(db, [], b="demiyum"), now=NOW + 86400, tz=TZ)  # 다음 날은 다시
    line = cmtcap.received_line(db, NOW + 86400, TZ)
    assert line.startswith("댓글: 데런픽 아직 · 드라마 아직 · 라커룸 ") and line.endswith(" 0개")


def test_old_comments_are_pruned_and_cap_per_blog() -> None:
    db = FakeDb()
    cmtcap.save(db, cform(db, [cmt("1", "처음")]), now=NOW, tz=TZ)
    later = NOW + cmtcap.KEEP_DAYS * 86400
    cmtcap.save(db, cform(db, [cmt(str(i + 10), f"댓글 {i}", at=iso(later - 3600)) for i in range(130)]), now=later, tz=TZ)
    keys = [k for k in db.kv if k.startswith(cmtcap.PREFIX)]
    assert f"{cmtcap.PREFIX}issue:1" not in keys and len(keys) == cmtcap.PER_BLOG_MAX


# ---------------------------------------------------------------- 2) 받는 곳
@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


async def _post(bot: DealBot, body: dict[str, str], **headers: str):  # type: ignore[no-untyped-def]
    raw = urlencode(body).encode()
    h = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.9", "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
         "content-length": str(len(raw)), "sec-fetch-site": "cross-site", **headers}
    return await bot.web.dispatch("POST", cmtcap.PATH, h, raw)


async def test_api_comments_capture(bot: DealBot) -> None:
    now = time.time()
    good = {"k": statscap.key(bot.db), "u": "https://m.blog.naver.com/FeedList.naver", "b": "derenlog",
            "c": json.dumps([{"no": "9", "p": "224", "t": "트로트", "at": iso(now - 600), "x": "좋아요", "lv": 1, "r": 0}], ensure_ascii=False), "g": ""}
    res = await _post(bot, good, origin="https://m.blog.naver.com")
    assert res[0] == 200 and json.loads(res[1])["n"] == 1 and res[3]["Access-Control-Allow-Origin"] == "https://m.blog.naver.com"
    res = await _post(bot, {**good, "k": seoi.key(bot.db)}, origin="https://m.blog.naver.com")
    assert res[0] == 403 and json.loads(res[1])["code"] == "key"
    res = await _post(bot, good, origin="https://evil.example")
    assert res[0] == 200 and "Access-Control-Allow-Origin" not in res[3]
    assert json.loads(bot.db.kv_get(f"{cmtcap.PREFIX}finance:9"))["text"] == "좋아요"
    assert cmtcap.PATH in bot.dashboard.api.public_paths()
    anon = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.7"}
    assert (await bot.web.dispatch("GET", cmtcap.PATH, anon))[0] == 405


def test_one_bookmarklet_holds_the_comment_hop_with_the_stats_key() -> None:
    db = FakeDb()
    js = unquote(one.view(db, DASH)["code"][len("javascript:"):])
    assert f"U='{DASH}{cmtcap.PATH}',K='{statscap.key(db)}'" in js and one.version() == 5
    assert js.index("__sbc1") < js.index("U='" + DASH + seoi.PATH)  # 댓글이 이웃 관리 앞


# ---------------------------------------------------------------- 3) 가짜 모바일 블로그
PW = r"""
const {chromium} = require('playwright');
(async () => {
  const [code, sj] = process.argv.slice(1), S = JSON.parse(sj), out = {bodies: [], api: [], cb: []};
  const b = await chromium.launch(), c = await b.newContext(), p = await c.newPage();
  await p.route('**/*', async r => {
    const u = new URL(r.request().url());
    if (u.hostname === 'dash.example') { out.bodies.push(r.request().postData() || '');
      return r.fulfill({status: 200, contentType: 'application/json', headers: {'Access-Control-Allow-Origin': '*'}, body: JSON.stringify({ok: true, label: 'x'})}); }
    if (u.hostname === 'apis.naver.com') { const o = u.searchParams.get('objectId'); out.cb.push(o);
      const j = S.cbox[o]; return r.fulfill({status: 200, contentType: 'text/javascript', body: j ? u.searchParams.get('_callback') + '(' + j + ');' : 'oops('}); }
    if (u.pathname.startsWith('/api/blogs/')) { out.api.push(u.pathname); const j = S.lists[u.pathname.split('/')[3]];
      return j ? r.fulfill({status: 200, contentType: 'application/json', body: j}) : r.fulfill({status: 403, contentType: 'text/html', body: 'no'}); }
    return r.fulfill({status: 200, contentType: 'text/html; charset=utf-8', body: '<html><head></head><body>이웃새글</body></html>'});
  });
  await p.goto('https://m.blog.naver.com/FeedList.naver');
  await p.evaluate(code);
  out.line = await p.evaluate(() => window.__sbcl);
  out.ls = await p.evaluate(() => localStorage.getItem('sbc1'));
  await p.evaluate(() => { window.__sbcl = ''; });
  const n = out.bodies.length;
  await p.evaluate(code);
  out.again = [out.bodies.length - n, await p.evaluate(() => window.__sbcl)];
  console.log(JSON.stringify(out));
  await b.close();
})().catch(e => { console.error(String(e)); process.exit(2); });
"""


def _code(db: FakeDb) -> str:
    src = kkumi.minify(cmtcap.SRC.read_text(encoding="utf-8"))
    return (src.replace("@@CURL@@", DASH + cmtcap.PATH).replace("@@SKEY@@", statscap.key(db)).replace("@@BLOGS@@", seoi.blogs_json()))


@needs_node
def test_fake_mobile_blog_reads_public_lists_and_comment_boxes_and_sends_per_blog() -> None:
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    now = time.time()
    lst = lambda items: json.dumps({"isSuccess": True, "result": {"items": items}}, ensure_ascii=False)  # noqa: E731
    cb = lambda cl: json.dumps({"success": True, "result": {"commentList": cl, "pageModel": {"page": 1, "totalPages": 1}}}, ensure_ascii=False)  # noqa: E731
    base = {"replyLevel": 1, "deleted": False, "secret": False, "blind": False, "mine": False, "manager": False}
    S = {"lists": {"derenpick": lst([{"logNo": 224, "blogNo": 9, "commentCnt": 4, "titleWithInspectMessage": "에이티즈 대상"},
                                      {"logNo": 225, "blogNo": 9, "commentCnt": 0, "titleWithInspectMessage": "댓글 없음"}]),
                   "derenlog": lst([{"logNo": 300, "blogNo": 7, "commentCnt": 1, "titleWithInspectMessage": "트로트"}])},
         "cbox": {"9_201_224": cb([dict(base, commentNo=1, contents="날짜가 틀렸어요", userName="홍길동", profileUserId="fan1", regTime=iso(now - 3600)),
                                   dict(base, commentNo=2, contents="감사합니다", replyLevel=2, parentCommentNo=1, mine=True, regTime=iso(now - 1800)),
                                   dict(base, commentNo=3, contents="오래된 댓글", regTime=iso(now - 9 * 86400)),
                                   dict(base, commentNo=4, contents="비밀", secret=True, regTime=iso(now - 60)),
                                   dict(base, commentNo=5, contents="콘서트 일정도 써 주세요", regTime=iso(now - 60))])}}
    db = FakeDb()
    res = subprocess.run([NODE, "-e", PW, _code(db), json.dumps(S, ensure_ascii=False)], capture_output=True, text=True, timeout=180, env=env)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    sent = [dict(parse_qsl(x, keep_blank_values=True)) for x in got["bodies"]]
    assert [f["b"] for f in sent] == ["derenpick", "derenlog", "demiyum"] and all(f["k"] == statscap.key(db) for f in sent)
    assert got["cb"] == ["9_201_224", "7_201_300"]  # 댓글 있는 글만 · 다른 블로그·다른 주소는 안 엶
    cs = json.loads(sent[0]["c"])
    assert [(c["no"], c["r"]) for c in cs] == [("1", 1), ("5", 0)]  # 주인 답글은 보내지 않고 '답함'으로만 · 7일 넘은 것·비밀 빼고
    assert set(cs[0]) == {"no", "p", "t", "at", "x", "lv", "r"} and "홍길동" not in sent[0]["c"] and "fan1" not in sent[0]["c"]
    assert json.loads(sent[1]["c"]) == [] and sent[1]["g"].startswith("cbox ")  # 댓글창이 이상하면 진단만
    assert sent[2]["g"].startswith("list 403")
    assert got["line"] == "데런픽 2개 · 드라마 0개(일부 못 읽음) · 라커룸 0개(일부 못 읽음)" and got["ls"]
    assert got["again"] == [0, "오늘 보냈어요"]  # 하루 한 번
    recs = [cmtcap.save(db, f, now=now, tz=TZ) for f in sent]
    assert [r["n"] for r in recs] == [2, 0, 0]

"""'Ably 랭킹 보내기' 책갈피 (주인 11/12 — 코디 랭킹 캡처 올리기 대신 · 서버는 에이블리를 열지 않음, 10/28).

1) ablyrank.bookmarklet(): static/ablyrank.js → "javascript:" 주소 · 파일 규칙(statscap.js 와 같음).
2) 가짜 에이블리 랭킹 화면 (진짜 크롬 · node playwright — 모든 요청을 가로채 바깥으로 나가지 않음): 순위·상품명·쇼핑몰·가격·할인·리뷰·별점·
   상품·사진 주소 · 코디 카드(제목·좋아요·든 상품) · 고른 탭 · 늦게 뜨는 카드 기다림 → 서버 save 까지. 에이블리 밖에서는 알림만.
3) save(): 열쇠 · 에이블리 주소만 · 칸 모양 · 개수·크기 제한 · 30일 · 하루 개수.
4) GET /dashboard/api/ablyrank (로그인 아래) · POST /dashboard/api/ably_rank_capture (열쇠로만 · CORS 는 a-bly.com 만 · 폼이면 HTML) ·
   Blog-Auto 가 'ably_rank_capture' 를 알리면 일로 넘김 (계약 action_ably_rank_capture.json).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode
from zoneinfo import ZoneInfo

import pytest

from dash_helpers import fixture, get_json, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import ablyrank, statscap

STATIC = Path(__file__).resolve().parents[1] / "src" / "dealbot" / "dashboard" / "static"
NODE = shutil.which("node")
TZ = ZoneInfo("Asia/Seoul")
NOW = 1_794_480_000.0  # 2026-11-12 KST


class FakeDb:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    def kv_get(self, k: str, default: str | None = None) -> str | None:
        return self.kv.get(k, default)

    def kv_set(self, k: str, v: str) -> None:
        self.kv[k] = v

    def kv_items(self, prefix: str) -> list[tuple[str, str | None]]:
        return sorted((k, v) for k, v in self.kv.items() if k.startswith(prefix))

    def kv_delete_prefix(self, prefix: str, *, keep=None) -> int:  # type: ignore[no-untyped-def]
        gone = [k for k, v in self.kv.items() if k.startswith(prefix) and not (keep and keep(k, v))]
        for k in gone:
            del self.kv[k]
        return len(gone)


def code(db: FakeDb) -> str:
    c = ablyrank.view(db, "https://dash.example")["code"]
    assert c.startswith("javascript:")
    return unquote(c[len("javascript:"):])


def recs(db: FakeDb) -> list[dict]:
    return [json.loads(v) for k, v in sorted(db.kv.items()) if k.startswith(ablyrank.PREFIX)]


# ---------------------------------------------------------------- 1) 책갈피
def test_bookmarklet_has_endpoint_key_and_keeps_file_rules() -> None:
    db = FakeDb()
    js = code(db)
    assert "'https://dash.example/dashboard/api/ably_rank_capture'" in js and f"'{ablyrank.key(db)}'" in js and "@@" not in js
    assert "/*" not in js and "\n//" not in js and len(ablyrank.view(db, "https://dash.example")["code"]) < 16000
    assert js.count("fetch(") == 1 and "credentials:'omit'" in js and "XMLHttpRequest" not in js and "cookie" not in js
    assert ablyrank.key(db) != statscap.key(db) and len(ablyrank.INSTALL_STEPS) == 4
    with pytest.raises(ValueError):
        ablyrank.bookmarklet("https://x.example/a'b", "k")
    src = (STATIC / "ablyrank.js").read_text(encoding="utf-8")
    body = src.split("*/", 1)[1]
    for ln in body.splitlines():
        s = ln.strip()
        assert "//" not in s or s.startswith("//") or "://" in s or "\\/" in s, ln  # // 주석은 줄 맨 앞에만
    for rx in re.findall(r"(?<![\w)])/(?![/*])((?:\\.|[^/\n])+)/[gimsuy]*[.;),]", body):
        assert " " not in rx and "'" not in rx and '"' not in rx, rx  # 정규식 안에 공백·따옴표 없음
    if NODE:
        assert subprocess.run([NODE, "--check", str(STATIC / "ablyrank.js")], capture_output=True).returncode == 0
        assert subprocess.run([NODE, "-e", "new Function(process.argv[1])", js], capture_output=True).returncode == 0  # 줄인 코드도 읽힘


# ---------------------------------------------------------------- 2) 가짜 에이블리 화면 (진짜 크롬)
PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>코디 랭킹 | 에이블리</title></head><body>
<header><nav><a href="/">홈</a> <a href="/ranking">랭킹</a></nav></header>
<h1>랭킹</h1>
<div role="tablist"><button role="tab" aria-selected="false">상품</button><button role="tab" aria-selected="true">코디</button>
<button role="tab" aria-selected="false">마켓</button></div>
<ul id="looks">
 <li><a href="/coordi/7001"><span>1</span><img src="https://img.cdn.example/look/7001.jpg" width="200"><p>데이트룩 니트 코디</p><span>좋아요 1.2만</span></a>
  <div><a href="/goods/111"><img src="https://img.cdn.example/g/111.jpg" width="80"><span class="market_name">러브마켓</span><span class="goods_name">앙고라 브이넥 니트</span><span>20%</span><b>31,200원</b></a></div>
  <div><a href="/goods/112"><img src="https://img.cdn.example/g/112.jpg" width="80"><p>러브마켓</p><p>A라인 미니 스커트</p><p>25,900원</p></a></div>
 </li>
</ul>
<ol id="goods"></ol>
<p id="ld">불러오는 중</p>
<script>
setTimeout(() => {
  const ol = document.getElementById('goods');
  ol.innerHTML = [
    '<li><div><span>1</span></div><a href="https://m.a-bly.com/goods/201"><img data-src="https://img.cdn.example/g/201.jpg" width="120"></a>'
      + '<div><p>무드온</p><a href="https://m.a-bly.com/goods/201">울 블렌드 롱 코트</a><p><em>35%</em> <del>99,000</del> <strong>64,350원</strong></p>'
      + '<p>★4.8 리뷰 1,234</p><p>무료배송</p></div></li>',
    '<li><div><span>2위</span></div><a href="/goods/202"><img src="https://img.cdn.example/g/202.jpg" width="120"></a>'
      + '<div><p>데일리룸</p><a href="/goods/202">케이블 가디건 2026 신상</a><p>29,900원</p><p>찜 532</p></div></li>',
    '<li style="display:none"><a href="/goods/299">숨은 상품</a><p>9,900원</p></li>'
  ].join('');
  document.getElementById('ld').remove();
}, 1200);
</script></body></html>"""

RUN = r"""
const {chromium} = require('playwright');
(async () => {
  const [code, page] = process.argv.slice(1), out = {alerts: [], posts: [], outside: []};
  const b = await chromium.launch();
  const p = await b.newPage();
  p.on('dialog', async d => { out.alerts.push(d.message()); await d.accept(); });
  await p.route('**/*', async r => {
    const u = r.request().url();
    if (u.startsWith('https://dash.example/')) {
      out.posts.push({url: u, body: r.request().postData() || '', cookie: r.request().headers()['cookie'] || ''});
      return r.fulfill({status: 200, contentType: 'application/json', headers: {'Access-Control-Allow-Origin': 'https://m.a-bly.com'},
        body: JSON.stringify({ok: true, label: '상품 2 · 코디 1 · 코디'})});
    }
    if (u === 'https://m.a-bly.com/ranking') return r.fulfill({status: 200, contentType: 'text/html; charset=utf-8', body: page});
    if (u === 'https://example.com/') return r.fulfill({status: 200, contentType: 'text/html', body: '<p>다른 곳</p>'});
    out.outside.push(u);
    return r.fulfill({status: 204, body: ''});
  });
  await p.goto('https://m.a-bly.com/ranking');
  const t = Date.now();
  await p.evaluate(code);
  out.waited = Date.now() - t;
  await p.goto('https://example.com/');
  await p.evaluate(code);
  console.log(JSON.stringify(out));
  await b.close();
})().catch(e => { console.error(String(e)); process.exit(2); });
"""


def _playwright_env() -> dict[str, str] | None:
    if NODE is None:
        return None
    env = dict(os.environ)
    roots = [str(Path(NODE).resolve().parents[1] / "lib" / "node_modules")]
    if npm := shutil.which("npm"):
        try:
            roots.append(subprocess.run([npm, "root", "-g"], capture_output=True, text=True, timeout=20).stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    env["NODE_PATH"] = os.pathsep.join(r for r in [env.get("NODE_PATH", ""), *roots] if r)
    ok = subprocess.run([NODE, "-e", "const {chromium}=require('playwright');chromium.launch().then(b=>b.close()).then(()=>process.exit(0),()=>process.exit(3))"],
                        capture_output=True, env=env, timeout=60)
    return env if ok.returncode == 0 else None


def test_bookmarklet_on_fake_ably_ranking_collects_cards_and_server_saves() -> None:
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    db = FakeDb()
    res = subprocess.run([NODE, "-e", RUN, code(db), PAGE], capture_output=True, text=True, timeout=120, env=env)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    assert got["alerts"] == ["Ably 랭킹 보냄 ✓ (상품 2 · 코디 1 · 코디)", "Ably 랭킹 보내기: 에이블리 랭킹 화면(a-bly.com)에서 눌러 주세요"]
    assert all(u.startswith("https://img.cdn.example/") for u in got["outside"])  # 사진은 화면(브라우저)이 받음 — 책갈피는 대시보드로만 보냄
    assert 900 <= got["waited"] < 6000  # 카드가 늦게 떠서 0.5초씩 기다림
    assert len(got["posts"]) == 1 and got["posts"][0]["url"] == "https://dash.example/dashboard/api/ably_rank_capture"
    assert got["posts"][0]["cookie"] == ""
    sent = dict(parse_qsl(got["posts"][0]["body"], keep_blank_values=True))
    assert sent["k"] == ablyrank.key(db) and sent["u"] == "https://m.a-bly.com/ranking" and sent["t"] == "코디 랭킹 | 에이블리"
    assert sent["s"].splitlines()[0] == "코디" and "랭킹" in sent["h"].splitlines()
    cards = json.loads(sent["j"])
    it = {i["url"].rsplit("/", 1)[1]: i for i in cards["items"]}
    assert set(it) == {"201", "202"}  # 숨은 상품 뺌 · 코디 안 상품은 코디 쪽에
    a, b = it["201"], it["202"]
    assert (a["rank"], a["shop"], a["name"], a["price"], a["orig"], a["disc"], a["star"], a["rev"]) == \
        (1, "무드온", "울 블렌드 롱 코트", 64350, 99000, 35, 4.8, "1,234")
    assert a["img"] == "https://img.cdn.example/g/201.jpg" and a["url"] == "https://m.a-bly.com/goods/201"
    assert (b["rank"], b["shop"], b["name"], b["price"], b["like"]) == (2, "데일리룸", "케이블 가디건 2026 신상", 29900, "532")
    assert "orig" not in b and "disc" not in b
    (lk,) = cards["looks"]
    assert (lk["rank"], lk["title"], lk["like"], lk["url"], lk["img"]) == \
        (1, "데이트룩 니트 코디", "1.2만", "https://m.a-bly.com/coordi/7001", "https://img.cdn.example/look/7001.jpg")
    assert [(x["shop"], x["name"], x["price"]) for x in lk["items"]] == [("러브마켓", "앙고라 브이넥 니트", 31200), ("러브마켓", "A라인 미니 스커트", 25900)]
    assert lk["items"][0]["disc"] == 20
    out = ablyrank.save(db, sent, now=NOW, tz=TZ)
    assert out["ok"] and out["label"] == "상품 2 · 코디 1 · 코디" and out["key"].startswith("ably_rank_capture:2026-11-12:")
    (rec,) = recs(db)
    assert rec["tab"] == "코디" and rec["items"][0]["pos"] == 1 and rec["looks"][0]["items"][1]["name"] == "A라인 미니 스커트"
    assert "울 블렌드 롱 코트" in rec["text"]


# ---------------------------------------------------------------- 3) 받기
def form(db: FakeDb, **kw: str) -> dict[str, str]:
    cards = {"items": [{"rank": 1, "name": "코트", "shop": "무드온", "price": 64350, "url": "https://m.a-bly.com/goods/1",
                        "img": "https://img.cdn.example/1.jpg"}], "looks": []}
    base = {"k": ablyrank.key(db), "u": "https://m.a-bly.com/ranking", "t": "랭킹", "s": "상품", "h": "", "j": json.dumps(cards), "x": "1\n코트"}
    return {**base, **kw}


def test_save_checks_key_where_and_shapes() -> None:
    db = FakeDb()
    for bad, code_ in ((form(db, k="nope"), "key"), (form(db, k=""), "key"), (form(db, u="https://evil.example/a-bly.com"), "where"),
                       (form(db, u="https://a-bly.com.evil.example/"), "where"), (form(db, u="http://m.a-bly.com/"), "where"),
                       (form(db, j="", x=""), "empty")):
        with pytest.raises(statscap.CaptureError) as e:
            ablyrank.save(db, bad, now=NOW, tz=TZ)
        assert e.value.code == code_
    assert not recs(db)
    weird = {"items": [{"name": "<script>alert(1)</script>니트 010-1234-5678", "url": "https://evil.example/goods/1", "img": "javascript:alert(1)",
                        "price": "1000", "rank": 5000, "star": 9, "rev": "<b>", "orig": 500},
                       {"name": "가디건", "url": "https://a-bly.com/goods/2", "price": 29900, "orig": 39900, "disc": 25, "star": 4.66}, "x", None],
             "looks": [{"title": "코디", "items": [{"name": f"상품{i}", "price": 1000} for i in range(30)]}]}
    ablyrank.save(db, form(db, j=json.dumps(weird)), now=NOW, tz=TZ)
    (rec,) = recs(db)
    w, g = rec["items"]
    assert "<" not in w["name"] and "010-1234-5678" not in w["name"] and "url" not in w and "img" not in w
    assert "price" not in w and "rank" not in w and "star" not in w and "rev" not in w and "orig" not in w
    assert (g["url"], g["price"], g["orig"], g["disc"], g["star"]) == ("https://a-bly.com/goods/2", 29900, 39900, 25, 4.7)
    assert len(rec["looks"][0]["items"]) == ablyrank.LOOK_ITEMS_MAX


def test_save_caps_items_and_record_size() -> None:
    db = FakeDb()
    many = {"items": [{"name": "가" * 200, "shop": "나" * 80, "price": 10000 + i, "url": f"https://m.a-bly.com/goods/{i}",
                       "img": f"https://img.cdn.example/{'x' * 300}/{i}.jpg"} for i in range(150)], "looks": []}
    out = ablyrank.save(db, form(db, j=json.dumps(many, ensure_ascii=False), x="다" * 20000), now=NOW, tz=TZ)
    raw = next(v for k, v in db.kv.items() if k.startswith(ablyrank.PREFIX))
    assert len(raw.encode()) <= ablyrank.REC_MAX and 0 < out["items"] < ablyrank.ITEMS_MAX
    assert out["items"] == len(json.loads(raw)["items"])


def test_keeps_30_days_and_caps_a_day_and_new_secret_drops_old_key() -> None:
    db = FakeDb()
    db.kv[ablyrank.PREFIX + "2026-10-01:120000-aaaa"] = "{}"
    db.kv[ablyrank.PREFIX + "2026-10-20:120000-aaaa"] = "{}"
    ablyrank.save(db, form(db), now=NOW, tz=TZ)
    assert ablyrank.PREFIX + "2026-10-01:120000-aaaa" not in db.kv and ablyrank.PREFIX + "2026-10-20:120000-aaaa" in db.kv
    assert ablyrank.today_count(db, NOW, TZ) == 1
    for i in range(ablyrank.DAY_MAX - 1):
        ablyrank.save(db, form(db), now=NOW + i, tz=TZ)
    with pytest.raises(statscap.CaptureError) as e:
        ablyrank.save(db, form(db), now=NOW + 100, tz=TZ)
    assert e.value.status == 429
    k1 = ablyrank.key(db)
    del db.kv[ablyrank.KV_SECRET]
    assert not ablyrank.key_ok(db, k1)


# ---------------------------------------------------------------- 4) 대시보드
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
    return await bot.web.dispatch("POST", ablyrank.PATH, h, raw)


def _good(bot: DealBot) -> dict[str, str]:
    return {"k": ablyrank.key(bot.db), "u": "https://m.a-bly.com/ranking", "t": "랭킹", "s": "코디",
            "j": json.dumps({"items": [{"rank": 1, "name": "코트", "price": 64350, "url": "https://m.a-bly.com/goods/1"}], "looks": []}), "x": ""}


async def test_api_code_needs_login_and_capture_takes_key_only(bot: DealBot) -> None:
    anon = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.7"}
    assert (await bot.web.dispatch("GET", "/dashboard/api/ablyrank", anon))[0] == 401
    status, v, _ = await get_json(bot, "/dashboard/api/ablyrank", session_headers(bot))
    assert status == 200 and v["steps"] == list(ablyrank.INSTALL_STEPS) and ablyrank.key(bot.db) in unquote(v["code"])
    good = _good(bot)
    res = await _post(bot, good, origin="https://m.a-bly.com")
    assert res[0] == 200 and json.loads(res[1]) == {"ok": True, "label": "상품 1 · 코디", "handed": False}  # 예전 Blog-Auto — kv 에만
    assert res[3]["Access-Control-Allow-Origin"] == "https://m.a-bly.com"
    res = await _post(bot, {**good, "k": statscap.key(bot.db)}, origin="https://a-bly.com")
    assert res[0] == 403 and json.loads(res[1])["code"] == "key" and res[3]["Access-Control-Allow-Origin"] == "https://a-bly.com"
    res = await _post(bot, good, origin="https://evil.example")
    assert res[0] == 200 and "Access-Control-Allow-Origin" not in res[3]
    res = await _post(bot, {**good, "via": "form"}, origin="null")
    assert res[0] == 200 and res[2].startswith("text/html") and "Ably 랭킹 보냄 ✓ (상품 1 · 코디)" in res[1]
    assert (await bot.web.dispatch("GET", ablyrank.PATH, anon))[0] == 405
    assert len(bot.db.kv_items(ablyrank.PREFIX)) == 3
    assert bot.actions_out.store.open_rows() == []


async def test_hands_off_when_blogauto_lists_the_action(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from dealbot.bridge.status import BlogAutoStatus

    monkeypatch.setattr(bot.actions_out, "status", lambda: BlogAutoStatus(actions=["ably_rank", ablyrank.ACTION]))
    res = await _post(bot, _good(bot), origin="https://m.a-bly.com")
    assert res[0] == 200 and json.loads(res[1])["handed"] is True
    (row,) = bot.actions_out.store.open_rows()
    (k, _v), = bot.db.kv_items(ablyrank.PREFIX)
    spooled = [json.loads(p.read_text(encoding="utf-8")) for p in (bot.actions_out.root() / "new").glob("*.json")]
    assert [(s["action"], s["blog"], s["args"], s["target"]) for s in spooled] == \
        [(ablyrank.ACTION, None, {"captures": [k]}, "ably-rank:" + k[len(ablyrank.PREFIX):])]
    fx = fixture("action_ably_rank_capture.json")
    assert fx["action"] == spooled[0]["action"] and set(fx["args"]) == set(spooled[0]["args"]) and fx["origin"]["session"] == "bookmarklet"
    assert fx["args"]["captures"][0].startswith(ablyrank.PREFIX) and row["action"] == "blog." + ablyrank.ACTION

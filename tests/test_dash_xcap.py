"""X 커뮤 캡처 글 (주인 11/17 — dashboard/xcap.py · static/xcap.js). 바깥 요청 없음 — 사이트·크롬·AI 는 가짜.

1) 목록·글 읽기 (보배드림·뽐뿌·디시 모양) · 규칙에 걸린 글(정치·혐오·비방·사생활·광고) 빼기 · 반응 점수 순
2) robots.txt 가 막으면 안 씀 · 403·429·사람 확인이면 그 출처 7일 쉼 (다시 부르지 않음)
3) 카드: 본문 캡처 1~2장(출처 띠) + 한 줄 (xstudio 길 · AI 가 빼면 버림) · 오늘 X 체크리스트에 한 줄 · 7일 안 같은 글 안 씀
4) 정한 시각에 하나씩 · 하루 per_day · 그림 경로는 이름 규칙만 · 화면 파일은 안전
"""

from __future__ import annotations

import io
import json
import re
import subprocess
from typing import Any

import pytest
from PIL import Image

from dealbot.config import XCapConfig, XTodayConfig
from dealbot.dashboard import shell
from dealbot.dashboard import xcap as xc
from dealbot.dashboard import xstudio as xs
from dealbot.dashboard import xtoday as xt
from test_dash_xtoday import NODE, STATIC, FakeDb, FakeNews, FakeRouter, at, needs_node

NOW = at("11:41", "2026-11-17")
BB_LIST = """<table><tbody>
<tr><td class="category">유머게시판</td><td><a class="bsubject" href="/view?code=best&No=1001&vdate=">친구랑 동업하면 안 되는 이유</a>
<span class="Comment">(<strong class="totreply">120</strong>)</span></td><td class="author02"><span class="author">닉네임1</span></td>
<td class="recomm"><font>88</font></td><td class="count">20,120</td></tr>
<tr><td><a class="bsubject" href="/view?code=best&No=1002&vdate=">국회 의원 또 막말</a><strong class="totreply">300</strong></td><td class="recomm">99</td><td class="count">9</td></tr>
<tr><td><a class="bsubject" href="/view?code=best&No=1003&vdate=">댓글 하나에 많은 걸 생각함</a><strong class="totreply">40</strong></td><td class="recomm">30</td><td class="count">900</td></tr>
<tr><td><a class="bsubject" href="/view?code=best&No=1004&vdate=">반응 적은 글</a><strong class="totreply">1</strong></td><td class="recomm">2</td><td class="count">10</td></tr>
<tr><td><a class="bsubject" href="https://evil.example/view?code=best&No=1">밖 주소</a><strong class="totreply">999</strong></td><td class="recomm">999</td></tr>
</tbody></table>"""
STORY = "고등학교 친구랑 가게를 같이 차렸는데 처음엔 좋았음. 돈 얘기 나오니까 서로 말이 달라지더라. 결국 1년 만에 접고 연락도 끊김. 연락처 010-1234-5678 로 연락 주던 친구였는데."


def view_html(title: str, body: str) -> str:
    return (f'<dl><dt class="subject_len"><strong>{title}</strong></dt><dd class="profileImg"><img alt="닉네임1"></dd></dl>'
            f'<div class="bodyCont"><p>{body}</p><script>x()</script></div><div class="commentArea">닉네임2: 댓글</div>')


class Get:
    def __init__(self, pages: dict[str, tuple[int, str]] | None = None, robots: tuple[int, str] = (200, "User-agent: *\nAllow: /\n")) -> None:
        self.pages = pages or {}
        self.robots = robots
        self.calls: list[str] = []

    async def __call__(self, url: str) -> tuple[int, str]:
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return self.robots
        if url == xc.SOURCES["bobaedream"].list_url:
            return self.pages.get("list", (200, BB_LIST))
        m = re.search(r"No=(\d+)", url)
        return self.pages.get(m.group(1) if m else url, (200, view_html("친구랑 동업하면 안 되는 이유", STORY)))


class Ask:
    def __init__(self, out: dict[str, Any] | None = None) -> None:
        self.out = out or {"ok": True, "why": "", "line": "친구랑 동업을 하면 안 되는 이유", "angle": "누구나 겪을 법한 돈 얘기"}
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((system, user))
        return self.out


def png(h: int = 2400) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (760, h), (250, 250, 250)).save(buf, "PNG")
    return buf.getvalue()


async def nosleep(_s: float) -> None:
    return None


def desk(tmp_path: Any, *, get: Get | None = None, ask: Ask | None = None, shot: bytes | None = None, **cfg: Any) -> tuple[xc.XCap, Get, Ask, list[float], xt.XToday, list[str]]:
    now = [NOW]
    db, get, ask = FakeDb(), get or Get(), ask or Ask()
    studio = xs.Studio(db, ask=ask, clock=lambda: now[0])
    today = xt.XToday(db, studio, cfg=lambda: XTodayConfig(goal_posts=False), router=FakeRouter, news=FakeNews(None), clock=lambda: now[0])
    conf = XCapConfig(**{"sources": ["bobaedream"], **cfg})
    shots: list[str] = []

    async def shoot(url: str, src: xc.Source) -> bytes | None:
        shots.append(url)
        return shot

    d = xc.XCap(db, studio, today, cfg=lambda: conf, get=get, shoot=shoot, img_dir=tmp_path / "xcap", ua="dealbot/1.0", clock=lambda: now[0], sleep=nosleep)
    return d, get, ask, now, today, shots


# ---------------------------------------------------------------- 1) 읽기
def test_list_and_view_are_read_with_the_site_shape() -> None:
    rows = xc.list_rows(xc.SOURCES["bobaedream"], BB_LIST)
    assert [r["title"] for r in rows] == ["친구랑 동업하면 안 되는 이유", "국회 의원 또 막말", "댓글 하나에 많은 걸 생각함", "반응 적은 글"]  # 밖 주소는 버림
    assert rows[0] == {"src": "bobaedream", "title": "친구랑 동업하면 안 되는 이유", "url": "https://www.bobaedream.co.kr/view?code=best&No=1001&vdate=",
                       "comments": 120, "rec": 88, "views": 20120}
    title, body = xc.view_text(xc.SOURCES["bobaedream"], view_html("제목", STORY))
    assert title == "제목" and body.startswith("고등학교 친구랑") and "x()" not in body and "닉네임" not in body  # 본문 칸만
    pp = """<table><tr class="baseList"><td><a class="baseList-title" href="view.php?id=freeboard&page=1&no=9001">회사 썰</a>
<span class="baseList-c">55</span></td><td class="baseList-rec">31 - 2</td><td class="baseList-views">4,000</td></tr></table>"""
    got = xc.list_rows(xc.SOURCES["ppomppu"], pp)
    assert got == [{"src": "ppomppu", "title": "회사 썰", "url": "https://www.ppomppu.co.kr/zboard/view.php?id=freeboard&page=1&no=9001", "comments": 55, "rec": 31, "views": 4000}]
    assert xc.bad_why("국회 의원 또 막말") == "정치" and xc.bad_why("직장 동료 신상 박제함") == "비방·사생활·광고" and xc.bad_why(STORY) == ""


def test_capture_gets_a_source_bar_and_splits_into_two_at_most() -> None:
    out = xc.compose(png(2400), "보배드림")
    assert len(out) == 2
    a, b = (Image.open(io.BytesIO(x)) for x in out)
    assert a.width == xc.IMG_W and a.height == 56 + 8 + xc.IMG_MAX_H + 8 and b.height < a.height  # 900/760 배로 늘린 2842 → 1500 + 1342
    assert xc.compose(png(9000), "보배드림").__len__() == 2  # 두 장 넘는 것은 버림
    assert xc.compose(png(50), "x") == []
    assert Image.open(io.BytesIO(xc.text_card("제목", STORY, "보배드림"))).width == xc.IMG_W


# ---------------------------------------------------------------- 2) 막히면 쉼
async def test_robots_disallow_or_block_rests_the_source_without_retry(tmp_path: Any) -> None:
    d, get, ask, _n, _t, _s = desk(tmp_path, get=Get(robots=(200, "User-agent: *\nDisallow: /\n")))
    assert await d.make_one() is None and get.calls == ["https://www.bobaedream.co.kr/robots.txt"] and ask.calls == []
    d, get, ask, now, _t, _s = desk(tmp_path, get=Get({"list": (403, "")}))
    assert await d.make_one() is None and ask.calls == []
    assert d.src_state(now[0])["off"]["bobaedream"] == pytest.approx(NOW + xc.OFF_S) and d.view()["off"] == ["보배드림"]
    n = len(get.calls)
    assert await d.make_one() is None and len(get.calls) == n  # 7일 동안 다시 부르지 않음
    d, get, _a, now, _t, _s = desk(tmp_path, get=Get({"list": (200, "<html>Just a moment... cf-chl</html>")}))
    await d.make_one()
    assert d.src_state(now[0])["off"]["bobaedream"] == pytest.approx(NOW + xc.OFF_S)  # 사람 확인 화면 = 막힘 (우회 안 함)


# ---------------------------------------------------------------- 3) 카드
async def test_card_has_captures_one_line_and_a_checklist_row(tmp_path: Any) -> None:
    d, get, ask, now, today, shots = desk(tmp_path, shot=png(2400))
    item = await d.make_one()
    assert item is not None and item["title"] == "친구랑 동업하면 안 되는 이유" and item["line"] == "친구랑 동업을 하면 안 되는 이유" and item["n"] == 2
    assert shots == [item["url"]] and "No=1002" not in " ".join(get.calls)  # 정치 글은 열지도 않음
    system, user = ask.calls[0]
    assert "ok=false" in system and "[사이트] 보배드림" in user and "댓글 120" in user and "고등학교 친구랑" in user
    v = d.view()
    assert v["made"] == 1 and v["items"][0]["imgs"] == [f"/dashboard/api/xcap/img/{item['id']}-1.jpg", f"/dashboard/api/xcap/img/{item['id']}-2.jpg"]
    assert d.img(f"{item['id']}-1.jpg")[:2] == b"\xff\xd8" and d.img("../x.jpg") is None and d.img(f"{item['id']}-3.jpg") is None
    row = next(i for i in today.plan(now[0])["items"] if i["id"] == xt.item_id(f"xcap:{item['id']}"))
    assert row["kind"] == "mine" and row["keyword"] == "커뮤 캡처 · 보배드림" and row["res"]["drafts"][0]["text"] == item["line"]
    assert d.studio._left("draft", xs.DRAFT_PER_DAY) == xs.DRAFT_PER_DAY - 1
    # 같은 글은 7일 안 다시 안 씀 → 다음 후보
    item2 = await d.make_one()
    assert item2 is not None and "No=1003" in item2["url"]
    # 안 써요
    status, v = d.skip({"id": item["id"]})
    assert status == 200 and [i["id"] for i in v["items"]] == [item2["id"]]


async def test_ai_veto_and_no_browser_fall_back_safely(tmp_path: Any) -> None:
    d, _g, ask, _n, _t, _s = desk(tmp_path, ask=Ask({"ok": False, "why": "특정인 비방", "line": "", "angle": ""}))
    assert await d.make_one() is None and len(ask.calls) == 2 and d.view()["items"] == []  # 둘 다 AI 가 뺌 — 카드 없음
    d, _g, _a, _n, _t, _s = desk(tmp_path, ask=Ask({"ok": True, "why": "", "line": "@누구 이거 봐 https://a.example", "angle": ""}))
    assert await d.make_one() is None
    d, _g, _a, _n, _t, _s = desk(tmp_path, shot=None)  # 크롬이 없으면 글자 그림 한 장
    item = await d.make_one()
    assert item is not None and item["n"] == 1
    d, get, _a, _n, _t, _s = desk(tmp_path, get=Get({"1001": (200, view_html("t", "짧음")), "1003": (200, view_html("t", "광고 협찬 받은 제품 " * 10))}))
    assert await d.make_one() is None  # 본문 없는 글·광고 글은 안 씀


# ---------------------------------------------------------------- 4) 시각·몫
async def test_tick_makes_one_per_time_up_to_per_day(tmp_path: Any) -> None:
    d, _g, _a, now, _t, _s = desk(tmp_path, per_day=2, times=["11:40", "16:40", "20:40"])
    now[0] = at("11:30", "2026-11-17")
    assert d.due(now[0]) == "" and d.tick() is False
    now[0] = at("11:41", "2026-11-17")
    assert d.tick() is True and d.tick() is False  # 도는 중
    await d.bg
    assert d.view()["made"] == 1 and d.due(now[0]) == ""  # 11:40 은 했음
    now[0] = at("21:00", "2026-11-17")
    assert d.due(now[0]) == "16:40"
    assert d.tick() is True
    await d.bg
    assert d.view()["made"] == 2 and d.due(now[0]) == "" and d.view()["next"] == ""  # 하루 per_day
    assert d.make()[1]["started"] is True
    await d.bg
    with pytest.raises(xs.StudioError) as e:
        d.make()  # 10분에 한 번
    assert e.value.code == "rate"


def test_xcap_js_is_local_safe_and_loaded_by_shell() -> None:
    js = (STATIC / "xcap.js").read_text(encoding="utf-8")
    if NODE:
        assert subprocess.run([NODE, "--check", str(STATIC / "xcap.js")], capture_output=True).returncode == 0
    code = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    assert "innerHTML" not in code and "eval(" not in code and ".click()" not in code and re.findall(r"https?://[^\s'\"`)]+", code) == []
    for label in ("커뮤 캡처 글", "한 줄 복사", "안 써요", "하나 더 만들기", "저장"):
        assert label in code, label
    page = shell.page({})
    assert f'<script defer src="{shell.asset_url("xcap.js")}"></script>' in page
    assert len((STATIC / "app.js").read_bytes()) <= 96000


@needs_node
async def test_xcap_js_card_sits_below_the_trend_card(tmp_path: Any) -> None:
    from test_dash_xtrend import SHIM

    d, _g, _a, _n, _t, _s = desk(tmp_path, shot=png(1000))
    await d.make_one()
    tail = r"""
global.fetch = async () => ({status: 200, json: async () => state});
const tick = async (n = 8) => { for (let i = 0; i < n; i++) await new Promise(r => setImmediate(r)); };
const text = n => n.textContent;
const all = (n, f, out = []) => { if (f(n)) out.push(n); n.children.forEach(c => all(c, f, out)); return out; };
(async () => {
  const xrc = mk('article'); xrc.attrs.id = 'xrc'; xtc.after(xrc);
  eval(fs.readFileSync(src, 'utf8'));
  await tick();
  const c = byId(main, 'xcc');
  console.log(JSON.stringify({order: main.children.map(x => x.attrs.id || x.tagName), text: text(c),
    imgs: all(c, n => n.tagName === 'IMG').map(n => n.attrs.src), saves: all(c, n => n.tagName === 'A' && 'download' in n.attrs).map(n => n.attrs.href)}));
})().catch(e => { console.error(e); process.exit(1); });
"""
    shim = SHIM.split("const clip = [];")[0] + tail
    # 주인 11/18 — '어그로 후보'로 통일: 카드는 설정 dashboard.x_cap.show_card 가 켜졌을 때만 (기본 숨김 · 캡처 길·API 는 그대로)
    assert d.view()["show"] is False and XCapConfig().show_card is False
    hidden = subprocess.run([NODE, "-e", shim.replace("const c = byId(main, 'xcc');", "const c = byId(main, 'xcc') || main;"), str(STATIC / "xcap.js"), "x",
                             json.dumps(d.view(), ensure_ascii=False), "#/sns/x"], capture_output=True, text=True, timeout=30)
    assert hidden.returncode == 0, hidden.stderr
    assert json.loads(hidden.stdout)["order"] == ["snt", "xsc", "xtc", "xrc", "DIV"]
    res = subprocess.run([NODE, "-e", shim, str(STATIC / "xcap.js"), "x", json.dumps({**d.view(), "show": True}, ensure_ascii=False), "#/sns/x"],
                         capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["order"] == ["snt", "xsc", "xtc", "xrc", "xcc", "DIV"]
    assert "커뮤 캡처 글 · 오늘 1/3" in out["text"] and "친구랑 동업을 하면 안 되는 이유" in out["text"] and "체크리스트에서" in out["text"]
    assert out["imgs"] == out["saves"] and out["imgs"][0].startswith("/dashboard/api/xcap/img/c")

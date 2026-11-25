"""'하루치 한 번에' v8 · 꾸미기 v12 (주인 11/25 — "책갈피로 올리면 본문은 가운데인데 제목은 왼쪽").

가운데 글(데런픽·트로트)은 제목을 넣은 뒤 꾸미기 titleMid 가 제목 칸을 누르고 도구줄 가운데를 3번까지 (안 먹으면 다시 누름).
그래도 왼쪽이면 본문을 넣기 전에 주인에게 "제목 칸을 누른 채 도구줄 '가운데'" 를 부탁하고 멈춤 → 그래도 왼쪽(주인이 '보류' 단추)이면
그 글은 발행하지 않고 보류(skip — 대시보드는 오늘 다시 주지 않음) · 다음 글로. 같은 판에서 2편 연속 보류면 나머지는 메모만 남기고 발행.
끝 요약에 '제목 가운데 안 됨 n편 — 알려 주세요'. 왼쪽 글(라커룸)은 정렬을 건드리지 않음. 스크립트는 누르기만 (에디터 글을 지우거나 고치지 않음).
가짜 글쓰기 화면은 test_dash_autopub 그대로 + TITLE_MID (커서가 제목이면 가운데 단추가 제목 줄을 가운데로).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from urllib.parse import unquote

import pytest

from dealbot.app import DealBot
from dealbot.dashboard import autopub, kkumi, routine
from test_dash_autopub import DAY, FLOW, NODE, OK, TZ, Rng, _editor, _loader, _paste, _tags_line, bot, _no_live_threads, needs_node  # noqa: F401 — 고정 장치
from test_dash_kkumi import _playwright_env
from test_dash_routine import SLOTS, at, item, with_routine
from test_dash_statscap import FakeDb

STATIC = Path(autopub.__file__).resolve().parent / "static"

# 주인 손: "도구줄 '가운데'를 눌러" 가 뜨면 midfix 면 제목 칸을 누르고(진짜 누름) 가운데 단추 · 아니면 '보류' 단추 · skip 은 닫힌 글
FLOW8 = FLOW.replace("closed = r => ['ok', 'unsure', 'wall', 'fail'].includes(r)", "closed = r => ['ok', 'unsure', 'wall', 'fail', 'skip'].includes(r)").replace(
    "    out.lines = lines;\n",
    "    if (/도구줄 '가운데'를 눌러/.test(bar)) { if (fresh) { out.mid = (out.mid || 0) + 1; await p.waitForTimeout(300);"
    " if (opt.midfix) { const e = ed(); await e.evaluate(() => { window.__tm = {...(window.__tm || {}), ok: 1}; }); await e.click('.se-documentTitle .se-text-paragraph'); await e.click(\"[data-value='center']\"); }"
    " else await f.click('#apb button'); } continue; }\n"
    "    out.lines = lines;\n")
assert FLOW8.count("도구줄 '가운데'") == 1 and "'fail', 'skip'" in FLOW8


def _run(posts: list[dict], tm: dict | None = None, timeout: int = 300, **opt) -> dict:
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    ed = _editor()
    if tm is not None:
        ed = ed.replace("</body></html>", f"<script>window.__tm = {json.dumps(tm)};</script></body></html>")
    out = subprocess.run([NODE, "-e", FLOW8, ed, _loader(), json.dumps(posts, ensure_ascii=False), json.dumps({"mode": "xhr", **opt})],
                         capture_output=True, text=True, timeout=timeout, env=env)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _post(n: int = 1, center: bool = True) -> dict:
    st = ' style="text-align:center"' if center else ""
    body = "".join(f"<p{st}>{n}번 글 {i}번째 줄이에요 길게.</p>" for i in range(4))
    return {"id": f"pub:issue:{10 + n}", "title": f"{n}번 글 제목이에요", "at": "", "label": "지금", "photos": 0, "disc": False, "text": "",
            "html": f"<p{st}>{n}번 글 첫 문단.</p>" + body + f"<p{st}>{n}번 글 끝 문단.</p>" + _tags_line(["아일릿", "키링"], "셀럽 소식")}


# ---------------------------------------------------------------- 진행기 코드
def test_v8_bookmarklet_fits_with_the_real_dashboard_address() -> None:
    db = FakeDb()
    v = autopub.view(db, "https://52-79-136-64.sslip.io")  # config.yaml dashboard.public_url 길이
    assert v["version"] == autopub.version() == 8 and v["len"] <= autopub.LEN_MAX == 50000, v["len"]
    assert len(autopub.view(db, "https://dash.example")["code"]) <= 50000
    assert kkumi.version() == 12 and len(kkumi.bookmarklet()) < 30500
    js = unquote(v["code"][len("javascript:"):])
    assert "\n" not in js  # v8: '}' 뒤 줄바꿈도 붙임 (주소 %0A)
    assert "let CORE=" in js and "const CORE" not in js  # v8: 'const' → 'let'


def test_v8_title_mid_retries_three_times_and_never_aligns_left() -> None:
    src = kkumi.SRC.read_text(encoding="utf-8")
    tm = src[src.index("const titleMid = async (tries = 3) => {"):src.index("// 링크 카드")]
    assert "n < tries && !centered(t)" in tm and "press(t); setSel(t);" in tm and "await S(400 + 250 * n);" in tm
    assert "return centered(t) ? '' : '못 함';" in tm and "left" not in tm  # 왼쪽으로 바꾸는 길 없음 (10/29)
    for word in ("textContent =", "innerHTML", ".remove(", "execCommand", "deleteContents"):  # 누르기만 — 에디터 글을 고치지 않음
        assert word not in tm, word
    shell = (STATIC / "autopub.js").read_text(encoding="utf-8")
    assert "const tm = MID(it) ? await k.titleMid() : '';" in shell
    pre = shell[shell.index("const tm = MID(it)"):shell.index("// 본문: 꾸미기와 같게")]
    assert "제목 칸을 누른 채 도구줄 '가운데'를 눌러 주세요" in pre and "K.why = '제목 가운데 안 됨'" in pre and "h2 < 2" in pre
    assert "if (MID(it) && !K.centered(ttl())) nt.push('제목 가운데 아님');" in shell  # 메모 줄은 그대로
    assert "' · 제목 가운데 안 됨 ' + tn + '편 — 알려 주세요'" in shell


def test_v8_dashboard_name_for_derenlog_is_trot() -> None:
    assert autopub.NAMES == {"issue": "데런픽", "finance": "트로트", "hotdeal": "라커룸"}
    assert "skip" in autopub.RESULTS and "skip" not in autopub.TRIED
    assert any("제목도 가운데" in s for s in autopub.STEPS)


# ---------------------------------------------------------------- 대시보드: 보류(skip)
def _rows() -> dict:
    return {"day": DAY, "slots": SLOTS, "overflow": 0, "items": [
        item(f"pub:issue:{n}", "s1", group="발행", blog="issue", title=f"데런픽 발행 · {n}", paste=_paste(f"글 {n}"), gate=OK) for n in (1, 2)]}


def _go(bot: DealBot, now: float, **form: str) -> dict:
    return autopub.act(bot.db, {"k": autopub.key(bot.db), "b": "issue", **form}, tz=TZ, now=now, rv=routine.view(bot, now=now), rng=Rng(),
                       pub={"finance": 1, "issue": 1, "hotdeal": 1})


def test_skipped_post_is_not_served_again_today_and_shows_in_the_summary(bot: DealBot, tmp_path: Path) -> None:
    with_routine(tmp_path, _rows())
    t = at(bot, DAY, "10:00")
    q = _go(bot, t, a="next")
    first = q["next"]["id"]
    assert _go(bot, t + 5, a="done", id=first, r="skip", n="제목 가운데 안 됨") == {"ok": True, "checked": False, "text": ""}
    q2 = _go(bot, t + 10, a="next")
    assert q2["next"] and q2["next"]["id"] != first  # 다음 글로
    by = autopub.today(bot.db, t + 20, TZ, routine.view(bot, now=t + 20))
    assert by["by"]["issue"]["skip"] == 1 and "제목 가운데 안 됨 1편 보류 — 알려 주세요" in by["by"]["issue"]["line"], by
    assert "제목 가운데 안 됨 보류 1" in by["note"]
    # 보류한 글은 발행을 눌러 본 글이 아님 — 주인이 손으로 고친 뒤 꾸미기로 올릴 수 있게 '해 봄'(held) 에는 남기지 않음
    assert first not in autopub.held(bot.db, t + 30)


# ---------------------------------------------------------------- 가짜 글쓰기 화면
@needs_node
def test_center_post_title_centers_on_the_first_press_and_publishes() -> None:
    got = _run([_post(1)])
    assert [d["r"] for d in got["done"]] == ["hold", "ok"] and not got["done"][-1]["n"], got["done"]
    assert not got.get("mid") and "제목 가운데" not in got["end"], got


@needs_node
def test_title_mid_tries_three_times() -> None:
    got = _run([_post(1)], tm={"need": 3})  # 세 번째 누름에서야 먹는 화면
    assert [d["r"] for d in got["done"]] == ["hold", "ok"] and not got.get("mid"), (got["done"], got["bars"])
    got = _run([_post(1)], tm={"need": 4}, midfix=1)  # 네 번째여야 먹음 → 3번에서 그만두고 주인에게
    assert got["mid"] == 1 and [d["r"] for d in got["done"]] == ["hold", "ok"], got["done"]


@needs_node
def test_owner_centers_the_title_by_hand_and_it_publishes() -> None:
    got = _run([_post(1)], tm={"never": 1}, midfix=1)
    assert got["mid"] == 1 and [d["r"] for d in got["done"]] == ["hold", "ok"] and len(got["pub"]) == 1, got["done"]
    i = next(k for k, b in enumerate(got["bars"]) if "도구줄 '가운데'" in b)
    assert not any("넣는 중" in b for b in got["bars"][:i])  # 본문 넣기 전에 멈춤
    assert not got["done"][-1]["n"] and "제목 가운데 안 됨" not in got["end"], got


@needs_node
def test_title_still_left_holds_the_post_and_goes_to_the_next() -> None:
    posts = [_post(1), _post(2, center=False)]
    got = _run(posts, tm={"never": 1})
    assert [(d["id"], d["r"]) for d in got["done"]] == [("pub:issue:11", "skip"), ("pub:issue:12", "hold"), ("pub:issue:12", "ok")], got["done"]
    assert got["done"][0]["n"] == "제목 가운데 안 됨" and got["done"][0]["pub"] == 0 and len(got["pub"]) == 1
    assert got["mid"] == 1 and got["lines"][0].startswith("보류 · 「1번 글 제목이에요」 — 제목 가운데 안 됨"), got["lines"]
    assert "제목 가운데 안 됨 1편 — 알려 주세요" in got["end"] and got["end"].endswith(" · v8"), got["end"]


@needs_node
def test_two_holds_in_a_row_then_the_rest_publish_with_a_note() -> None:
    got = _run([_post(1), _post(2), _post(3), _post(4)], tm={"never": 1}, timeout=420)
    rs = [(d["id"][-2:], d["r"]) for d in got["done"]]
    assert rs == [("11", "skip"), ("12", "skip"), ("13", "hold"), ("13", "ok"), ("14", "hold"), ("14", "ok")], rs
    assert got["mid"] == 2  # 셋째부터는 멈추지 않음 (하루 발행 0 막기)
    assert all(d["n"] == "제목 가운데 아님" for d in got["done"] if d["r"] == "ok"), got["done"]
    assert "제목 가운데 안 됨 4편 — 알려 주세요" in got["end"], got["end"]


@needs_node
def test_left_blog_title_is_never_touched() -> None:
    got = _run([_post(1, center=False)], tm={"need": 1})
    assert [d["r"] for d in got["done"]] == ["hold", "ok"] and not got.get("mid") and not got["done"][-1]["n"], got["done"]
    assert "제목 가운데" not in got["end"]
